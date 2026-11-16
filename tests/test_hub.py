import contextlib
import io
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector
import db
from analytics import filter_repos, load_repos

NOW = "2026-11-11T12:00:00+00:00"


def repo(github_id=100, name="owner/project", stars=150):
    return {"id": github_id, "full_name": name, "description": "Linux [literal] project",
            "language": "Python", "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-11-11T00:00:00Z", "topics": ["systems"],
            "stargazers_count": stars, "forks_count": 10, "open_issues_count": 3}


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"intel.db"
        db.init_db(self.path)
        self.con = db.get_connection(self.path)

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def insert_repo(self, item=None):
        with self.con:
            rid, _ = collector.upsert_repo(item or repo(), "Systems", self.con)
        return rid

    def snap(self, rid, stars, captured):
        with self.con:
            self.con.execute("INSERT INTO snapshots(repo_id,stars,forks,open_issues,captured_at) VALUES(?,?,1,0,?)", (rid, stars, captured))


class DataTests(DatabaseCase):
    def test_missing_baseline_is_not_zero(self):
        rid = self.insert_repo()
        self.snap(rid, 9592, NOW)
        row = load_repos(self.con, NOW).iloc[0]
        self.assertEqual(row.quality, "历史不足")
        self.assertTrue(__import__('pandas').isna(row.growth_7d))
        self.assertTrue(filter_repos(load_repos(self.con, NOW), growth_enabled=True).empty)
        self.assertEqual(len(filter_repos(load_repos(self.con, NOW))), 1)

    def test_weekly_growth_and_real_zero_baseline(self):
        rid = self.insert_repo()
        self.snap(rid, 0, "2026-11-04T12:00:00+00:00")
        self.snap(rid, 50, NOW)
        row = load_repos(self.con, NOW).iloc[0]
        self.assertEqual(row.growth_7d, 50)
        self.assertEqual(row.quality, "可比较")
        self.assertTrue(__import__('pandas').isna(row.growth_rate))

    def test_negative_growth_preserved(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-11-04T12:00:00+00:00")
        self.snap(rid, 95, NOW)
        frame = load_repos(self.con, NOW)
        self.assertEqual(frame.iloc[0].growth_7d, -5)
        self.assertEqual(frame.iloc[0].growth_rate, -5)
        self.assertEqual(frame.growth_7d.mean(), -5)
        self.assertEqual(len(filter_repos(frame, growth_enabled=True, min_growth=-10)), 1)

    def test_window_anchored_to_latest_snapshot(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-11-03T12:00:00+00:00")
        self.snap(rid, 130, "2026-11-04T12:00:00+00:00")
        self.snap(rid, 150, "2026-11-10T12:00:00+00:00")
        row = load_repos(self.con, NOW).iloc[0]
        self.assertEqual(row.growth_7d, 50)
        self.assertEqual(row.span_days, 7)

    def test_stale_and_overlong_intervals_not_in_weekly_stats(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-10-30T12:00:00+00:00")
        self.snap(rid, 150, NOW)
        self.assertEqual(load_repos(self.con, NOW).iloc[0].quality, "基线过旧")
        row = load_repos(self.con, "2026-11-14T12:00:00Z").iloc[0]
        self.assertEqual(row.quality, "数据过期")
        self.assertTrue(__import__('pandas').isna(row.growth_7d))
        self.assertEqual(row.observed_growth, 50)

    def test_future_and_no_snapshot(self):
        rid = self.insert_repo()
        self.assertEqual(load_repos(self.con, NOW).iloc[0].quality, "尚无快照")
        self.snap(rid, 100, "2026-11-12T00:00:00Z")
        self.assertEqual(load_repos(self.con, NOW).iloc[0].quality, "时间异常")

    def test_exact_eight_day_and_freshness_boundaries(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-11-03T12:00:00Z")
        self.snap(rid, 150, NOW)
        self.assertEqual(load_repos(self.con, NOW).iloc[0].quality, "可比较")
        self.assertEqual(load_repos(self.con, "2026-11-13T12:00:00Z").iloc[0].quality, "可比较")
        self.assertEqual(load_repos(self.con, "2026-11-13T12:00:01Z").iloc[0].quality, "数据过期")

    def test_baseline_more_than_eight_days_not_weekly(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-11-03T11:59:59Z")
        self.snap(rid, 150, NOW)
        self.assertEqual(load_repos(self.con, NOW).iloc[0].quality, "基线过旧")

    def test_microsecond_week_boundary_uses_true_earlier_baseline(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-11-04T11:59:59.999900Z")
        self.snap(rid, 125, "2026-11-04T12:00:00.000200Z")
        self.snap(rid, 150, "2026-11-11T12:00:00.000100Z")
        row = load_repos(self.con,"2026-11-11T12:00:01Z").iloc[0]
        self.assertEqual(row.growth_7d,50)
        self.assertGreaterEqual(row.span_days,7)

    def test_latest_snapshot_within_same_millisecond_uses_time_not_id(self):
        rid = self.insert_repo()
        self.snap(rid, 150, "2026-11-11T11:00:00.000300Z")
        self.snap(rid, 100, "2026-11-11T11:00:00.000100Z")
        self.assertEqual(load_repos(self.con,NOW).iloc[0].current_stars,150)

    def test_mixed_legacy_timestamp_sorting(self):
        rid = self.insert_repo()
        self.snap(rid, 100, "2026-11-11 11:00:00")
        self.snap(rid, 90, "2026-11-11T10:00:00+00:00")
        self.assertEqual(load_repos(self.con, NOW).iloc[0].current_stars, 100)

    def test_literal_search_and_combined_filters(self):
        rid = self.insert_repo()
        self.snap(rid, 150, NOW)
        frame = load_repos(self.con, NOW)
        for term in ("[", "(", ".", "\\", "中文"):
            filter_repos(frame, keyword=term)
        self.assertEqual(len(filter_repos(frame, keyword="[literal]", category="Systems", min_stars=100)), 1)
        self.assertTrue(filter_repos(frame, category="Academic").empty)
        self.assertTrue(filter_repos(frame, min_stars=200).empty)
        self.assertTrue(filter_repos(frame, recent=True, as_of="2027-01-11T12:00:00Z").empty)

    def test_rename_preserves_local_id_and_history(self):
        rid = self.insert_repo()
        self.snap(rid, 100, NOW)
        with self.con:
            renamed, added = collector.upsert_repo(repo(name="new-owner/new-name"), None, self.con, tracked_id=rid)
        self.assertEqual(renamed, rid)
        self.assertFalse(added)
        self.assertEqual(self.con.execute("SELECT count(*) FROM repos").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT repo_id FROM snapshots").fetchone()[0], rid)
        self.assertEqual(self.con.execute("SELECT category FROM repos").fetchone()[0], "Systems")

    def test_identity_conflict_does_not_merge_or_overwrite(self):
        rid = self.insert_repo()
        with self.assertRaises(collector.APIError), self.con:
            collector.upsert_repo(repo(github_id=999), None, self.con, tracked_id=rid)
        self.assertEqual(self.con.execute("SELECT github_id FROM repos").fetchone()[0], 100)

    def test_legacy_identity_attached_without_history_loss(self):
        with self.con:
            rid = self.con.execute("INSERT INTO repos(full_name,category) VALUES('owner/project','Systems')").lastrowid
        self.snap(rid, 100, NOW)
        with self.con:
            updated, new = collector.upsert_repo(repo(), "Academic", self.con)
        self.assertEqual(updated, rid)
        self.assertFalse(new)
        self.assertEqual(self.con.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)

    def test_one_snapshot_per_run_even_with_same_timestamp(self):
        rid = self.insert_repo()
        with contextlib.redirect_stdout(io.StringIO()):
            run = collector.Run(self.con)
        with self.con, mock.patch.object(collector, "utc_now", return_value=NOW):
            self.assertTrue(collector.record_snapshot(self.con, rid, run.id, repo()))
            self.assertFalse(collector.record_snapshot(self.con, rid, run.id, repo(stars=200)))
        self.assertEqual(self.con.execute("SELECT stars FROM snapshots").fetchone()[0], 150)

    def test_foreign_key_and_count_time_constraints(self):
        self.assertEqual(self.con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        rid = self.insert_repo()
        for repo_id, stars, captured in [(9999, 1, NOW), (rid, -1, NOW), (rid, 1, "invalid")]:
            with self.assertRaises(sqlite3.IntegrityError), self.con:
                self.con.execute("INSERT INTO snapshots(repo_id,stars,forks,open_issues,captured_at) VALUES(?,?,0,0,?)", (repo_id,stars,captured))
        with self.assertRaises(ValueError):
            collector.validate_repo({**repo(), "forks_count": None}, require_counts=True)

    def test_lock_rejects_second_collector(self):
        with collector.collector_lock(self.path):
            with self.assertRaises(collector.CollectionBusy):
                with collector.collector_lock(self.path):
                    pass

    def test_progress_committed_for_other_readers(self):
        with contextlib.redirect_stdout(io.StringIO()):
            run = collector.Run(self.con)
            run.checkpoint("refresh", 3, 10, "owner/project", "请求中")
        with contextlib.closing(db.get_connection(self.path)) as reader:
            row = reader.execute("SELECT * FROM collection_runs").fetchone()
        self.assertEqual(row["progress_done"], 3)
        self.assertEqual(row["progress_total"], 10)
        self.assertEqual(row["status"], "running")
        self.assertIsNotNone(row["heartbeat_at"])


class MigrationTests(unittest.TestCase):
    def test_migration_preserves_rows_and_backups_before_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"legacy.db"
            with sqlite3.connect(path) as con:
                con.executescript("""
                    CREATE TABLE repos(id INTEGER PRIMARY KEY,full_name TEXT UNIQUE NOT NULL,
                      description TEXT,html_url TEXT,language TEXT,created_at TEXT,updated_at TEXT,
                      topics TEXT,category TEXT,is_archived INTEGER,discovered_at TEXT);
                    CREATE TABLE snapshots(id INTEGER PRIMARY KEY,repo_id INTEGER NOT NULL,
                      stars INTEGER NOT NULL,forks INTEGER NOT NULL,open_issues INTEGER NOT NULL,
                      captured_at TEXT NOT NULL,UNIQUE(repo_id,captured_at),FOREIGN KEY(repo_id) REFERENCES repos(id));
                    CREATE TABLE collection_runs(id INTEGER PRIMARY KEY,started_at TEXT,finished_at TEXT,
                      discovered INTEGER DEFAULT 0,updated INTEGER DEFAULT 0,errors INTEGER DEFAULT 0,message TEXT);
                    INSERT INTO repos(id,full_name) VALUES(42,'legacy/project');
                    INSERT INTO snapshots VALUES(7,42,100,1,2,'2026-11-11T00:00:00+00:00');
                    INSERT INTO collection_runs(id,started_at) VALUES(9,'2026-11-11T00:00:00+00:00');
                """)
            db.init_db(path)
            db.init_db(path)
            with contextlib.closing(db.get_connection(path)) as con:
                self.assertEqual(tuple(con.execute("SELECT id,repo_id,stars FROM snapshots").fetchone()), (7,42,100))
                self.assertEqual(con.execute("SELECT status FROM collection_runs").fetchone()[0], "legacy")
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            backups = list((path.parent/"backups").glob('*.sqlite3'))
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0]) as backup:
                self.assertEqual(backup.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)
                self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_config_reads_env_before_database_and_respects_process_override(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder/"config.py").write_text((ROOT/"config.py").read_text())
            (folder/".env").write_text("DB_PATH=custom/local.db\nGITHUB_TOKEN=fake-secret\n")
            env = dict(os.environ)
            env.pop("DB_PATH", None)
            env.pop("GITHUB_TOKEN", None)
            script = "import config; print(config.DB_PATH)"
            result = subprocess.run([sys.executable,"-B","-c",script],cwd=folder,env=env,capture_output=True,text=True,check=True)
            self.assertEqual(result.stdout.strip(), str(folder/'custom/local.db'))
            env["DB_PATH"] = str(folder/"override.db")
            result = subprocess.run([sys.executable,"-B","-c",script],cwd=folder,env=env,capture_output=True,text=True,check=True)
            self.assertEqual(result.stdout.strip(), str(folder/'override.db'))

    def test_invalid_legacy_data_rolls_back_schema_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bad.db'
            with sqlite3.connect(path) as con:
                con.execute("CREATE TABLE repos(id INTEGER PRIMARY KEY,full_name TEXT UNIQUE NOT NULL)")
                con.execute("CREATE TABLE snapshots(id INTEGER PRIMARY KEY,repo_id INTEGER,stars INTEGER,forks INTEGER,open_issues INTEGER,captured_at TEXT)")
                con.execute("INSERT INTO repos VALUES(1,'owner/project')")
                con.execute("INSERT INTO snapshots VALUES(1,1,-1,0,0,'2026-11-11T12:00:00Z')")
            with self.assertRaises(RuntimeError):
                db.init_db(path)
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],0)
                self.assertNotIn('github_id',[r[1] for r in con.execute('PRAGMA table_info(repos)')])
                self.assertEqual(con.execute('SELECT stars FROM snapshots').fetchone()[0],-1)
            self.assertEqual(len(list((path.parent/'backups').glob('*.sqlite3'))),1)


class FakeResponse:
    def __init__(self, status=200, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = "rate limit" if status in (403,429) else ""
        self.payload = payload if payload is not None else {"ok": True}
    def json(self):
        return self.payload
    def close(self):
        pass


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = collector.GitHubClient("fake-secret")
        self.addCleanup(self.client.close)

    def test_429_and_retry_after(self):
        with mock.patch.object(self.client.session,"get",side_effect=[FakeResponse(429,{"Retry-After":"30"}),FakeResponse()]) as get, mock.patch.object(collector,"cooperative_wait") as wait:
            self.assertEqual(self.client.request_json(collector.BASE+'/test'), {"ok":True})
            self.assertEqual(get.call_count,2)
            self.assertEqual(wait.call_args.args[0],30)

    def test_timeout_and_503_retry(self):
        with mock.patch.object(self.client.session,"get",side_effect=[collector.requests.Timeout('secret-must-not-leak'),FakeResponse(503),FakeResponse()]) as get, mock.patch.object(collector,"cooperative_wait"):
            self.client.request_json(collector.BASE+'/test')
            self.assertEqual(get.call_count,3)

    def test_401_immediately_fatal(self):
        with mock.patch.object(self.client.session,"get",return_value=FakeResponse(401)) as get:
            with self.assertRaises(collector.APIError) as caught:
                self.client.request_json(collector.BASE+'/test')
            self.assertTrue(caught.exception.fatal)
            self.assertNotIn('fake-secret',str(caught.exception))
            self.assertEqual(get.call_count,1)

    def test_retry_exhaustion_is_bounded(self):
        with mock.patch.object(self.client.session,"get",return_value=FakeResponse(503)) as get, mock.patch.object(collector,"cooperative_wait") as wait:
            with self.assertRaises(collector.APIError):
                self.client.request_json(collector.BASE+'/test')
            self.assertEqual(get.call_count,4)
            self.assertEqual(wait.call_count,3)

    def test_secondary_rate_limit_waits_one_minute_without_headers(self):
        with mock.patch.object(self.client.session,'get',side_effect=[FakeResponse(403),FakeResponse()]), mock.patch.object(collector,'cooperative_wait') as wait:
            self.client.request_json(collector.BASE+'/test')
            self.assertEqual(wait.call_args.args[0],60)

    def test_excessive_server_wait_fails_without_early_retry(self):
        with mock.patch.object(self.client.session,'get',return_value=FakeResponse(429,{'Retry-After':'7200'})) as get, mock.patch.object(collector,'cooperative_wait') as wait:
            with self.assertRaises(collector.APIError):
                self.client.request_json(collector.BASE+'/test')
            self.assertEqual(get.call_count,1)
            self.assertEqual(wait.call_count,0)

    def test_external_redirect_never_receives_credentials(self):
        with mock.patch.object(self.client.session,"get",return_value=FakeResponse(301,{"Location":"https://evil.example/repos"})) as get:
            with self.assertRaises(collector.APIError):
                self.client.request_json(collector.BASE+'/repos/old/name')
            self.assertEqual(get.call_count,1)

    def test_github_rename_redirect(self):
        with mock.patch.object(self.client.session,"get",side_effect=[FakeResponse(301,{"Location":collector.BASE+'/repositories/100'}),FakeResponse()]) as get:
            self.client.request_json(collector.BASE+'/repos/old/name')
            self.assertEqual(get.call_count,2)


class CollectionTests(DatabaseCase):
    def run_fake(self, search_error=None, refresh_error=None, incomplete=False):
        class Client:
            def __init__(self, token, run): pass
            def request_json(self, url, params=None):
                if url.endswith('/search/repositories'):
                    if search_error: raise search_error
                    return {"items":[repo(),repo()],"incomplete_results":incomplete}
                if refresh_error: raise refresh_error
                return repo()
            def close(self): pass
        output = io.StringIO()
        with mock.patch.dict(os.environ,{"GITHUB_TOKEN":"fake-secret","PER_PAGE":"100","MAX_PAGES":"1"}), mock.patch.object(collector,"SEARCHES",{"Systems":["one","two"]}), mock.patch.object(collector,"cooperative_wait"), mock.patch.object(collector,"sync_descriptions"), contextlib.redirect_stdout(output):
            result = collector.run_collection(self.path,Client)
        self.assertNotIn('fake-secret',output.getvalue())
        row = self.con.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
        return result,row,output.getvalue()

    def test_success_deduplicates_and_counts_new_not_matched(self):
        result,row,output = self.run_fake()
        self.assertEqual(result,0)
        self.assertEqual(row['status'],'success')
        self.assertEqual(row['discovered'],1)
        self.assertEqual(row['matched'],1)
        self.assertEqual(row['updated'],1)
        self.assertEqual(row['progress_done'],row['progress_total'])
        self.assertIn('100%',output)
        self.assertEqual(self.con.execute('SELECT count(*) FROM snapshots').fetchone()[0],1)
        _,row,_ = self.run_fake()
        self.assertEqual(row['discovered'],0)
        self.assertEqual(row['matched'],1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM snapshots').fetchone()[0],2)

    def test_search_failures_counted_and_refresh_still_runs(self):
        self.insert_repo()
        result,row,_ = self.run_fake(search_error=collector.APIError('模拟搜索失败'))
        self.assertEqual(result,1)
        self.assertEqual(row['status'],'partial')
        self.assertEqual(row['search_errors'],2)
        self.assertEqual(row['errors'],2)
        self.assertEqual(row['updated'],1)

    def test_auth_failure_recorded_once_and_task_finalized(self):
        result,row,_ = self.run_fake(search_error=collector.APIError('认证失败',status=401,fatal=True))
        self.assertEqual(result,1)
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['errors'],1)
        self.assertIsNotNone(row['finished_at'])

    def test_refresh_failure_not_false_success(self):
        result,row,_ = self.run_fake(refresh_error=collector.APIError('GitHub HTTP 404',status=404))
        self.assertEqual(result,1)
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['refresh_errors'],1)
        self.assertEqual(row['updated'],0)

    def test_duplicate_alias_is_reported_instead_of_silently_skipped(self):
        self.insert_repo()
        with self.con:
            self.con.execute("INSERT INTO repos(full_name,category) VALUES('old/alias','Systems')")
        result,row,_ = self.run_fake()
        self.assertEqual(result,1)
        self.assertEqual(row['status'],'partial')
        self.assertEqual(row['refresh_errors'],1)
        self.assertEqual(row['updated'],1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM repos').fetchone()[0],2)

    def test_incomplete_search_is_partial(self):
        _,row,_ = self.run_fake(incomplete=True)
        self.assertEqual(row['status'],'partial')
        self.assertEqual(row['search_errors'],2)

    def test_interrupt_finalized_and_old_unfinished_run_retained(self):
        with self.con:
            self.con.execute("INSERT INTO collection_runs(started_at,status) VALUES(?,'running')",(NOW,))
        result,row,_ = self.run_fake(refresh_error=KeyboardInterrupt())
        self.assertEqual(result,130)
        self.assertEqual(row['status'],'interrupted')
        self.assertIsNotNone(row['finished_at'])
        old = self.con.execute("SELECT * FROM collection_runs ORDER BY id LIMIT 1").fetchone()
        self.assertEqual(old['status'],'interrupted')
        self.assertIsNone(old['finished_at'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM repos').fetchone()[0],1)


class FrontendTests(DatabaseCase):
    def test_real_widgets_literal_filter_empty_and_progress(self):
        from streamlit.testing.v1 import AppTest
        self.insert_repo()
        rid = self.con.execute('SELECT id FROM repos').fetchone()[0]
        self.snap(rid,150,db.utc_now())
        with contextlib.redirect_stdout(io.StringIO()):
            run = collector.Run(self.con)
            run.checkpoint('refresh',1,3,'owner/project','请求中')
        logging.disable(logging.CRITICAL)
        try:
            with mock.patch.object(db,'DB_PATH',self.path):
                at = AppTest.from_file(str(ROOT/'app.py'),default_timeout=30).run()
                self.assertEqual(len(at.exception),0)
                self.assertEqual(len(at.dataframe[0].value),1)
                self.assertIn('1 / 3',at.get('progress')[0].proto.text)
                for term in ('[','(','.','\\','中文'):
                    at.text_input[0].set_value(term).run()
                    self.assertEqual(len(at.exception),0)
                self.assertTrue(any('没有符合条件' in info.value for info in at.info))
                at.text_input[0].set_value('').run()
                at.checkbox[0].check().run()
                self.assertEqual(len(at.exception),0)
                self.assertTrue(any('没有符合条件' in info.value for info in at.info))
        finally:
            logging.disable(logging.NOTSET)

    def test_empty_database_still_shows_collection_records(self):
        from streamlit.testing.v1 import AppTest
        with contextlib.redirect_stdout(io.StringIO()):
            run = collector.Run(self.con)
            run.finish('failed','测试失败任务')
        logging.disable(logging.CRITICAL)
        try:
            with mock.patch.object(db,'DB_PATH',self.path):
                at = AppTest.from_file(str(ROOT/'app.py'),default_timeout=30).run()
                self.assertEqual(len(at.exception),0)
                self.assertTrue(any('数据库尚无项目' in item.value for item in at.info))
                self.assertEqual(len(at.dataframe[0].value),1)
        finally:
            logging.disable(logging.NOTSET)


if __name__ == '__main__':
    unittest.main()
