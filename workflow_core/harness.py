"""Harness-side verification: the controller runs the check and reads the exit code from the process."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath

from .outputs import Outputs
from .project import Rejected, plain_path
from .records import checked_name
from . import runner

ACTIVE = ("running", "repair_needed", "needs_review")
MAX_WATCHED = 5000


def config_path(engine):
    return plain_path(engine.project.home / "harness.json")


def load(engine):
    path = config_path(engine)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"checks": {}}


def state_path(engine):
    return plain_path(engine.project.home / "harness-state.json")


def configure(engine, name, argv, timeout=600, watch=()):
    checked_name(name, "check name")
    if not argv:
        raise Rejected("Give the check command after --, e.g. -- python -m unittest")
    for pattern in watch:
        parts = PurePosixPath(pattern.replace("\\", "/")).parts
        if not parts or parts[0] == "/" or ":" in pattern or ".." in parts:
            raise Rejected(f"Watch pattern must be relative to the project root: {pattern}")
    config = load(engine)
    config["checks"][name] = {"argv": list(argv), "timeout": timeout, **({"watch": list(watch)} if watch else {})}
    engine.write_owned(config_path(engine), (json.dumps(config, indent=2) + "\n").encode("utf-8"))
    return {"status": "configured", "check": name, "argv": list(argv), "timeout": timeout, "watch": list(watch),
            "runs": "only when wf.py harness run/finish --run or the opt-in Stop hook invokes it; "
                    "the hook also runs it without an active task when watched files changed"}


def fingerprint(engine, patterns):
    """Content digest of the watched project files; folders count by name only."""
    root = engine.project.root
    paths = sorted({path for pattern in patterns for path in root.glob(pattern)
                    if ".agent-workflow" not in path.relative_to(root).parts})
    if len(paths) > MAX_WATCHED:
        raise Rejected(f"Watch patterns match {len(paths)} paths; the limit is {MAX_WATCHED}")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"folder")
    return digest.hexdigest()


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
    """Decide a host Stop hook: block while the configured check fails.

    The check runs once per Stop when an active task has an output (the result is captured for each
    such task), or, without one, when files matched by the check's watch patterns changed since its
    last run. Unchanged files are not rechecked, so a native-mode failure blocks once. Returns {} to
    allow; the CLI then prints nothing, as Claude Code and the Codex adapter expect."""
    name = name or "default"
    check = load(engine)["checks"].get(name)
    if payload.get("stop_hook_active") or not check:
        return {}
    rows = engine.store.connection.execute(
        "SELECT id FROM tasks WHERE status IN (?,?,?) ORDER BY updated DESC LIMIT 5", ACTIVE).fetchall()
    tasks = [row["id"] for row in rows
             if engine.project.resolve(engine.store.task(row["id"])["spec"]["output"]).is_file()]
    watch = check.get("watch") or []
    path = state_path(engine)
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not tasks and (not watch or fingerprint(engine, watch) == state.get(name, {}).get("fingerprint")):
        return {}
    result = runner.run(check["argv"], cwd=engine.project.root, timeout=check.get("timeout", 600))
    for task in tasks:
        try:
            Outputs(engine).capture_process(task, result)
        except Rejected:
            pass
    # Fingerprint after the run, so files the check itself writes do not trigger a rerun.
    state[name] = {"fingerprint": fingerprint(engine, watch) if watch else None, "exit_code": result["exit_code"],
                   "timed_out": result["timed_out"], "tasks": tasks,
                   "checked": datetime.now(timezone.utc).isoformat()}
    engine.write_owned(path, (json.dumps(state, indent=2) + "\n").encode("utf-8"))
    if result["exit_code"] == 0:
        return {}
    tail = [line for line in result["output"].decode("utf-8").splitlines() if line.strip()][-5:]
    scope = "tasks " + ", ".join(tasks) if tasks else "watched files changed"
    status = "timed out" if result["timed_out"] else f"exit {result['exit_code']}"
    return {"decision": "block", "reason": f"Harness check '{name}' failed ({status}, read from the process; "
                                           f"{scope}): " + " | ".join(tail)}
