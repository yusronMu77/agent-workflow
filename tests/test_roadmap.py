"""Unit tests for roadmap features: each feature exists and behaves at its boundary, no native sessions."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import wf  # noqa: E402
from workflow_core import (benchmarks, broker, candidates, catalog, changes, dispatch, harness, health, jobs,  # noqa: E402
                           knowledge, recovery, routing, rulesync)
from workflow_core.engine import Engine, detect_host  # noqa: E402
from workflow_core.feedback import collect, commit_memory, decide, propose_memory  # noqa: E402
from workflow_core.outputs import Outputs  # noqa: E402
from workflow_core.project import Project, Rejected  # noqa: E402

PY = sys.executable
FAKE_MCP = r'''
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake"}}
    elif message.get("method") == "tools/list":
        result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-roadmap-")
        self.root = Path(self.temp.name).resolve()
        (self.root / "AGENTS.md").write_text("Fixture rules.\n", encoding="utf-8")
        Project.init(self.root)
        self.engine = Engine(self.root)

    def tearDown(self):
        for job in jobs.listing(self.engine).values():
            if job["state"] == "running":
                jobs.stop(self.engine, job["id"], "test cleanup")
        self.engine.close()
        self.temp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def task(self, task_id="t", checks=("capture_exit_zero",), output="out.py"):
        self.engine.create(task_id, "Fixture task", "code", "project:" + output, checks=checks)
        self.engine.pack(task_id, host="none")
        self.write(output, "print(1)\n")
        return task_id

    def cli_raw(self, *argv):
        buffer = io.StringIO()
        self.engine.close()
        try:
            with redirect_stdout(buffer):
                code = wf.main(["--project", str(self.root), *argv])
        finally:
            self.engine = Engine(self.root)
        return code, buffer.getvalue()

    def cli(self, *argv):
        code, output = self.cli_raw(*argv)
        return code, json.loads(output or "{}")

    def kinds(self):
        return [r[0] for r in self.engine.store.connection.execute("SELECT kind FROM proposals")]


class HarnessTests(Base):
    def test_exit_code_comes_from_process_and_binds_output(self):
        task = self.task(checks=("harness_exit_zero",))
        harness.configure(self.engine, "default", [PY, "-c", "import sys; sys.exit(0)"])
        Outputs(self.engine).capture(task, "project:out.py", 0)  # caller-reported, not harness
        before = {c["criterion"]: c["result"] for c in self.engine.verify(task)["result"]["checks"]}
        self.assertEqual(before["harness_exit_zero"], "not_checked")
        result = harness.run_check(self.engine, task)
        self.assertEqual((result["status"], result["verifier"]), ("passed", "harness_process"))
        after = {c["criterion"]: c["result"] for c in self.engine.verify(task)["result"]["checks"]}
        self.assertEqual(after["harness_exit_zero"], "passed")

    def test_failing_check_and_stop_hook(self):
        task = self.task()
        harness.configure(self.engine, "default", [PY, "-c", "print('boom'); raise SystemExit(4)"])
        self.assertEqual(harness.run_check(self.engine, task)["exit_code"], 4)
        decision = harness.stop_hook(self.engine, {})
        self.assertEqual(decision["decision"], "block")
        self.assertIn("exit 4", decision["reason"])
        self.assertEqual(harness.stop_hook(self.engine, {"stop_hook_active": True}), {})

    def counting_check(self, fails_when):
        """Check that appends one line per run to runs.txt and fails while notes/bad.md exists."""
        return [PY, "-c", "import pathlib, sys; p = pathlib.Path('runs.txt'); "
                "p.write_text(p.read_text() + 'x' if p.exists() else 'x'); "
                f"sys.exit(3 if pathlib.Path('{fails_when}').exists() else 0)"]

    def runs(self):
        path = self.root / "runs.txt"
        return len(path.read_text()) if path.exists() else 0

    def test_native_mode_reruns_only_when_watched_files_change(self):
        harness.configure(self.engine, "default", self.counting_check("notes/bad.md"), watch=["notes/*.md"])
        self.write("notes/a.md", "a\n")
        self.assertEqual(harness.stop_hook(self.engine, {}), {})
        self.assertEqual(self.runs(), 1)
        self.assertEqual(harness.stop_hook(self.engine, {}), {})  # unchanged: not rerun
        self.assertEqual(self.runs(), 1)
        self.write("notes/bad.md", "b\n")
        decision = harness.stop_hook(self.engine, {})
        self.assertEqual(decision["decision"], "block")
        self.assertIn("exit 3", decision["reason"])
        self.assertIn("watched files changed", decision["reason"])
        self.assertEqual(harness.stop_hook(self.engine, {}), {})  # same failure blocks once
        self.assertEqual(self.runs(), 2)
        (self.root / "notes/bad.md").unlink()
        self.assertEqual(harness.stop_hook(self.engine, {}), {})
        self.assertEqual(self.runs(), 3)

    def test_without_watch_or_task_the_hook_runs_nothing(self):
        harness.configure(self.engine, "default", self.counting_check("never"))
        self.assertEqual(harness.stop_hook(self.engine, {}), {})
        self.assertEqual(self.runs(), 0)

    def test_task_mode_runs_the_check_once_for_all_active_tasks(self):
        self.task("t1", checks=("harness_exit_zero",))
        self.task("t2", checks=("harness_exit_zero",), output="out2.py")
        harness.configure(self.engine, "default", self.counting_check("never"))
        self.assertEqual(harness.stop_hook(self.engine, {}), {})
        self.assertEqual(self.runs(), 1)
        for task in ("t1", "t2"):
            checks = {c["criterion"]: c["result"] for c in self.engine.verify(task)["result"]["checks"]}
            self.assertEqual(checks["harness_exit_zero"], "passed")

    def test_watch_patterns_stay_inside_the_project(self):
        for pattern in ("../x/*.md", "/etc/*", "C:/x/*", "..\\x\\*.md"):
            with self.assertRaises(Rejected):
                harness.configure(self.engine, "default", [PY, "-c", "pass"], watch=[pattern])
        code, result = self.cli("harness", "configure", "default", "--watch", "notes/*.md", "--", PY, "-c", "pass")
        self.assertEqual((code, result["watch"]), (0, ["notes/*.md"]))

    def test_hook_cli_is_silent_when_allowing_and_fails_open(self):
        with patch("sys.stdin", io.StringIO("{}")):
            code, output = self.cli_raw("harness", "hook")
        self.assertEqual((code, output), (0, ""))
        harness.configure(self.engine, "default", [PY, "-c", "pass"])
        self.engine.close()
        (self.root / ".agent-workflow" / "harness.json").write_text("{broken", encoding="utf-8")
        self.engine = Engine(self.root)
        with patch("sys.stdin", io.StringIO("{}")), redirect_stderr(io.StringIO()) as errors:
            code, output = self.cli_raw("harness", "hook")
        self.assertEqual((code, output), (0, ""))
        self.assertIn("fail-open", errors.getvalue())

    def test_cli_options_before_separator_are_not_part_of_argv(self):
        code, result = self.cli("harness", "configure", "default", "--timeout", "300", "--", PY, "-X", "utf8", "-c", "pass")
        self.assertEqual((code, result["timeout"], result["argv"]), (0, 300, [PY, "-X", "utf8", "-c", "pass"]))

    def test_finish_run_needs_no_reported_exit_code(self):
        self.task()
        harness.configure(self.engine, "default", [PY, "-c", "pass"])
        code, result = self.cli("finish", "t", "--run", "--review-result", "passed", "--review-note", "fixture reviewed")
        self.assertEqual((code, result["status"]), (0, "completed"))
        self.assertEqual(result["check_log"]["verifier"], "harness_process")

    def test_codex_host_detection_and_override(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "x"}, clear=False):
            os.environ.pop("CLAUDECODE", None)
            os.environ.pop("AGENT_WORKFLOW_HOST", None)
            self.assertEqual(detect_host(), "codex")
            os.environ["AGENT_WORKFLOW_HOST"] = "none"
            self.assertEqual(detect_host(), "none")


class RoutingTests(Base):
    def test_correction_produces_route_corrected_proposal(self):
        chosen = routing.record(self.engine.store, None, "web_search_exa", ["github_search"], "need recent pages")
        routing.correct(self.engine.store, chosen["event_id"], "github_search", "repo question")
        collect(self.engine.store)
        self.assertIn("route_corrected", self.kinds())
        self.assertEqual(routing.history(self.engine.store)["corrected"], 1)
        with self.assertRaises(Rejected):
            routing.correct(self.engine.store, chosen["event_id"], "web_search_exa", "same route")

    def test_selector_baseline_and_external(self):
        routes = {"exa": "semantic web search recent pages", "scholar": "academic paper citations"}
        self.assertEqual(routing.select(self.engine, "find academic paper citations", routes)["route"], "scholar")
        script = self.write("selector.py", "import json,sys; json.load(sys.stdin); print(json.dumps({'route':'exa','confidence':0.9}))\n")
        (self.root / ".agent-workflow" / "selector.json").write_text(json.dumps({"argv": [PY, str(script)], "name": "qwen"}), encoding="utf-8")
        picked = routing.select(self.engine, "find academic paper citations", routes)
        self.assertEqual((picked["route"], picked["selector"]), ("exa", "external:qwen"))


class CatalogHealthTests(Base):
    def test_catalog_match_and_gap(self):
        self.write("skills/pdf/SKILL.md", "---\nname: pdf\ndescription: Read and merge PDF files\n---\n")
        self.write("mcp.json", json.dumps({"mcpServers": {"exa": {"url": "https://example.invalid/mcp"}}}))
        found = catalog.research(self.engine.store, "merge pdf files", [self.root / "skills"], [self.root / "mcp.json"])
        self.assertEqual((found["status"], found["matches"][0]["name"]), ("gap_researched", "pdf"))
        gap = catalog.research(self.engine.store, "render blender scene", [self.root / "skills"])
        self.assertEqual(gap["status"], "capability_gap")
        self.assertEqual(len(gap["web_queries"]), 3)
        collect(self.engine.store)
        self.assertIn("capability_gap", self.kinds())

    def test_health_static_auth_and_stdio_probe(self):
        server = self.write("fake_mcp.py", FAKE_MCP)
        config = self.write("mcp.json", json.dumps({"mcpServers": {
            "fake": {"command": PY, "args": [str(server)]},
            "missing": {"command": "no-such-binary-xyz"},
            "keyed": {"url": "https://example.invalid/mcp?key=${WF_TEST_KEY}", "env": {"TOKEN": "literal"}}}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WF_TEST_KEY", None)
            report = health.check([config], probe=True, timeout=20)
        servers = {s["server"]: s for s in report["servers"]}
        self.assertEqual((servers["fake"]["probe"], servers["fake"]["tools"]), ("ok", 1))
        self.assertEqual(servers["missing"]["config"], "command_missing")
        self.assertEqual(servers["keyed"]["auth_variables"], {"WF_TEST_KEY": False})
        self.assertEqual(servers["keyed"]["literal_env_keys"], ["TOKEN"])
        self.assertNotIn("literal", json.dumps(report).replace("literal_env_keys", ""))
        self.assertEqual(report["status"], "degraded")


class BenchmarkTests(Base):
    def test_import_is_idempotent_and_failures_become_proposals(self):
        self.write("bench.json", json.dumps({"results": [{"id": "a", "success": True, "seconds": 3},
                                                         {"id": "b", "exit_code": 2}]}))
        first = benchmarks.import_file(self.engine, "project:bench.json")
        self.assertEqual((first["imported"], first["failed"]), (2, 1))
        self.assertEqual(benchmarks.import_file(self.engine, "project:bench.json")["already_imported"], 2)
        collect(self.engine.store)
        self.assertEqual(self.kinds(), ["benchmark_failed"])


class JobBrokerTests(Base):
    def wait(self, job_id, state, seconds=20):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if jobs.listing(self.engine)[job_id]["state"] == state:
                return True
            time.sleep(0.2)
        return False

    def test_owned_job_finishes_with_real_exit_code(self):
        job = jobs.start(self.engine, None, [PY, "-c", "print('hi'); raise SystemExit(3)"])
        self.assertTrue(self.wait(job["id"], "finished"))
        jobs.reap(self.engine)
        self.assertEqual(jobs.listing(self.engine)[job["id"]]["exit_code"], 3)

    def test_admission_and_stop(self):
        (self.root / ".agent-workflow" / "host.json").write_text(json.dumps({"max_jobs": 1, "min_free_mb": 0}), encoding="utf-8")
        job = jobs.start(self.engine, None, [PY, "-c", "import time; time.sleep(60)"])
        with self.assertRaisesRegex(Rejected, "admission"):
            jobs.start(self.engine, None, [PY, "-c", "pass"])
        jobs.stop(self.engine, job["id"], "done")
        self.assertEqual(jobs.listing(self.engine)[job["id"]]["state"], "stopped")
        with self.assertRaises(Rejected):
            jobs.stop(self.engine, "job-unknown", "not ours")

    def test_broker_lease_release_evict(self):
        broker.register(self.engine, "local", [PY, "-c", "import time; time.sleep(60)"], "http://127.0.0.1:9/mcp")
        first = broker.lease(self.engine, "local")
        second = broker.lease(self.engine, "local")
        self.assertTrue(first["started"])
        self.assertEqual((second["started"], second["job"]), (False, first["job"]))
        self.assertEqual(broker.evict(self.engine, 0)["evicted"], [])
        broker.release(self.engine, first["lease"])
        broker.release(self.engine, second["lease"])
        self.assertEqual(broker.evict(self.engine, 0)["evicted"], ["local"])
        with self.assertRaises(Rejected):
            broker.register(self.engine, "remote", [PY], "https://example.com/mcp")


class DispatchTests(Base):
    def test_unqualified_profile_falls_back_to_manual_handoff(self):
        self.engine.create("d", "Dispatch fixture", "code", "project:out.py")
        self.assertEqual(set(dispatch.profiles(self.engine)) >= {"claude", "codex", "acpx"}, True)
        result = dispatch.dispatch(self.engine, "d", "codex", execute=True)
        self.assertEqual((result["status"], result["reason"]), ("manual_handoff", "profile not qualified"))
        self.assertTrue(Path(result["context_path"]).is_file())

    def test_qualified_profile_runs_as_owned_job(self):
        self.engine.create("d", "Dispatch fixture", "code", "project:out.py")
        self.write("proof.txt", "native trial passed\n")
        agent = self.write("agent.py", "import sys; open('dispatched.txt','w').write(open(sys.argv[1]).read()[:10])\n")
        dispatch.qualify(self.engine, "fake", "project:proof.txt", [PY, str(agent), "{context_file}"])
        result = dispatch.dispatch(self.engine, "d", "fake", execute=True)
        self.assertEqual(result["status"], "dispatched")
        deadline = time.time() + 20
        while not (self.root / "dispatched.txt").exists() and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue((self.root / "dispatched.txt").exists())


class ChangeTests(Base):
    def setUp(self):
        super().setUp()
        self.write("config.json", '{"a": 1}\n')
        self.write("new.json", '{"a": 2}\n')
        self.write("review.txt", "Reviewed diff a=1 -> a=2\n")

    def staged(self, **kw):
        change = changes.propose(self.engine, "mcp", "project:config.json", "project:new.json", "raise a", **kw)
        self.assertTrue(changes.evaluate(self.engine, change["id"], [PY, "-c", "import json,os; json.load(open(os.environ['AGENT_WORKFLOW_SHADOW']))"])["passed"])
        return change["id"]

    def test_promotion_activation_and_rollback(self):
        change_id = self.staged()
        with self.assertRaises(Rejected):
            changes.activate(self.engine, change_id)
        changes.review(self.engine, change_id, "passed", "project:review.txt", "checked")
        self.assertEqual((self.root / "config.json").read_text(encoding="utf-8"), '{"a": 1}\n')
        changes.activate(self.engine, change_id)
        self.assertEqual((self.root / "config.json").read_text(encoding="utf-8"), '{"a": 2}\n')
        changes.rollback(self.engine, change_id, "undo")
        self.assertEqual((self.root / "config.json").read_text(encoding="utf-8"), '{"a": 1}\n')

    def test_failed_post_check_rolls_back_and_drift_blocks(self):
        change_id = self.staged()
        changes.review(self.engine, change_id, "passed", "project:review.txt", "checked")
        result = changes.activate(self.engine, change_id, [PY, "-c", "raise SystemExit(1)"])
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual((self.root / "config.json").read_text(encoding="utf-8"), '{"a": 1}\n')
        drift = self.staged()
        changes.review(self.engine, drift, "passed", "project:review.txt", "checked")
        self.write("config.json", '{"a": 9}\n')
        with self.assertRaisesRegex(Rejected, "changed"):
            changes.activate(self.engine, drift)

    def test_invalid_json_and_unaccepted_proposal(self):
        self.write("bad.json", "{oops\n")
        bad = changes.propose(self.engine, "config", "project:config.json", "project:bad.json", "broken")
        self.assertFalse(changes.evaluate(self.engine, bad["id"])["passed"])
        self.write("x.txt", "fixture\n")
        with self.assertRaises(Rejected):
            changes.propose(self.engine, "rules", "project:config.json", "project:new.json", "n", proposal_id="missing")


class CandidateTests(Base):
    def test_trial_install_and_automatic_rollback(self):
        source = Path(tempfile.mkdtemp(prefix="workflow-candidate-src-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(source, ignore_errors=True))
        (source / "tool.py").write_text("print('ok')\n", encoding="utf-8")
        probe = '["' + PY.replace("\\", "/") + '", "{candidate}/tool.py"]'
        result = candidates.trial(self.engine, source, None, json.loads(probe))
        self.assertEqual(result["status"], "passed")
        installed = candidates.install(self.engine, result["id"], "project:installed", [PY, "-c", "raise SystemExit(2)"])
        self.assertEqual(installed["status"], "rolled_back")
        self.assertFalse((self.root / "installed").exists())
        failing = candidates.trial(self.engine, source, None, [PY, "-c", "raise SystemExit(5)"])
        self.assertEqual(failing["status"], "failed")
        with self.assertRaises(Rejected):
            candidates.install(self.engine, failing["id"], "project:other")


class MemoryRecoveryTests(Base):
    def test_semantic_search_and_shadow_export(self):
        self.write("notes.md", "SQLite WAL keeps readers concurrent.\nPNG previews need a renderer.\n")
        for quote in ("SQLite WAL keeps readers concurrent.", "PNG previews need a renderer."):
            commit_memory(self.engine.store, propose_memory(self.engine.store, "project:notes.md", quote)["id"])
        hits = knowledge.semantic(self.engine.store, "concurrent sqlite readers")["hits"]
        self.assertEqual(hits[0]["quote"], "SQLite WAL keeps readers concurrent.")
        shadow = Path(tempfile.mkdtemp(prefix="workflow-shadow-")) / "bm"
        exported = knowledge.shadow_export(self.engine.store, shadow)
        self.assertEqual(exported["notes"], 2)
        self.assertIn("type: note", next(shadow.glob("*.md")).read_text(encoding="utf-8"))

    def test_restore_verified_backup(self):
        backup = Path(tempfile.mkdtemp(prefix="workflow-backup-")) / "state.sqlite"
        self.assertEqual(self.cli("backup", "--output", str(backup))[0], 0)
        self.engine.create("after", "Created after backup", "code", "project:after.py")
        self.engine.close()
        self.assertEqual(recovery.restore(self.root, auto=True, backups=backup.parent)["status"], "healthy_no_restore")
        restored = recovery.restore(self.root, source=backup)
        self.assertEqual(restored["status"], "restored")
        self.assertTrue(Path(restored["preserved_previous_state"]).is_file())
        self.engine = Engine(self.root)
        with self.assertRaises(Rejected):
            self.engine.store.task("after")
        other = Path(tempfile.mkdtemp(prefix="workflow-other-"))
        sqlite3.connect(other / "x.sqlite").execute("CREATE TABLE meta(key TEXT, value TEXT)").connection.close()
        self.assertFalse(recovery.inspect(Project(self.root), other / "x.sqlite")["valid"])


class RulesyncBudgetWorkerTests(Base):
    def test_rulesync_dry_run_never_writes_project(self):
        script = Path(tempfile.mkdtemp(prefix="workflow-rulesync-script-")) / "gen.py"
        script.write_text("open('GENERATED.md','w').write('x')\n", encoding="utf-8")
        result = rulesync.dry_run(self.root, [PY, str(script)])
        self.assertEqual((result["status"], result["created"]), ("dry_run_passed", ["GENERATED.md"]))
        self.assertFalse((self.root / "GENERATED.md").exists())

    def test_budget_and_worker_once(self):
        self.task()
        code, result = self.cli("budget", "t")
        self.assertEqual(code, 0)
        self.assertEqual(result["task"]["packs"], 1)
        self.assertEqual(result["defaults"]["e2e_minutes"], 240)
        import worker
        self.engine.close()
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                self.assertEqual(worker.main(["--project", str(self.root)]), 0)
            lock = self.root / ".agent-workflow" / "worker.lock"
            lock.write_text(str(os.getpid()), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(worker.main(["--project", str(self.root)]), 3)
            lock.unlink()
        finally:
            self.engine = Engine(self.root)
        self.assertTrue((self.root / ".agent-workflow" / "worker" / "last.json").is_file())

    def test_status_ui_snapshot_is_read_only(self):
        import status_ui
        self.task()
        self.engine.close()
        try:
            data = status_ui.snapshot(self.root)
            self.assertEqual(data["tasks"][0]["id"], "t")
            self.assertIn(b"read-only", status_ui.render(data))
        finally:
            self.engine = Engine(self.root)


class FakeOpenAI:
    """A local OpenAI-compatible endpoint that answers every chat completion with fixed text."""
    def __init__(self, text):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 7}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ModelGatewayAcceptanceTests(Base):
    def test_model_draft_is_evidence_only(self):
        from workflow_core import models
        fake = FakeOpenAI("draft body")
        self.addCleanup(fake.close)
        models.register(self.engine, "local", fake.url, "qwen")
        self.engine.create("m", "Model fixture", "code", "project:out.py")
        result = models.draft(self.engine, "m", "local")
        self.assertEqual(result["status"], "drafted")
        self.assertFalse((self.root / "out.py").exists())
        self.assertIn("Model fixture", fake.seen[0]["messages"][1]["content"])
        with self.assertRaises(Rejected):
            models.register(self.engine, "bad", fake.url, "x", api_key_env="sk-not-a-name!")

    def test_model_backed_selector(self):
        from workflow_core import models
        fake = FakeOpenAI('{"route": "scholar", "confidence": 0.8}')
        self.addCleanup(fake.close)
        models.register(self.engine, "qwen", fake.url, "qwen")
        (self.root / ".agent-workflow" / "selector.json").write_text(json.dumps({"model": "qwen"}), encoding="utf-8")
        picked = routing.select(self.engine, "cari paper", {"exa": "web search", "scholar": "papers"})
        self.assertEqual((picked["route"], picked["selector"]), ("scholar", "external:qwen"))

    def test_gateway_requires_token_and_exposes_no_writes(self):
        import gateway
        from http.server import ThreadingHTTPServer
        import threading
        import urllib.error
        import urllib.request
        self.task()
        server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.handler(self.root, "x" * 20))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/"

        def post(operation, token):
            request = urllib.request.Request(url, json.dumps({"operation": operation}).encode(),
                                             {"Authorization": "Bearer " + token}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())
        self.assertEqual(post("list", "wrong")[0], 401)
        status, body = post("list", "x" * 20)
        self.assertEqual((status, body["result"]["tasks"][0]["id"]), (200, "t"))
        self.assertEqual(post("change_activate", "x" * 20)[0], 400)
        self.assertNotIn("restore", gateway.OPERATIONS)

    def test_acceptance_report_over_recorded_tasks(self):
        self.task()
        harness.configure(self.engine, "default", [PY, "-c", "pass"])
        self.cli("finish", "t", "--run", "--review-result", "passed", "--review-note", "fixture")
        code, report = self.cli("acceptance")
        self.assertEqual((code, report["acceptance_rate"], report["harness_verified_completed"]), (0, 1.0, 1))


if __name__ == "__main__":
    unittest.main()
