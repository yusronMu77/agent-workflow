import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow_core.engine import Engine
from workflow_core.feedback import collect, commit_memory, propose_memory, recall
from workflow_core.project import Project, Rejected


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="durable-workflow-")
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project Ω"
        self.project.mkdir()
        (self.project / "AGENTS.md").write_text("Keep mandatory project policy.\n", encoding="utf-8")
        (self.project / "source.txt").write_text("Atlas retains data for 7 days.\n" + "Unneeded appendix.\n" * 3000, encoding="utf-8")
        Project.init(self.project)
        self.engine = Engine(self.project)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def begin(self, task="example", **kw):
        return self.engine.create(task, "Write a sourced summary", "document", kw.get("output", "project:answer.md"),
                                  kw.get("sources", ["project:source.txt:1:1"]), kw.get("contains", ["7 days"]))

    def output(self, text="Atlas retains data for 7 days."):
        (self.project / "answer.md").write_text(text, encoding="utf-8")

    def review(self, task="example"):
        proof = self.engine.evidence(task, "review.txt", "Checked source quotation and requested output manually.")
        self.engine.review(task, "final_review", "passed", proof["ref"], "Source and final artifact inspected")

    def test_init_idempotent_and_identity(self):
        first = self.engine.project.config["project_id"]
        p, changed = Project.init(self.project)
        self.assertFalse(changed)
        self.assertEqual(first, p.config["project_id"])

    def test_legacy_state_is_preserved(self):
        other = self.base / "legacy"
        state = other / ".agent-workflow"
        state.mkdir(parents=True)
        (state / "config.json").write_text("legacy", encoding="utf-8")
        with self.assertRaises(Rejected):
            Project.init(other)
        self.assertEqual((state / "config.json").read_text(), "legacy")

    def test_pack_keeps_policy_and_excludes_appendix(self):
        self.begin()
        result = self.engine.pack("example")
        text = Path(result["context_path"]).read_text(encoding="utf-8")
        self.assertIn("Keep mandatory project policy.", text)
        self.assertNotIn("Unneeded appendix", text)
        self.assertIsNone(result["native_tokens"])

    def test_native_review_required_and_completion(self):
        self.begin()
        self.engine.pack("example")
        self.output()
        self.assertEqual(self.engine.verify("example")["status"], "needs_review")
        self.review()
        result = self.engine.verify("example")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["checks"][-1]["grade"], "native_review")

    def test_review_is_invalidated_by_output_change(self):
        self.begin()
        self.engine.pack("example")
        self.output()
        self.review()
        self.output("Updated report: 7 days.")
        self.assertEqual(self.engine.verify("example")["status"], "needs_review")

    def test_shared_output_reservation(self):
        self.begin()
        with self.assertRaises(Rejected):
            self.begin(task="other")
        self.assertEqual(self.engine.store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_transaction_rolls_back_event_and_state(self):
        self.begin()
        before = self.engine.store.task("example")
        events = self.engine.store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        with patch.object(self.engine.store, "event", side_effect=RuntimeError("simulated failure before commit")):
            with self.assertRaises(RuntimeError):
                self.engine.checkpoint("example", "new", "next")
        self.assertEqual(self.engine.store.task("example")["revision"], before["revision"])
        self.assertEqual(self.engine.store.connection.execute("SELECT count(*) FROM events").fetchone()[0], events)

    def test_source_and_policy_drift(self):
        self.begin()
        (self.project / "source.txt").write_text("Changed", encoding="utf-8")
        with self.assertRaises(Rejected):
            self.engine.pack("example")
        (self.project / "CLAUDE.md").write_text("New policy", encoding="utf-8")
        with self.assertRaises(Rejected):
            self.engine.resume("example")

    def test_expected_revision_cas(self):
        self.begin()
        old = self.engine.store.task("example")["revision"]
        self.engine.checkpoint("example", "first writer", "continue")
        with self.assertRaises(Rejected):
            self.engine.store.transition("example", "running", "stale", {}, expected_revision=old)

    def test_failure_feedback_is_idempotent_and_repairs_bounded(self):
        self.begin()
        self.engine.pack("example")
        for i in range(3):
            self.output("Wrong output " + str(i))
            result = self.engine.verify("example")
            self.assertEqual(result["status"], "repair_needed" if i < 2 else "partial")
            self.assertFalse(result["feedback"]["auto_apply"])
        self.assertEqual(collect(self.engine.store)["new_proposals"], 0)
        self.assertEqual(self.engine.store.connection.execute("SELECT count(*) FROM proposals").fetchone()[0], 3)

    def test_unchanged_failure_does_not_burn_retry(self):
        self.begin()
        self.engine.pack("example")
        self.output("Wrong")
        self.engine.verify("example")
        with self.assertRaises(Rejected):
            self.engine.verify("example")
        self.assertEqual(self.engine.store.task("example")["repairs"], 1)

    def test_unknown_blocks_until_reconciled(self):
        self.begin()
        self.engine.pack("example")
        proof = self.engine.evidence("example", "receipt.txt", "Upstream job completed and artifact inspected")
        self.engine.unknown("example", "Write timed out")
        with self.assertRaises(Rejected):
            self.engine.pack("example")
        result = self.engine.reconcile("example", "succeeded", proof["ref"], "Job status and output checked")
        self.assertEqual(result["status"], "running")

    def test_memory_candidate_stale_source_and_revocation(self):
        result = propose_memory(self.engine.store, "project:source.txt", "Atlas retains data for 7 days.")
        self.assertEqual(recall(self.engine.store, "Atlas")["hits"], [])
        commit_memory(self.engine.store, result["id"])
        self.assertEqual(len(recall(self.engine.store, "Atlas")["hits"]), 1)
        self.assertTrue(commit_memory(self.engine.store, result["id"])["idempotent"])
        self.engine.store.connection.execute("UPDATE releases SET revoked=1 WHERE id='builtin-v1'")
        self.assertEqual(recall(self.engine.store, "Atlas")["hits"], [])

    def test_memory_source_change_and_record_change_are_not_recalled(self):
        candidate = propose_memory(self.engine.store, "project:source.txt", "Atlas retains data for 7 days.")
        record = commit_memory(self.engine.store, candidate["id"])
        Path(record["path"]).write_text("Tampered", encoding="utf-8")
        self.assertEqual(recall(self.engine.store, "Atlas")["hits"], [])
        (self.project / "source.txt").write_text("New retention", encoding="utf-8")
        with self.assertRaises(Rejected):
            commit_memory(self.engine.store, candidate["id"])

    def test_memory_prepared_file_crash_reconciles(self):
        candidate = propose_memory(self.engine.store, "project:source.txt", "Atlas retains data for 7 days.")
        with patch.object(self.engine.store, "event", side_effect=RuntimeError("crash after file before DB commit")):
            with self.assertRaises(RuntimeError):
                commit_memory(self.engine.store, candidate["id"])
        self.assertEqual(self.engine.store.connection.execute("SELECT status FROM memories").fetchone()[0], "prepared")
        self.assertEqual(recall(self.engine.store, "Atlas")["hits"], [])
        self.assertEqual(commit_memory(self.engine.store, candidate["id"])["status"], "verified_quote")

    def test_native_in_place_code_edit_is_allowed(self):
        source = self.project / "answer.md"
        source.write_text("old", encoding="utf-8")
        self.begin(sources=["project:answer.md:1:1"])
        self.engine.pack("example")
        self.output()
        self.review()
        self.assertEqual(self.engine.verify("example")["status"], "completed")

    def test_scope_secrets_and_symlink_escape(self):
        for value in ("project:../outside", "project:.ENV", "project:.mcp.json", "project:.agent-workflow/state.sqlite"):
            with self.assertRaises(Rejected):
                self.engine.project.resolve(value)
        outside = self.base / "outside"
        outside.mkdir()
        link = self.project / "link"
        if sys.platform == "win32":
            import os
            process = subprocess.run(["pwsh", "-NoProfile", "-Command", "New-Item -ItemType Junction -Path $env:WF_LINK -Target $env:WF_TARGET -ErrorAction Stop | Out-Null"],
                                     env=dict(os.environ, WF_LINK=str(link), WF_TARGET=str(outside)), capture_output=True)
            self.assertEqual(process.returncode, 0)
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(Rejected):
            self.engine.project.resolve("project:link/file.txt")

    def test_workspace_jsonc_and_multi_root(self):
        sibling = self.base / "library"
        sibling.mkdir()
        (sibling / "spec.txt").write_text("Specification", encoding="utf-8")
        target = self.base / "workspace project"
        target.mkdir()
        config = self.base / "test.code-workspace"
        config.write_text('{// comment\n"folders":[{"name":"app","path":"workspace project"},{"name":"library","path":"library"},],}', encoding="utf-8")
        p, _ = Project.init(target, workspace=config)
        self.assertEqual(p.snapshot("library:spec.txt")["text"], "Specification")

    def test_db_backup_and_relocation(self):
        self.begin()
        self.engine.pack("example")
        self.output()
        self.review()
        self.engine.verify("example")
        self.engine.close()
        moved = self.base / "moved"
        shutil.copytree(self.project, moved)
        self.engine = Engine(moved)
        self.assertEqual(self.engine.resume("example")["status"], "completed")

    def test_skill_launcher_from_unrelated_cwd(self):
        launcher = ROOT / "skills/agent-workflow/scripts/wf.py"
        result = subprocess.run([sys.executable, str(launcher), "--project", str(self.project), "doctor"],
                                cwd=self.base, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_pack_over_budget_preserves_task_and_policy(self):
        self.begin()
        self.engine.project.config["budget"]["pack_bytes"] = 10
        with self.assertRaisesRegex(Rejected, "budget exceeded"):
            self.engine.pack("example")
        self.assertEqual(self.engine.store.task("example")["status"], "ready")

    def test_live_revocation_blocks_validation(self):
        self.begin()
        self.engine.pack("example")
        self.output()
        self.review()
        with self.engine.store.tx() as db:
            db.execute("UPDATE releases SET revoked=1 WHERE id='builtin-v1'")
        with self.assertRaises(Rejected):
            self.engine.verify("example")
        self.assertNotEqual(self.engine.store.task("example")["status"], "completed")

    def test_workspace_keeps_comma_braces_inside_strings(self):
        from workflow_core.project import jsonc
        path = self.base / "quoted.code-workspace"
        path.write_text('{"label":"literal ,} and ,]", "folders": [],}', encoding="utf-8")
        self.assertEqual(jsonc(path)["label"], "literal ,} and ,]")

    def test_active_reservation_survives_relocation(self):
        self.begin()
        self.engine.close()
        moved = self.base / "moved-active"
        shutil.copytree(self.project, moved)
        self.engine = Engine(moved)
        with self.assertRaisesRegex(Rejected, "already owned"):
            self.begin("second")


if __name__ == "__main__":
    unittest.main(verbosity=2)
