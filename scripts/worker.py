"""Background self-improvement worker: collect feedback, reap jobs, evict idle MCP, check health.

Observe/propose only: it never activates a change, installs a candidate or dispatches an agent.
Run once from a scheduler (Task Scheduler/cron) or loop with --interval. One worker per project.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core import broker, health, jobs  # noqa: E402
from workflow_core.engine import Engine  # noqa: E402
from workflow_core.feedback import collect  # noqa: E402
from workflow_core.project import Project, plain_path  # noqa: E402
from workflow_core.records import emit  # noqa: E402


def acquire(lock):
    """Exclusive lock file holding our pid; a lock left by a dead worker is replaced."""
    for _ in range(2):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = int(lock.read_text(encoding="utf-8") or 0)
            except (OSError, ValueError):
                owner = 0
            if owner and jobs.alive(owner):
                return False
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        return True
    return False


def cycle(project, configs, probe, idle_minutes):
    engine = Engine(project)
    try:
        summary = {"feedback": collect(engine.store), "jobs": jobs.reap(engine)}
        if broker.config(engine)["servers"]:
            summary["mcp"] = broker.evict(engine, idle_minutes)
        if configs:
            report = health.check(configs, probe)
            summary["health"] = {"status": report["status"], "unhealthy": report["unhealthy"]}
        emit(engine.store, None, "worker_cycle", {k: v.get("status") for k, v in summary.items()})
        summary["auto_apply"] = False
        out = plain_path(engine.project.home / "worker" / "last.json")
        engine.write_owned(out, (json.dumps(summary, indent=2) + "\n").encode("utf-8"))
        return summary
    finally:
        engine.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--interval", type=int, help="Seconds between cycles; omit to run once")
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--mcp-config", action="append", default=[])
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--idle-minutes", type=int, default=15)
    args = parser.parse_args(argv)
    project = Project(args.project)
    lock = plain_path(project.home / "worker.lock")
    if not acquire(lock):
        print(json.dumps({"status": "already_running"}))
        return 3
    try:
        count = 0
        while True:
            summary = cycle(args.project, args.mcp_config, args.probe, args.idle_minutes)
            count += 1
            print(json.dumps({"status": "cycle_done", "cycle": count, **summary}, ensure_ascii=True))
            if not args.interval or (args.max_cycles and count >= args.max_cycles):
                return 0
            time.sleep(args.interval)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
