"""Regressions discovered while qualifying remaining v1.2 behavior."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from qualify_io import probe, probe_reservations
from workflow_core.engine import Engine
from workflow_core.project import Project
from session_reminder import respond
import json


class ArtifactConcurrencyTests(unittest.TestCase):
    def test_two_processes_replace_distinct_staging_files_without_partial_data(self):
        self.assertTrue(probe(2)["passed"])

    def test_two_processes_cannot_commit_ownership_of_the_same_output(self):
        self.assertTrue(probe_reservations()["passed"])

    def test_failed_replace_preserves_old_artifact_and_cleans_only_own_temp(self):
        with tempfile.TemporaryDirectory(prefix="workflow-stage-test-") as directory:
            root = Path(directory)
            target = root / "artifact.txt"
            target.write_bytes(b"old")
            unrelated = root / "other.tmp"
            unrelated.write_bytes(b"keep")
            with patch.object(Path, "replace", side_effect=OSError("injected failure")):
                with self.assertRaises(OSError):
                    Engine.write_owned(None, target, b"new")
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(unrelated.read_bytes(), b"keep")
            self.assertEqual(list(root.glob("artifact.txt.*.tmp")), [])


class SessionReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-reminder-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        Project.init(self.root)
        self.engine = Engine(self.root)
        self.addCleanup(self.engine.close)
        self.engine.create("handoff", "Continue after handoff", "code", "project:out.py")
        self.engine.pack("handoff")
        self.engine.checkpoint("handoff", "Phase one done", "Inspect checkpoint")

    def payload(self, event="SessionStart", **values):
        return {"hook_event_name": event, "cwd": str(self.root), **values}

    def snapshot(self):
        db = self.engine.store.connection
        return [db.execute("SELECT * FROM " + table).fetchall() for table in ("tasks", "reviews", "events", "reservations")]

    def test_resume_and_compact_remind_without_mutating_workflow(self):
        before = self.snapshot()
        for source in ("startup", "resume", "compact"):
            result = respond(self.root, self.payload(source=source))
            self.assertIn("handoff", result["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.snapshot(), before)

    def test_stop_blocks_once_and_never_completes_or_replays(self):
        before = self.snapshot()
        first = respond(self.root, self.payload("Stop", stop_hook_active=False))
        self.assertEqual(first["decision"], "block")
        self.assertEqual(respond(self.root, self.payload("Stop", stop_hook_active=True)), {})
        self.assertEqual(self.snapshot(), before)

    def test_unknown_outcome_is_preserved_and_terminal_task_is_quiet(self):
        self.engine.unknown("handoff", "Timed out")
        self.assertIn("blocked_outcome", json.dumps(respond(self.root, self.payload())))
        # Cancel cannot erase unknown effects; a separate completed fixture is quiet.
        other = self.root / "empty"
        other.mkdir()
        Project.init(other)
        with_engine = Engine(other)
        with_engine.create("stopped", "Stop fixture", "code", "project:out.py")
        with_engine.pack("stopped")
        with_engine.cancel("stopped", "Effects settled in fixture", quiescent=True)
        with_engine.close()
        self.assertEqual(respond(other, {"cwd": str(other), "hook_event_name": "Stop"}), {})

    def test_wrong_scope_or_missing_state_is_quiet(self):
        self.assertEqual(respond(self.root, {"hook_event_name": "SessionStart", "cwd": str(self.root.parent)}), {})
        self.assertEqual(respond(self.root, {"hook_event_name": "SessionStart"}), {})
        missing = self.root / "not-initialized"
        missing.mkdir()
        self.assertEqual(respond(missing, {"hook_event_name": "SessionStart", "cwd": str(missing)}), {})
        self.assertFalse((missing / ".agent-workflow").exists())

    def test_mismatched_identity_is_not_exposed_or_repaired(self):
        with self.engine.store.tx() as db:
            db.execute("UPDATE meta SET value='foreign-fixture' WHERE key='project_id'")
        self.assertEqual(respond(self.root, self.payload()), {})
        self.assertEqual(self.engine.store.connection.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()[0], "foreign-fixture")

if __name__ == "__main__":
    unittest.main()
