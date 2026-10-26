"""The shorter CLI path preserves failures, scope and explicit review requirements."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wf import main
from workflow_core.engine import Engine


class EfficientFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-efficient-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "AGENTS.md").write_text("Keep all mandatory fixture rules.\n", encoding="utf-8")

    def call(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["--project", str(self.root), *args])
        return code, json.loads(output.getvalue() or errors.getvalue())

    def start(self, *extra):
        return self.call("start", "t", "--init", "--request", "Produce checked JSON",
                         "--workflow", "code", "--output", "project:out.json", "--check", "json", *extra)

    def output(self, text='{"answer": 42}\n'):
        (self.root / "out.json").write_text(text, encoding="utf-8")
        (self.root / "check.txt").write_text("Checked actual fixture.\n", encoding="utf-8")

    def finish(self, exit_code=0, result="passed", note="Inspected output and actual check"):
        return self.call("finish", "t", "--input", "project:check.txt", "--exit-code", str(exit_code),
                         "--review-result", result, "--review-note", note)

    def test_two_commands_return_inline_policy_and_complete_all_gates(self):
        code, result = self.start()
        self.assertEqual(code, 0)
        self.assertIn("Keep all mandatory fixture rules.", result["context"])
        self.assertIn("capture_exit_zero", result["context"])
        self.output()
        code, result = self.finish()
        self.assertEqual((code, result["status"]), (0, "completed"))
        engine = Engine(self.root)
        try:
            self.assertEqual(engine.store.connection.execute("SELECT count(*) FROM reviews").fetchone()[0], 1)
            self.assertEqual(len(engine.captures("t")), 1)
            self.assertEqual(engine.store.connection.execute("SELECT count(*) FROM reservations").fetchone()[0], 0)
        finally:
            engine.close()

    def test_compact_context_preserves_complete_rules_and_selected_source(self):
        rules = "Mandatory first rule.\n" + "Preserve this rule verbatim.\n" * 180 + "Mandatory final rule.\n"
        (self.root / "AGENTS.md").write_text(rules, encoding="utf-8")
        (self.root / "spec.txt").write_text("irrelevant\nRequired source line.\n", encoding="utf-8")
        code, result = self.start("--source", "project:spec.txt:2:2")
        self.assertEqual(code, 0)
        self.assertIn(rules, result["context"])
        self.assertIn("Required source line.", result["context"])
        self.output()
        (self.root / "spec.txt").write_text("changed\nChanged source line.\n", encoding="utf-8")
        self.assertEqual(self.finish()[0], 2)

    def test_oversized_policy_is_rejected_without_trimming_or_completing(self):
        (self.root / "AGENTS.md").write_text("Mandatory rule.\n" * 5000, encoding="utf-8")
        code, result = self.start()
        self.assertEqual(code, 2)
        self.assertIn("never trim mandatory policy", result["reason"])
        self.output()
        self.assertEqual(self.finish()[0], 2)

    def test_failed_check_and_failed_native_review_remain_failures(self):
        self.start()
        self.output()
        code, result = self.finish(exit_code=1)
        self.assertEqual((code, result["status"]), (3, "repair_needed"))
        self.assertTrue(any(c["id"] == "capture_exit_zero" and c["result"] == "failed" for c in result["checks"]))

    def test_extra_reviews_are_not_silently_approved(self):
        self.start("--review-required", "domain_review")
        self.output()
        code, result = self.finish()
        self.assertEqual((code, result["status"]), (3, "needs_review"))
        self.assertIn({"id": "domain_review", "result": "not_checked"}, result["checks"])

    def test_empty_review_and_policy_drift_reject_before_capture(self):
        self.start()
        self.output()
        self.assertEqual(self.finish(note=" ")[0], 2)
        (self.root / "AGENTS.md").write_text("New rules.\n", encoding="utf-8")
        code, result = self.finish()
        self.assertEqual(code, 2)
        self.assertIn("rebind", result["reason"])
        engine = Engine(self.root)
        try:
            self.assertEqual(engine.captures("t"), [])
        finally:
            engine.close()

    def test_unknown_write_outcome_cannot_be_finished(self):
        self.start()
        self.output()
        self.call("unknown", "t", "--note", "Unsettled local fixture")
        self.assertEqual(self.finish()[0], 2)

    def test_latest_failure_cannot_be_hidden_by_an_earlier_passing_capture(self):
        self.start()
        self.output()
        self.call("capture", "t", "--input", "project:check.txt", "--exit-code", "0")
        code, result = self.finish(exit_code=1)
        self.assertEqual((code, result["status"]), (3, "repair_needed"))

    def test_invalid_json_cannot_complete_despite_passing_external_check(self):
        self.start()
        self.output("not JSON\n")
        self.assertEqual(self.finish()[1]["status"], "repair_needed")

    def test_review_failure_cannot_complete_with_exit_zero(self):
        self.start()
        self.output()
        self.assertEqual(self.finish(result="failed")[1]["status"], "repair_needed")

    def test_init_preserves_legacy_configuration(self):
        state = self.root / ".agent-workflow"
        state.mkdir()
        (state / "project.json").write_text("legacy", encoding="utf-8")
        self.assertEqual(self.start()[0], 2)
        self.assertEqual((state / "project.json").read_text(), "legacy")


if __name__ == "__main__":
    unittest.main()
