import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import collector
import db
import descriptions as d
from analytics import filter_repos, load_repos
from test_hub import DatabaseCase, repo, ROOT


class DescriptionTests(DatabaseCase):
    def sync(self, client):
        with contextlib.redirect_stdout(io.StringIO()):
            run = collector.Run(self.con)
            d.sync_descriptions(self.con, run, lambda: client)
        return self.con.execute("SELECT * FROM collection_runs WHERE id=?", (run.id,)).fetchone()

    def test_backfill_cache_source_change_and_chinese_search(self):
        rid = self.insert_repo()
        client = mock.Mock()
        client.translate_many.return_value = {repo()["description"]: "Linux 字面匹配示例项目"}
        self.sync(client)
        row = self.con.execute("SELECT * FROM repos").fetchone()
        self.assertEqual(row["description"], repo()["description"])
        self.assertEqual(row["description_zh"], "Linux 字面匹配示例项目")
        self.assertEqual(len(filter_repos(load_repos(self.con), keyword="字面匹配")), 1)
        client.reset_mock()
        self.sync(client)
        client.translate_many.assert_not_called()
        with self.con:
            collector.upsert_repo(repo(name="new/project"), None, self.con, tracked_id=rid)
        self.assertEqual(self.con.execute("SELECT description_zh FROM repos").fetchone()[0], "Linux 字面匹配示例项目")
        with self.con:
            collector.upsert_repo({**repo(name="new/project"), "description": "Changed introduction"}, None, self.con, tracked_id=rid)
        self.assertIsNone(self.con.execute("SELECT description_zh FROM repos").fetchone()[0])
        client.translate_many.return_value = {"Changed introduction": "已修改的简介"}
        self.sync(client)
        self.assertEqual(self.con.execute("SELECT description_zh FROM repos").fetchone()[0], "已修改的简介")

    def test_missing_and_chinese_descriptions_need_no_network(self):
        with self.con:
            collector.upsert_repo({**repo(), "description": None}, "Systems", self.con)
            collector.upsert_repo({**repo(101, "owner/chinese"), "description": "用于恶意软件分析的开源工具"}, "Systems", self.con)
        client = mock.Mock()
        self.sync(client)
        client.translate_many.assert_not_called()
        self.assertEqual(d.pending_sources(self.con), [])
        self.assertEqual(self.con.execute("SELECT description_zh FROM repos ORDER BY id").fetchone()[0], d.EMPTY_DESCRIPTION)

    def test_failure_is_recorded_and_retried_without_affecting_snapshots(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-11-11T12:00:00Z")
        client = mock.Mock()
        client.translate_many.side_effect = d.TranslationError("中文翻译服务暂不可用")
        run = self.sync(client)
        self.assertEqual(run["translation_errors"], 1)
        self.assertEqual(run["errors"], 1)
        self.assertEqual(len(d.pending_sources(self.con)), 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)
        client.translate_many.side_effect = None
        client.translate_many.return_value = {repo()["description"]: "中文项目简介"}
        self.sync(client)
        self.assertEqual(d.pending_sources(self.con), [])

    def test_description_containing_our_batch_marker_does_not_stop_the_others(self):
        with self.con:
            collector.upsert_repo({**repo(100, "evil/marker"), "description": "Uses the separator ⟦100000⟧ on purpose"}, "Systems", self.con)
            collector.upsert_repo({**repo(101, "ok/plain"), "description": "A perfectly ordinary English description"}, "Systems", self.con)
        client = mock.Mock()
        client.translate_many.side_effect = lambda batch: {source: "译文：" + source for source in batch}
        run = self.sync(client)
        translated = {row[0]: row[1] for row in self.con.execute("SELECT description, description_zh FROM repos")}
        self.assertEqual(translated["A perfectly ordinary English description"], "译文：A perfectly ordinary English description")
        self.assertIn(translated["Uses the separator ⟦100000⟧ on purpose"], (None, ""), "the marked text is skipped, not translated")
        self.assertEqual(run["translation_errors"], 1, "the skipped description is recorded for manual review")
        sent = [text for call in client.translate_many.call_args_list for text in call.args[0]]
        self.assertFalse(any("⟦100000⟧" in text for text in sent), "marked text never reaches the translation service")

    def test_stale_translation_is_not_attached_to_changed_source(self):
        self.insert_repo()
        with self.con:
            self.con.execute("UPDATE repos SET description='new source'")
        d.save_translations(self.con, [repo()["description"]], {repo()["description"]: "旧的译文"})
        self.assertIsNone(self.con.execute("SELECT description_zh FROM repos").fetchone()[0])

    def test_frontend_displays_and_searches_chinese_and_preserves_original(self):
        from streamlit.testing.v1 import AppTest
        self.insert_repo()
        d.save_translations(self.con, [repo()["description"]], {repo()["description"]: "用于安全研究的中文简介"})
        with mock.patch.object(db, "DB_PATH", self.path):
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(at.dataframe[0].value.iloc[0]["中文简介"], "用于安全研究的中文简介")
            self.assertTrue(any(text.value == repo()["description"] for text in at.text))
            at.text_input[0].set_value("安全研究").run()
            self.assertEqual(len(at.dataframe[0].value), 1)

    def test_translate_only_needs_no_github_token_and_creates_no_snapshots(self):
        self.insert_repo()
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": ""}), mock.patch.object(collector, "sync_descriptions"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(collector.run_translation(self.path), 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)


class TranslationClientTests(unittest.TestCase):
    def test_reassembles_segments_and_rejects_missing_or_reordered_markers(self):
        sources = ["A tool", "A framework"]
        payload = [[["⟦100000⟧\n工具。\n", None], ["⟦100001⟧\n框架。", None]]]
        self.assertEqual(d.parse_translation(payload, sources), {"A tool": "工具。", "A framework": "框架。"})
        for text in ("⟦100001⟧框架⟦100000⟧工具", "⟦100000⟧工具", "⟦100000⟧A tool⟦100001⟧框架"):
            with self.assertRaises(d.TranslationError):
                d.parse_translation([[[text]]], sources)
        for payload in ({}, None, [[[]]]):
            with self.assertRaises(d.TranslationError):
                d.parse_translation(payload, sources)

    def test_network_retry_does_not_log_raw_error_or_share_github_credentials(self):
        client = d.ChineseTranslator()
        self.addCleanup(client.close)
        self.assertNotIn("Authorization", client.session.headers)
        with mock.patch.object(client.session, "get", side_effect=d.requests.Timeout("fake-secret")) as get, mock.patch.object(d.time, "sleep"):
            with self.assertRaises(d.TranslationError) as caught:
                client.translate_many(["A tool"])
        self.assertEqual(get.call_count, 3)
        self.assertNotIn("fake-secret", str(caught.exception))

    def test_mixed_language_is_translated_and_batches_are_bounded(self):
        self.assertIsNone(d.local_description("中文说明 An English paragraph that needs to be translated"))
        sources = [f"Source description number {index}" for index in range(45)]
        batches = list(d.make_batches(sources + sources))
        self.assertEqual([source for batch in batches for source in batch], sources)
        self.assertTrue(all(len(batch) <= 20 for batch in batches))

    def test_non_ascii_languages_are_isolated_from_english_batches(self):
        sources = ["An English project", "中文说明 English text", "Another English project", "한국어 설명"]
        self.assertEqual(list(d.make_batches(sources)), [[source] for source in sources])

    def test_bilingual_source_translates_english_portion(self):
        client = d.ChineseTranslator()
        self.addCleanup(client.close)
        response = mock.Mock(status_code=200)
        response.json.return_value = [[["⟦100000⟧\n中文说明与英文内容"]]]
        with mock.patch.object(client.session, "get", return_value=response) as get:
            self.assertEqual(client.translate_many(["中文说明 English text"]), {"中文说明 English text": "中文说明与英文内容"})
        self.assertEqual(get.call_args.kwargs["params"]["sl"], "en")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])


class V2MigrationTests(unittest.TestCase):
    def test_v1_upgrade_backs_up_and_keeps_all_existing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "v1.db"
            db.init_db(path)
            with sqlite3.connect(path) as con:
                con.execute("INSERT INTO repos(id,full_name,description) VALUES(5,'owner/project','Original text')")
                con.execute("ALTER TABLE repos DROP COLUMN description_zh")
                con.execute("ALTER TABLE repos DROP COLUMN description_zh_source")
                con.execute("ALTER TABLE collection_runs DROP COLUMN translation_errors")
                con.execute("PRAGMA user_version=1")
            db.init_db(path)
            db.init_db(path)
            with db.get_connection(path) as con:
                self.assertEqual(tuple(con.execute("SELECT id,description,description_zh FROM repos").fetchone()), (5, "Original text", None))
                self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
            backups = list((path.parent / "backups").glob("*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0]) as con:
                self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
