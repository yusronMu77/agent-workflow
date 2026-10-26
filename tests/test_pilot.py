"""Pilot infrastructure tests use synthetic records, never agent A/B results."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pilot_ab import prepare, summarize


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-pilot-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "pilot"
        prepare(self.root)
        self.plan = self.root / "plan.json"
        self.results = json.loads((self.root / "results.template.json").read_text())
        self.result_path = self.root / "results.json"

    def report(self):
        self.result_path.write_text(json.dumps(self.results), encoding="utf-8")
        return summarize(self.plan, self.result_path)

    def fill_synthetic_records(self):
        for index, row in enumerate(self.results["trials"]):
            row.update(session_id=f"synthetic-session-{index}", model="fixture", effort="fixture",
                       client_version="fixture", tool_catalog_sha256="a" * 64,
                       elapsed_seconds=10 + index, success=True, rubric_evidence="trace.txt",
                       tool_calls=4, retries=0, user_interventions=0, instruction_violations=0)
            (self.root / row["trial_id"] / "trace.txt").write_text("SYNTHETIC RECORD, not a native trial.\n", encoding="utf-8")

    def test_preparation_pins_identical_project_and_kit_snapshots(self):
        plan = json.loads(self.plan.read_text())
        self.assertEqual(len(plan["trials"]), 6)
        self.assertEqual(len({r["snapshot_sha256"] for r in plan["trials"]}), 3)
        for scenario in ("bug", "short-output", "long-log"):
            def files(arm):
                root = self.root / (scenario + "-" + arm)
                return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(files("A"), files("B"))
        self.assertFalse((self.root / "results.json").exists())
        with self.assertRaisesRegex(ValueError, "new pilot"):
            prepare(self.root)

    def test_preparation_rejects_parent_agent_workspace(self):
        (self.base / "AGENTS.md").write_text("parent rules", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside"):
            prepare(self.base / "other")

    def test_empty_template_remains_pending(self):
        result = self.report()
        self.assertEqual(result["status"], "pending_fresh_sessions")
        self.assertFalse(result["completed_comparison"])
        self.assertEqual(len(result["pending"]), 6)

    def test_synthetic_records_validate_structure_not_agent_quality(self):
        self.fill_synthetic_records()
        result = self.report()
        self.assertEqual(result["status"], "exploratory_complete")
        self.assertTrue(result["acceptance_passed"])
        self.assertEqual(len(result["pairs"]), 3)
        self.assertEqual(len(result["evidence"]), 6)
        self.assertTrue(all(row["B_minus_A_seconds"] == 1 for row in result["pairs"]))

    def test_failed_rubric_or_instruction_violation_fails_acceptance(self):
        self.fill_synthetic_records()
        self.results["trials"][0]["success"] = False
        self.assertFalse(self.report()["acceptance_passed"])
        self.results["trials"][0]["success"] = True
        self.results["trials"][1]["instruction_violations"] = 1
        self.assertFalse(self.report()["acceptance_passed"])

    def test_rejects_reused_session_and_uncontrolled_settings(self):
        self.fill_synthetic_records()
        self.results["trials"][1]["session_id"] = self.results["trials"][0]["session_id"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.report()
        self.fill_synthetic_records()
        self.results["trials"][1]["effort"] = "changed"
        with self.assertRaisesRegex(ValueError, "must match"):
            self.report()

    def test_rejects_duplicate_trial_evidence_escape_and_invalid_metrics(self):
        self.fill_synthetic_records()
        self.results["trials"].append(self.results["trials"][0].copy())
        with self.assertRaisesRegex(ValueError, "exactly once"):
            self.report()
        self.results["trials"].pop()
        self.results["trials"][0]["rubric_evidence"] = "../results.template.json"
        with self.assertRaisesRegex(ValueError, "within its trial"):
            self.report()
        self.fill_synthetic_records()
        for field, value in (("elapsed_seconds", float("nan")), ("elapsed_seconds", -1),
                             ("tool_calls", True), ("tool_calls", 1.5), ("success", "yes")):
            with self.subTest(field=field, value=value):
                original = self.results["trials"][0][field]
                self.results["trials"][0][field] = value
                with self.assertRaises(ValueError):
                    self.report()
                self.results["trials"][0][field] = original


if __name__ == "__main__":
    unittest.main()
