import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import data_safety as safety


class DataSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.writer = sqlite3.connect(self.source)
        self.writer.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA user_version=2;
            PRAGMA foreign_keys=ON;
            CREATE TABLE parent(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT);
            CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id),
                               n INTEGER, r REAL, payload BLOB, empty TEXT);
            INSERT INTO parent(title) VALUES('private-text-must-not-appear');
            INSERT INTO child VALUES(1,1,9007199254740993,1.25,X'00FF',NULL);
        ''')
        self.writer.commit()

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def backup(self):
        output = self.root / "backup"
        return output, safety.create_backup(self.source, output)

    def test_wal_backup_restore_preserves_every_field_schema_and_sequence(self):
        # WAL remains present and uncheckpointed while the writer stays open.
        self.assertTrue(Path(str(self.source) + "-wal").is_file())
        before = safety.audit_database(self.source)
        backup, manifest = self.backup()
        self.assertEqual(before, manifest["database"]["audit"])
        self.assertNotIn("private-text", json.dumps(manifest))
        restored = self.root / "restored"
        result = safety.restore_isolated(backup, restored)
        self.assertEqual(before, result["audit"])
        with contextlib.closing(safety.open_readonly(restored / safety.DATABASE_NAME)) as con:
            self.assertEqual(con.execute("SELECT * FROM child").fetchall(), self.writer.execute("SELECT * FROM child").fetchall())
            self.assertEqual(con.execute("SELECT * FROM sqlite_sequence").fetchall(), [("parent", 1)])
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        for path in backup.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_source_does_not_create_database_or_output(self):
        missing = self.root / "missing.db"
        with self.assertRaises(safety.SafetyError):
            safety.create_backup(missing, self.root / "backup")
        self.assertFalse(missing.exists())
        self.assertFalse((self.root / "backup").exists())

    def test_unreadable_source_fails_closed(self):
        self.source.chmod(0)
        try:
            with self.assertRaises(safety.SafetyError):
                safety.audit_database(self.source)
        finally:
            self.source.chmod(0o600)

    def test_no_overwrite_even_for_empty_existing_directory_or_source_parent(self):
        backup, _ = self.backup()
        original = safety.file_hash(backup / safety.DATABASE_NAME)
        with self.assertRaises(safety.SafetyError):
            safety.create_backup(self.source, backup)
        with self.assertRaises(safety.SafetyError):
            safety.restore_isolated(backup, self.source.parent)
        self.assertEqual(original, safety.file_hash(backup / safety.DATABASE_NAME))

    def test_incomplete_backup_never_verifies_or_replaces_old_artifact(self):
        old, _ = self.backup()
        old_hash = safety.file_hash(old / safety.DATABASE_NAME)
        broken = self.root / "broken"
        with mock.patch.object(safety, "copy_database", side_effect=OSError("secret-token")):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                code = safety.main(["backup", "--source", str(self.source), "--output", str(broken)])
        self.assertEqual(code, 1)
        self.assertNotIn("secret-token", error.getvalue())
        self.assertTrue((broken / "INCOMPLETE.json").exists())
        with self.assertRaises(safety.SafetyError):
            safety.verified_backup(broken)
        self.assertEqual(old_hash, safety.file_hash(old / safety.DATABASE_NAME))

    def test_newer_version_is_not_backed_up_or_restored(self):
        self.writer.execute("PRAGMA user_version=3")
        with self.assertRaises(safety.SafetyError):
            self.backup()
        self.assertFalse((self.root / "backup").exists())

    def test_backup_deadline_stops_without_touching_source(self):
        before = safety.audit_database(self.source)
        with contextlib.closing(safety.open_readonly(self.source)) as con:
            with self.assertRaises(safety.SafetyError) as error:
                safety.copy_database(con, self.root / "timed-out.sqlite3", timeout=-1)
        self.assertEqual(error.exception.code, "BACKUP_TIMEOUT")
        self.assertEqual(before, safety.audit_database(self.source))

    def test_hash_tampering_and_manifest_traversal_fail_before_restore(self):
        backup, manifest = self.backup()
        database = backup / safety.DATABASE_NAME
        with database.open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaises(safety.SafetyError):
            safety.restore_isolated(backup, self.root / "restore")
        self.assertFalse((self.root / "restore").exists())
        manifest["database"]["filename"] = "../source.db"
        (backup / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(safety.SafetyError):
            safety.verified_backup(backup)

    def test_logical_fingerprints_detect_change_even_if_file_hash_is_replaced(self):
        backup, manifest = self.backup()
        database = backup / safety.DATABASE_NAME
        with sqlite3.connect(database) as con:
            con.execute("UPDATE child SET n=n-1")
        manifest["database"]["sha256"] = safety.file_hash(database)
        manifest["database"]["size_bytes"] = database.stat().st_size
        (backup / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(safety.SafetyError):
            safety.verified_backup(backup)

    def test_foreign_key_failure_rejects_backup_without_reporting_row_content(self):
        self.writer.execute("PRAGMA foreign_keys=OFF")
        self.writer.execute("INSERT INTO child(id,parent_id) VALUES(9,999)")
        self.writer.commit()
        with self.assertRaises(safety.SafetyError):
            self.backup()
        self.assertFalse((self.root / "backup").exists())

    def test_concurrent_wal_writer_cannot_mix_audit_and_backup_snapshots(self):
        original_copy = safety.copy_database

        def commit_then_copy(con, destination):
            self.writer.execute("INSERT INTO parent(title) VALUES('newer-commit')")
            self.writer.commit()
            original_copy(con, destination)

        with mock.patch.object(safety, "copy_database", side_effect=commit_then_copy):
            _, manifest = self.backup()
        self.assertEqual(manifest["database"]["audit"]["tables"]["parent"]["row_count"], 1)
        self.assertEqual(safety.audit_database(self.source)["tables"]["parent"]["row_count"], 2)

    def test_symlink_input_rejected_and_readonly_connection_cannot_write(self):
        alias = self.root / "alias.db"
        alias.symlink_to(self.source)
        with self.assertRaises(safety.SafetyError):
            safety.audit_database(alias)
        with contextlib.closing(safety.open_readonly(self.source)) as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM parent")

    def test_duplicate_rows_null_blob_and_identifier_quoting(self):
        self.writer.execute('CREATE TABLE "odd""name"(value, other)')
        self.writer.executemany('INSERT INTO "odd""name" VALUES(?,?)', [(None,b""),(None,b""),(0,"0")])
        self.writer.commit()
        _, manifest = self.backup()
        self.assertEqual(manifest["database"]["audit"]["tables"]['odd"name']["row_count"], 3)

    def test_cli_from_outside_project_has_no_legacy_imports_or_secret_output(self):
        script = Path(safety.__file__).resolve()
        env = {**os.environ, "GITHUB_TOKEN": "cli-secret-never-print"}
        result = subprocess.run([sys.executable,"-B",str(script),"audit","--source",str(self.source)],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("cli-secret", result.stdout + result.stderr)
        self.assertNotIn("private-text", result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["user_version"], 2)


if __name__ == "__main__":
    unittest.main()
