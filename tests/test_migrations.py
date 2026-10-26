"""Upgrade infrastructure against real schema-1 state; no production schema bump."""
from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.engine import Engine
from workflow_core.feedback import collect, propose_memory
from workflow_core.project import Project, Rejected
from workflow_core import state


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-migration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "AGENTS.md").write_text("Preserve state.\n", encoding="utf-8")
        (self.root / "source.txt").write_text("Exact source quote.\n", encoding="utf-8")
        self.project, _ = Project.init(self.root)
        engine = Engine(self.root)
        try:
            engine.create("pending", "Preserve task", "code", "project:out.py")
            engine.pack("pending")
            (self.root / "out.py").write_text("print('fixture')\n", encoding="utf-8")
            proof = engine.evidence("pending", "proof.txt", "Synthetic review to test upgrade preservation.")
            engine.review("pending", "final_review", "passed", proof["ref"], "Fixture only")
            engine.unknown("pending", "Synthetic uncertain write")
            collect(engine.store)
            propose_memory(engine.store, "project:source.txt", "Exact source quote.")
            with engine.store.tx() as db:
                db.execute("INSERT INTO releases(id,revoked,reason) VALUES('old',1,'test')")
        finally:
            engine.close()
        self.before = self.snapshot()

    def snapshot(self):
        with closing(sqlite3.connect(self.project.db)) as db, db:
            return {name: db.execute("SELECT * FROM " + name + " ORDER BY rowid").fetchall()
                    for name in ("tasks", "reservations", "reviews", "events", "proposals", "memories", "releases", "meta")}

    @staticmethod
    def upgrade(db):
        db.execute("CREATE TABLE upgrade_fixture(id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO upgrade_fixture VALUES('preserved')")

    def test_upgrade_preserves_state_and_runs_once(self):
        with patch.object(state, "SCHEMA_VERSION", 2), patch.dict(state.MIGRATIONS, {1: self.upgrade}, clear=True):
            for _ in range(2):
                store = state.Store(self.project)
                store.close()
        after = self.snapshot()
        for table in ("tasks", "reservations", "reviews", "proposals", "memories", "releases"):
            self.assertEqual(after[table], self.before[table], table)
        self.assertEqual(after["events"][:-1], self.before["events"])
        self.assertEqual(after["events"][-1][2], "schema_migrated")
        with closing(sqlite3.connect(self.project.db)) as db, db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "2")
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_second_step_rolls_back_ddl_data_version_and_events(self):
        def fail(db):
            db.execute("DELETE FROM proposals")
            raise RuntimeError("injected migration failure")
        with patch.object(state, "SCHEMA_VERSION", 3), patch.dict(state.MIGRATIONS, {1: self.upgrade, 2: fail}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                state.Store(self.project)
        self.assertEqual(self.snapshot(), self.before)
        with closing(sqlite3.connect(self.project.db)) as db, db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='upgrade_fixture'").fetchone())
        # A failed open released its connection and the old kit can still open.
        store = state.Store(self.project)
        store.close()

    def test_two_openers_upgrade_only_once(self):
        def open_store(_):
            store = state.Store(self.project)
            store.close()
        with patch.object(state, "SCHEMA_VERSION", 2), patch.dict(state.MIGRATIONS, {1: self.upgrade}, clear=True):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(open_store, range(2)))
        with closing(sqlite3.connect(self.project.db)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='schema_migrated'").fetchone()[0], 1)

    def test_missing_step_refuses_before_running_any_upgrade(self):
        with patch.object(state, "SCHEMA_VERSION", 3), patch.dict(state.MIGRATIONS, {1: self.upgrade}, clear=True):
            with self.assertRaisesRegex(Rejected, "Missing database migration 2"):
                state.Store(self.project)
        self.assertEqual(self.snapshot(), self.before)

    def test_future_schema_is_not_repaired_or_downgraded(self):
        with closing(sqlite3.connect(self.project.db)) as db, db:
            db.execute("UPDATE meta SET value='99' WHERE key='schema'")
            db.execute("DROP TABLE memories")
        before = self.snapshot_schema()
        with self.assertRaisesRegex(Rejected, "newer"):
            state.Store(self.project)
        self.assertEqual(self.snapshot_schema(), before)

    def snapshot_schema(self):
        with closing(sqlite3.connect(self.project.db)) as db, db:
            return db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()

    def test_identity_mismatch_prevents_migration(self):
        self.project.config["project_id"] = "other-project"
        with patch.object(state, "SCHEMA_VERSION", 2), patch.dict(state.MIGRATIONS, {1: self.upgrade}, clear=True):
            with self.assertRaisesRegex(Rejected, "identity"):
                state.Store(self.project)
        self.assertEqual(self.snapshot(), self.before)

    def test_invalid_or_missing_version_is_not_silently_initialized(self):
        for value in (None, "garbage", "0", "-1", "1.5", b"1"):
            with self.subTest(value=value):
                with closing(sqlite3.connect(self.project.db)) as db, db:
                    db.execute("DELETE FROM meta WHERE key='schema'")
                    if value is not None:
                        db.execute("INSERT INTO meta VALUES('schema',?)", (value,))
                before = self.snapshot()
                with self.assertRaisesRegex(Rejected, "schema metadata"):
                    state.Store(self.project)
                self.assertEqual(self.snapshot(), before)

    def test_unversioned_database_is_preserved(self):
        other = self.root / "unversioned"
        other.mkdir()
        project, _ = Project.init(other)
        with closing(sqlite3.connect(project.db)) as db, db:
            db.execute("CREATE TABLE legacy(value TEXT)")
            db.execute("INSERT INTO legacy VALUES('keep')")
        with self.assertRaisesRegex(Rejected, "Unversioned"):
            state.Store(project)
        with closing(sqlite3.connect(project.db)) as db, db:
            self.assertEqual(db.execute("SELECT * FROM legacy").fetchall(), [("keep",)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("legacy",)])


if __name__ == "__main__":
    unittest.main()
