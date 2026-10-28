"""Harness-side verification: the controller runs the check and reads the exit code from the process."""
import json

from .outputs import Outputs
from .project import Rejected, plain_path
from .records import checked_name
from . import runner

ACTIVE = ("running", "repair_needed", "needs_review")


def config_path(engine):
    return plain_path(engine.project.home / "harness.json")


def load(engine):
    path = config_path(engine)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"checks": {}}


def configure(engine, name, argv, timeout=600):
    checked_name(name, "check name")
    if not argv:
        raise Rejected("Give the check command after --, e.g. -- python -m unittest")
    config = load(engine)
    config["checks"][name] = {"argv": list(argv), "timeout": timeout}
    engine.write_owned(config_path(engine), (json.dumps(config, indent=2) + "\n").encode("utf-8"))
    return {"status": "configured", "check": name, "argv": list(argv), "timeout": timeout,
            "runs": "only when wf.py harness run/finish --run or the opt-in Stop hook invokes it"}


def resolve(engine, name=None, argv=None):
    if argv:
        return list(argv), 600
    check = load(engine)["checks"].get(name or "default")
    if not check:
        raise Rejected("No harness check configured; run harness configure first or pass -- argv")
    return check["argv"], check.get("timeout", 600)


def run_check(engine, task_id, name=None, argv=None, budget=2048):
    argv, timeout = resolve(engine, name, argv)
    engine.guard(task_id)
    result = runner.run(argv, cwd=engine.project.root, timeout=timeout)
    view = Outputs(engine).capture_process(task_id, result, budget=budget)
    return {"status": "passed" if result["exit_code"] == 0 else "failed", "exit_code": result["exit_code"],
            "timed_out": result["timed_out"], "verifier": "harness_process", "check_log": view}


def stop_hook(engine, payload, name=None):
    """Decide a host Stop hook: block once while a configured check fails for an active task.

    Returns {} to allow; the CLI then prints nothing, as Claude Code and the Codex adapter expect."""
    if payload.get("stop_hook_active") or not load(engine)["checks"]:
        return {}
    rows = engine.store.connection.execute(
        "SELECT id FROM tasks WHERE status IN (?,?,?) ORDER BY updated DESC LIMIT 5", ACTIVE).fetchall()
    failures = []
    for row in rows:
        task = engine.store.task(row["id"])
        if not engine.project.resolve(task["spec"]["output"]).is_file():
            continue
        try:
            outcome = run_check(engine, row["id"], name)
        except Rejected:
            continue
        if outcome["status"] != "passed":
            tail = outcome["check_log"].get("lines", [])[-5:]
            failures.append(f"{row['id']}: exit {outcome['exit_code']} " + " | ".join(l.get("text", "") for l in tail))
    if failures:
        return {"decision": "block", "reason": "Harness check failed (exit code read from the process): " + "; ".join(failures)}
    return {}
