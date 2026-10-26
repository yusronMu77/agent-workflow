"""A review capsule cannot take over or complete its parent's task."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.engine import Engine
from workflow_core.project import Project, Rejected, digest
from workflow_core.outputs import Outputs


class ReviewPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-review-pack-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "AGENTS.md").write_text("Required agent rules.", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("Required canonical rules.", encoding="utf-8")
        (self.root / "source.md").write_text("Source finding.", encoding="utf-8")
        Project.init(self.root)
        self.engine = Engine(self.root)
        self.addCleanup(self.engine.close)
        self.engine.create("parent", "Review the artifact", "design", "project:result.md",
                           sources=["project:source.md:1:1"])
        self.engine.pack("parent", host="codex")
        (self.root / "result.md").write_text("Artifact to review.", encoding="utf-8")

    def test_full_policy_read_only_contract_hashes_without_parent_transition(self):
        before = self.engine.store.task("parent")
        reservations = self.engine.store.connection.execute("SELECT * FROM reservations").fetchall()
        result = self.engine.pack("parent", audience="subagent-review")
        text = Path(result["context_path"]).read_text(encoding="utf-8")
        self.assertIn("Required agent rules.", text)
        self.assertIn("Required canonical rules.", text)
        self.assertIn("Source finding.", text)
        self.assertIn("Do not edit artifacts", text)
        self.assertEqual(result["host"], "none")
        self.assertEqual(result["dispatch"], "none")
        self.assertEqual(result["output"]["sha256"], digest((self.root / "result.md").read_bytes()))
        self.assertEqual(self.engine.store.task("parent"), before)
        self.assertEqual(self.engine.store.connection.execute("SELECT * FROM reservations").fetchall(), reservations)
        self.assertEqual(self.engine.store.connection.execute("SELECT count(*) FROM reviews").fetchone()[0], 0)
        metrics = Outputs(self.engine).metrics("parent")
        self.assertEqual(metrics["review_pack_count"], 1)
        self.assertEqual(metrics["review_pack_bytes"], result["context_bytes"])
        self.assertEqual(metrics["all_pack_bytes"], metrics["pack_bytes"] + result["context_bytes"])

    def test_reviewer_pack_detects_drift_and_cannot_omit_host_policies(self):
        with self.assertRaisesRegex(Rejected, "every policy"):
            self.engine.pack("parent", host="codex", audience="subagent-review")
        (self.root / "source.md").write_text("Changed source.", encoding="utf-8")
        with self.assertRaisesRegex(Rejected, "rebind"):
            self.engine.pack("parent", audience="subagent-review")

    def test_reviewer_pack_budget_failure_keeps_parent_state(self):
        before = self.engine.store.task("parent")
        self.engine.project.config["budget"]["pack_bytes"] = 10
        with self.assertRaisesRegex(Rejected, "budget exceeded"):
            self.engine.pack("parent", audience="subagent-review")
        self.assertEqual(self.engine.store.task("parent"), before)

    def test_distinct_output_snapshots_do_not_overwrite_a_reviewers_capsule(self):
        first = self.engine.pack("parent", audience="subagent-review")
        body = Path(first["context_path"]).read_bytes()
        (self.root / "result.md").write_text("Revised artifact.", encoding="utf-8")
        second = self.engine.pack("parent", audience="subagent-review")
        self.assertNotEqual(first["context_path"], second["context_path"])
        self.assertEqual(Path(first["context_path"]).read_bytes(), body)


if __name__ == "__main__":
    unittest.main()
