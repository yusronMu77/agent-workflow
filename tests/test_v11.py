"""Regression tests for the v1.1 review fixes: evidence gate, host-aware packs, rebind, ordering, schema."""
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.engine import Engine, detect_host
from workflow_core.outputs import Outputs
from workflow_core.project import Project, Rejected
from workflow_core.state import Store


class V11Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-v11-")
        self.root = Path(self.temp.name).resolve()
        (self.root / "CLAUDE.md").write_text("Canonical project rules.\n" * 400, encoding="utf-8")
        (self.root / "AGENTS.md").write_text("Read CLAUDE.md completely.\n", encoding="utf-8")
        (self.root / "source.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        Project.init(self.root)
        self.engine = Engine(self.root)
        self.outputs = Outputs(self.engine)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def begin(self, task="t", **kw):
        kw.setdefault("sources", ["project:source.txt:1:1"])
        self.engine.create(task, "Do the work", "code", "project:out.py", **kw)
        return task

    def write_output(self, text="print(1)\n"):
        (self.root / "out.py").write_text(text, encoding="utf-8")

    def capture(self, task, text="ok\n", exit_code=0):
        (self.root / "tool.txt").write_text(text, encoding="utf-8")
        return self.outputs.capture(task, "project:tool.txt", exit_code)

    def final_review(self, task):
        proof = self.engine.evidence(task, "proof.txt", "Inspected the artifact and the captured check.")
        self.engine.review(task, "final_review", "passed", proof["ref"], "Inspected")

    def test_events_keep_insertion_order(self):
        task = self.begin()
        self.engine.pack(task)
        sources = []
        for index in range(8):
            (self.root / f"log{index}.txt").write_text(f"line {index}\n", encoding="utf-8")
            self.outputs.capture(task, f"project:log{index}.txt")
            sources.append(f"project:log{index}.txt")
        self.assertEqual([c["source"] for c in self.engine.captures(task)], sources)

    def test_non_utf8_source_is_explained(self):
        (self.root / "binary.dat").write_bytes(b"\xff\xfe\x00")
        with self.assertRaisesRegex(Rejected, "UTF-8"):
            self.begin(sources=["project:binary.dat:1:1"])

    def test_newer_schema_is_refused(self):
        self.engine.close()
        connection = sqlite3.connect(self.engine.project.db)
        connection.execute("UPDATE meta SET value='99' WHERE key='schema'")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(Rejected, "newer"):
            Engine(self.root)

    def test_task_minutes_override_budget(self):
        self.begin("short", minutes=5)
        deadline = self.engine.store.task("short")["spec"]["deadline"]
        remaining = (datetime.fromisoformat(deadline) - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(200 < remaining <= 300)
        for bad in (0, -1, 10081, True):
            with self.assertRaises(Rejected):
                self.engine.create(f"bad{bad}", "r", "code", f"project:o{bad}.py", minutes=bad)

    def test_pack_references_host_autoloaded_policy(self):
        task = self.begin()
        claude = self.engine.pack(task, host="claude")
        text = Path(claude["context_path"]).read_text(encoding="utf-8")
        self.assertIn("reference, ", text)
        self.assertNotIn("Canonical project rules.", text)
        self.assertIn("Read CLAUDE.md completely.", text)  # AGENTS.md is not autoloaded by Claude Code
        self.assertGreater(claude["policy_ref_bytes"], 9000)
        self.assertLess(claude["context_bytes"], 3000)
        both = self.engine.pack(task, host="none")
        self.assertIn("Canonical project rules.", Path(both["context_path"]).read_text(encoding="utf-8"))
        self.assertEqual(both["policy_ref_bytes"], 0)
        self.assertGreater(both["context_bytes"], claude["context_bytes"] + 9000)
        with self.assertRaises(Rejected):
            self.engine.pack(task, host="other")
        metrics = self.outputs.metrics(task)
        self.assertEqual(metrics["pack_count"], 2)
        self.assertGreater(metrics["policy_ref_bytes"], 9000)

    def test_host_detection_defaults_to_inline(self):
        with patch.dict(os.environ, {"CLAUDECODE": "1"}):
            self.assertEqual(detect_host(), "claude")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDECODE", None)
            self.assertEqual(detect_host(), "none")

    def test_reference_pack_still_detects_policy_drift(self):
        task = self.begin()
        self.engine.pack(task, host="claude")
        (self.root / "CLAUDE.md").write_text("Changed rule.\n", encoding="utf-8")
        with self.assertRaisesRegex(Rejected, "rebind"):
            self.engine.pack(task, host="claude")

    def test_capture_gate_requires_exit_zero_for_this_output(self):
        task = self.begin(checks=["capture_exit_zero"])
        self.engine.pack(task)
        self.write_output()
        self.final_review(task)
        self.assertEqual(self.engine.verify(task)["status"], "needs_review")  # no capture yet
        self.capture(task, exit_code=None)  # caller reported nothing: still no evidence
        self.assertEqual(self.engine.capture_gate(task, self.output_hash()), "not_checked")
        self.capture(task, "FAILED\n", exit_code=1)
        self.assertEqual(self.engine.capture_gate(task, self.output_hash()), "failed")
        self.capture(task, "OK\n", exit_code=0)
        self.assertEqual(self.engine.verify(task)["status"], "completed")

    def test_capture_gate_ignores_run_before_output_changed(self):
        task = self.begin(checks=["capture_exit_zero"])
        self.engine.pack(task)
        self.write_output("print(1)\n")
        self.capture(task, exit_code=0)
        self.write_output("print(2)\n")  # edited after the passing run
        self.assertEqual(self.engine.capture_gate(task, self.output_hash()), "not_checked")
        self.final_review(task)
        self.assertEqual(self.engine.verify(task)["status"], "needs_review")

    def test_failed_capture_gate_needs_repair(self):
        task = self.begin(checks=["capture_exit_zero"])
        self.engine.pack(task)
        self.write_output()
        self.capture(task, "FAILED\n", exit_code=2)
        self.final_review(task)
        self.assertEqual(self.engine.verify(task)["status"], "repair_needed")

    def test_review_can_cite_captured_output(self):
        task = self.begin()
        self.engine.pack(task)
        self.write_output()
        key = self.capture(task)["key"]
        self.engine.review(task, "final_review", "passed", f"output:{key}", "Test log read in full")
        self.assertEqual(self.engine.verify(task)["status"], "completed")

    def test_captured_output_evidence_rejects_unknown_and_tampered(self):
        task = self.begin()
        self.engine.pack(task)
        self.write_output()
        with self.assertRaises(Rejected):
            self.engine.review(task, "final_review", "passed", "output:" + "0" * 32, "Nothing was captured")
        key = self.capture(task)["key"]
        raw = self.engine.directory(task) / "outputs" / key / "raw.txt"
        raw.write_bytes(raw.read_bytes() + b"tampered\n")
        with self.assertRaises(Rejected):
            self.engine.review(task, "final_review", "passed", f"output:{key}", "Tampered archive")

    def test_rebind_accepts_reviewed_drift_and_clears_reviews(self):
        task = self.begin()
        self.engine.pack(task, host="none")
        self.write_output()
        self.final_review(task)
        (self.root / "CLAUDE.md").write_text("Tighter rule.\n", encoding="utf-8")
        with self.assertRaisesRegex(Rejected, "rebind"):
            self.engine.checkpoint(task, "s", "n")
        with self.assertRaises(Rejected):
            self.engine.rebind(task, "  ")
        result = self.engine.rebind(task, "Read the new CLAUDE.md diff; narrower scope only")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["changes"]["policies"], ["CLAUDE.md"])
        self.assertEqual(self.engine.store.connection.execute("SELECT count(*) FROM reviews").fetchone()[0], 0)
        with self.assertRaises(Rejected):
            self.engine.verify(task)  # must pack again before verifying
        text = Path(self.engine.pack(task, host="none")["context_path"]).read_text(encoding="utf-8")
        self.assertIn("Tighter rule.", text)
        proof = self.engine.evidence(task, "proof2.txt", "Reviewed under the new policy.")
        self.engine.review(task, "final_review", "passed", proof["ref"], "Redone after rebind")
        self.assertEqual(self.engine.verify(task)["status"], "completed")

    def test_rebind_refreshes_changed_source_and_refuses_noop_or_terminal(self):
        task = self.begin()
        self.engine.pack(task, host="none")
        with self.assertRaisesRegex(Rejected, "Nothing changed"):
            self.engine.rebind(task, "No drift yet")
        (self.root / "source.txt").write_text("alpha changed\nbeta\n", encoding="utf-8")
        result = self.engine.rebind(task, "Source edited by me")
        self.assertEqual(result["changes"]["sources"], ["project:source.txt:1:1"])
        self.assertIn("alpha changed", self.engine.store.task(task)["spec"]["sources"][0]["text"])
        self.engine.cancel(task, "stop", quiescent=True)
        with self.assertRaises(Rejected):
            self.engine.rebind(task, "Too late")

    def test_rebind_refuses_blocked_unknown_outcome(self):
        task = self.begin()
        self.engine.pack(task, host="none")
        self.engine.unknown(task, "write timed out")
        (self.root / "AGENTS.md").write_text("Changed.\n", encoding="utf-8")
        with self.assertRaises(Rejected):
            self.engine.rebind(task, "Would hide the unknown outcome")

    def test_claude_probe_accepts_kit_and_rejects_triggerless_description(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from probe_claude_skill import KIT, check
        self.assertEqual(check(KIT, run_claude=False)["errors"], [])
        copy = self.root / "kit"
        for name in ("VERSION", "plugin.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
                     ".codex-plugin/plugin.json", "wf.py", "skills/agent-workflow/SKILL.md",
                     "skills/agent-workflow/scripts/wf.py"):
            target = copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((KIT / name).read_bytes())
        import shutil
        shutil.copytree(KIT / "workflow_core", copy / "workflow_core", ignore=shutil.ignore_patterns("__pycache__"))
        skill = copy / "skills/agent-workflow/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8").replace("; not for questions, lookups or one-step edits", ""),
                         encoding="utf-8")
        report = check(copy, run_claude=False)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("NOT to trigger" in e for e in report["errors"]))

    def output_hash(self):
        from workflow_core.project import digest
        return digest((self.root / "out.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
