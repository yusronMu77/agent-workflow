"""Feedback decisions are evidence-bound records, not configuration activation."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.engine import Engine
from workflow_core.feedback import collect, decide
from workflow_core.project import Project, Rejected


class FeedbackDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-feedback-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "AGENTS.md").write_text("Keep project rules.", encoding="utf-8")
        (self.root / "proof.txt").write_text("Actual diagnostic evidence in this fixture.", encoding="utf-8")
        Project.init(self.root)
        self.engine = Engine(self.root)
        self.addCleanup(self.engine.close)
        self.engine.create("t", "Fixture", "code", "project:out.py")
        self.engine.pack("t")
        self.engine.unknown("t", "Synthetic timeout to exercise triage")
        collect(self.engine.store)
        self.proposal = self.engine.store.connection.execute("SELECT id FROM proposals").fetchone()[0]

    def choose(self, decision="accept", evidence="project:proof.txt", note="Diagnostic reviewed"):
        return decide(self.engine, self.proposal, decision, evidence, note)

    def test_accept_is_idempotent_and_does_not_activate_or_change_rules(self):
        before = (self.root / "AGENTS.md").read_bytes()
        first = self.choose()
        self.assertEqual(first["status"], "accepted")
        self.assertFalse(first["auto_apply"])
        self.assertFalse(first["configuration_changed"])
        self.assertTrue(self.choose()["idempotent"])
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), before)
        self.assertEqual(self.engine.store.task("t")["status"], "blocked_outcome")
        self.assertEqual(self.engine.store.connection.execute("SELECT count(*) FROM events WHERE kind='proposal_decided'").fetchone()[0], 1)

    def test_reject_is_terminal_and_evidence_changes_do_not_silently_rewrite_decision(self):
        self.assertEqual(self.choose("reject")["status"], "rejected")
        with self.assertRaisesRegex(Rejected, "immutable"):
            self.choose("accept")
        (self.root / "proof.txt").write_text("Changed evidence", encoding="utf-8")
        with self.assertRaisesRegex(Rejected, "immutable"):
            self.choose("reject")

    def test_failed_event_rolls_back_status(self):
        with patch.object(self.engine.store, "event", side_effect=RuntimeError("injected crash")):
            with self.assertRaises(RuntimeError):
                self.choose()
        self.assertEqual(self.engine.store.connection.execute("SELECT status FROM proposals").fetchone()[0], "proposed")

    def test_invalid_proof_note_scope_or_proposal_cannot_decide(self):
        for decision, evidence, note in (("activate", "project:proof.txt", "n"),
                                         ("accept", "project:proof.txt", " "),
                                         ("accept", "project:../outside.txt", "n"),
                                         ("accept", "project:.env", "n")):
            with self.subTest(decision=decision, evidence=evidence):
                with self.assertRaises((Rejected, OSError)):
                    self.choose(decision, evidence, note)
        with self.assertRaisesRegex(Rejected, "not found"):
            decide(self.engine, "missing", "reject", "project:proof.txt", "n")


if __name__ == "__main__":
    unittest.main()
