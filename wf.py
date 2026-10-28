#!/usr/bin/env python3
"""Use from a native agent terminal: durable workflow, context, validation and feedback."""
import argparse
import json
from pathlib import Path
import sqlite3
import shutil
import sys

from workflow_core.engine import AUTOLOAD, CHECKS, Engine, WORKFLOWS
from workflow_core.feedback import collect, commit_memory, decide, propose_memory, recall
from workflow_core.project import Project, Rejected, digest
from workflow_core.state import Store
from workflow_core.outputs import Outputs
from workflow_core import cli_ext, harness, recovery


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=(Path(__file__).resolve().parent / 'VERSION').read_text().strip())
    parser.add_argument("--project", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init")
    setup.add_argument("--root", action="append", default=[])
    setup.add_argument("--workspace", type=Path)
    commands.add_parser("doctor")
    commands.add_parser("list")
    capture = commands.add_parser("capture", help="Archive a UTF-8 tool output file and return a bounded view; never runs commands")
    capture.add_argument("task")
    capture.add_argument("--input", required=True, help="registered-alias:relative/file")
    capture.add_argument("--exit-code", type=int)
    capture.add_argument("--max-bytes", type=int, default=6000)
    capture.add_argument("--find", help="Literal case-insensitive text, not regex")
    read = commands.add_parser("read-output", help="Retrieve bounded exact lines without rerunning a command")
    read.add_argument("task")
    read.add_argument("key")
    read.add_argument("--start", type=int, default=1)
    read.add_argument("--end", type=int)
    read.add_argument("--find")
    read.add_argument("--column", type=int, help="1-based Unicode character column on --start line; follow next_column until null")
    read.add_argument("--max-bytes", type=int, default=6000)
    metrics = commands.add_parser("metrics", help="Observed payload bytes; native tokens/cost remain unknown")
    metrics.add_argument("task")
    for command in ("begin", "start"):
        new = commands.add_parser(command)
        new.add_argument("task")
        new.add_argument("--request", required=True)
        new.add_argument("--workflow", choices=WORKFLOWS, required=True)
        new.add_argument("--output", required=True)
        new.add_argument("--source", action="append", default=[], help="alias:path:start:end; inclusive 1-based UTF-8 line range")
        new.add_argument("--require", action="append", default=[], help="Literal substring required in output; use --review-required for semantic acceptance")
        new.add_argument("--check", action="append", default=[], choices=list(CHECKS),
                         help="capture_exit_zero needs a capture --exit-code 0 made while the output had its current hash")
        new.add_argument("--minutes", type=int, help="Task deadline in minutes (default: project budget)")
        new.add_argument("--review-required", action="append", default=[])
        if command == "start":
            new.add_argument("--init", action="store_true", dest="initialize", help="Explicitly initialize a new single-root project if needed")
            new.add_argument("--host", choices=sorted(AUTOLOAD))
    finish = commands.add_parser("finish", help="Capture a real check, record explicit native review, then run all normal validators")
    finish.add_argument("task")
    finish.add_argument("--input", help="Caller-saved check log; omit with --run")
    finish.add_argument("--exit-code", type=int, help="Caller-reported exit code; omit with --run")
    finish.add_argument("--run", action="store_true", help="Run the configured harness check; exit code read from the process")
    finish.add_argument("--check", help="Harness check name for --run (default: default)")
    finish.add_argument("--review-result", choices=("passed", "failed"), required=True)
    finish.add_argument("--review-note", required=True, help="Explicit attestation after inspecting artifact and actual check output")
    for name in ("pack", "show", "resume", "verify", "checkpoint", "evidence", "review", "unknown", "reconcile", "cancel", "rebind"):
        item = commands.add_parser(name)
        item.add_argument("task")
        if name == "pack":
            item.add_argument("--host", choices=sorted(AUTOLOAD), help="Reference instruction files this host autoloads instead of repeating them (default: detected; none inlines all)")
            item.add_argument("--audience", choices=("current", "subagent-review"), default="current",
                              help="subagent-review includes every policy and a read-only review contract; never dispatches")
        if name == "checkpoint":
            item.add_argument("--summary", required=True)
            item.add_argument("--next", required=True)
        if name == "evidence":
            item.add_argument("--name", required=True)
            item.add_argument("--text", required=True)
        if name == "review":
            item.add_argument("--criterion", default="final_review")
            item.add_argument("--result", choices=["passed", "failed"], required=True)
            item.add_argument("--evidence", required=True)
            item.add_argument("--note", required=True)
        if name in ("unknown", "cancel", "reconcile", "rebind"):
            item.add_argument("--note", required=True)
        if name == "reconcile":
            item.add_argument("--outcome", choices=["succeeded", "failed"], required=True)
            item.add_argument("--evidence", required=True)
        if name == "cancel":
            item.add_argument("--quiescent", action="store_true", help="Explicit native owner attests jobs/effects are settled; does not kill processes")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("--collect", action="store_true")
    feedback.add_argument("--decide", metavar="PROPOSAL_ID")
    feedback.add_argument("--decision", choices=("accept", "reject"))
    feedback.add_argument("--evidence")
    feedback.add_argument("--note")
    memory = commands.add_parser("memory")
    sub = memory.add_subparsers(dest="memory_command", required=True)
    candidate = sub.add_parser("propose")
    candidate.add_argument("--source", required=True)
    candidate.add_argument("--quote", required=True)
    verify = sub.add_parser("verify-quote")
    verify.add_argument("id")
    search = sub.add_parser("search")
    search.add_argument("--query", default="")
    search.add_argument("--semantic", action="store_true", help="Rank fresh verified quotes by TF-IDF cosine")
    shadow = sub.add_parser("shadow-export", help="Copy verified quotes into a new Basic Memory folder")
    shadow.add_argument("--output", type=Path, required=True)
    release = commands.add_parser("revoke")
    release.add_argument("release")
    release.add_argument("--reason", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore", help="Restore verified DB backup; current state is preserved first")
    restore.add_argument("--from", dest="source", type=Path)
    restore.add_argument("--backups", type=Path, help="Folder of *.sqlite backups; newest valid is chosen")
    restore.add_argument("--auto", action="store_true", help="Only restore when live quick_check fails")
    cli_ext.register(commands)
    args = parser.parse_args(argv)
    hook = args.command == "harness" and getattr(args, "action", None) == "hook"
    engine = None
    try:
        if args.command == "init":
            project, created = Project.init(args.project, args.root, args.workspace)
            store = Store(project)
            store.close()
            result = {"status": "initialized" if created else "already_initialized", "project_id": project.config["project_id"],
                      "roots": project.config["roots"], "state": str(project.db), "native_dispatch": "current_chat"}
        elif args.command == "restore":
            result = recovery.restore(args.project, args.source, args.backups, args.auto)
        else:
            if args.command == "start" and args.initialize:
                Project.init(args.project)
            engine = Engine(args.project)
            if args.command == "doctor":
                check = engine.store.connection.execute("PRAGMA quick_check").fetchone()[0]
                result = {"status": "ok" if check == "ok" else "unhealthy", "database_check": check,
                          "schema": int(engine.store.connection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0]),
                          "project_id": engine.project.config["project_id"],
                          "roots": {k: str(v) for k, v in engine.project.roots.items()},
                          "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                          "version": (Path(__file__).resolve().parent / 'VERSION').read_text().strip(),
                          "tools": {name: bool(shutil.which(name)) for name in ('git', 'codex', 'claude', 'rtk')},
                          "budget": engine.project.config["budget"], "feedback_mode": "observe_propose",
                          "native_dispatch": "current_chat_via_terminal", "native_tokens": None,
                          "optional_adapters": "not_required_not_qualified"}
            elif args.command == "begin":
                result = engine.create(args.task, args.request, args.workflow, args.output,
                                       args.source, args.require, args.check, args.review_required, args.minutes)
            elif args.command == "start":
                checks = list(dict.fromkeys([*args.check, "capture_exit_zero"]))
                engine.create(args.task, args.request, args.workflow, args.output,
                              args.source, args.require, checks, args.review_required, args.minutes)
                prepared = engine.pack(args.task, args.host, compact=True)
                context = Path(prepared["context_path"]).read_text(encoding="utf-8")
                result = {"id": args.task, "status": prepared["status"], "context": context,
                          "context_path": prepared["context_path"], "authorization": "not_granted"}
            elif args.command == "finish":
                task = engine.guard(args.task)
                if task["status"] not in ("running", "repair_needed", "needs_review") or not args.review_note.strip():
                    raise Rejected("Finish requires a prepared active task and a concrete review note")
                if not any(c["kind"] == "capture_exit_zero" for c in task["spec"]["acceptance"]):
                    raise Rejected("Finish requires capture_exit_zero; use the manual review/verify path for older tasks")
                engine.evidence_snapshot(args.task, task["spec"]["output"])
                if args.run:
                    if args.input or args.exit_code is not None:
                        raise Rejected("--run reads the exit code from the process; do not also pass --input/--exit-code")
                    captured = harness.run_check(engine, args.task, args.check)["check_log"]
                elif args.input and args.exit_code is not None:
                    captured = Outputs(engine).capture(args.task, args.input, args.exit_code, budget=2048)
                else:
                    raise Rejected("Finish needs --run, or --input and --exit-code")
                engine.review(args.task, "final_review", args.review_result, "output:" + captured["key"], args.review_note)
                verified = engine.verify(args.task)
                result = {"id": args.task, "status": verified["status"],
                          "checks": [{"id": c["criterion"], "result": c["result"]} for c in verified["result"]["checks"]],
                          "output_sha256": verified["result"]["output"]["sha256"], "check_log": captured}
            elif args.command == "pack":
                result = engine.pack(args.task, args.host, args.audience)
            elif args.command == "capture":
                result = Outputs(engine).capture(args.task, args.input, args.exit_code, args.max_bytes, args.find)
            elif args.command == "read-output":
                result = Outputs(engine).read(args.task, args.key, args.max_bytes, args.start, args.end, args.find, args.column)
            elif args.command == "metrics":
                result = Outputs(engine).metrics(args.task)
            elif args.command == "show":
                result = engine.export(args.task)
            elif args.command == "resume":
                result = engine.resume(args.task)
            elif args.command == "checkpoint":
                result = engine.checkpoint(args.task, args.summary, args.next)
            elif args.command == "evidence":
                result = engine.evidence(args.task, args.name, args.text)
            elif args.command == "review":
                result = engine.review(args.task, args.criterion, args.result, args.evidence, args.note)
            elif args.command == "verify":
                result = engine.verify(args.task)
            elif args.command == "rebind":
                result = engine.rebind(args.task, args.note)
            elif args.command == "unknown":
                result = engine.unknown(args.task, args.note)
            elif args.command == "reconcile":
                result = engine.reconcile(args.task, args.outcome, args.evidence, args.note)
            elif args.command == "cancel":
                result = engine.cancel(args.task, args.note, args.quiescent)
            elif args.command == "list":
                result = {"tasks": [dict(row) for row in engine.store.connection.execute(
                    "SELECT id,status,revision,repairs,next_action FROM tasks ORDER BY updated DESC LIMIT 50")]}
            elif args.command == "feedback":
                if args.decide:
                    if args.collect or not all((args.decision, args.evidence, args.note)):
                        raise Rejected("Use --decide ID --decision accept|reject --evidence REF --note TEXT, separately from --collect")
                    result = decide(engine, args.decide, args.decision, args.evidence, args.note)
                else:
                    if args.decision or args.evidence or args.note:
                        raise Rejected("--decision/--evidence/--note require --decide")
                    result = collect(engine.store) if args.collect else {"status": "observed"}
                result["proposals"] = [{**dict(row), "payload": json.loads(row["payload"])}
                                       for row in engine.store.connection.execute("SELECT * FROM proposals ORDER BY id LIMIT 50")]
                result["pending_events"] = engine.store.connection.execute("SELECT count(*) FROM events WHERE delivered=0").fetchone()[0]
            elif args.command == "memory":
                if args.memory_command == "propose":
                    result = propose_memory(engine.store, args.source, args.quote)
                elif args.memory_command == "verify-quote":
                    result = commit_memory(engine.store, args.id)
                elif args.memory_command == "shadow-export" or args.semantic:
                    result = cli_ext.memory_extension(engine, args)
                else:
                    result = recall(engine.store, args.query)
            elif args.command == "revoke":
                if not args.reason.strip():
                    raise Rejected("A reason is required")
                with engine.store.tx() as db:
                    if not db.execute("SELECT id FROM releases WHERE id=?", (args.release,)).fetchone():
                        raise Rejected("Release not found")
                    db.execute("UPDATE releases SET revoked=1,reason=? WHERE id=?", (args.reason, args.release))
                    engine.store.event(db, None, "release_revoked", {"release": args.release, "reason": args.reason})
                result = {"status": "revoked", "release": args.release, "inflight_native_actions": "must_reconcile_in_chat"}
            elif args.command == "backup":
                output = args.output.resolve()
                if output.exists() or output.is_relative_to(engine.project.home):
                    raise Rejected("Choose a new backup outside live state")
                output.parent.mkdir(parents=True, exist_ok=True)
                target = sqlite3.connect(output)
                try:
                    engine.store.connection.backup(target)
                finally:
                    target.close()
                result = {"status": "backed_up", "path": str(output), "sha256": digest(output.read_bytes()),
                          "scope": "DB only; artifacts and canonical refs require separate backup",
                          "restore": "requires_revocation_freshness_and_requalification_no_automatic_restore"}
            elif args.command in cli_ext.COMMANDS:
                result = cli_ext.run(engine, args)
            else:
                raise Rejected("Unsupported command")
        if result or not hook:  # An allowing Stop hook prints nothing.
            print(json.dumps(result, ensure_ascii=True))
        return 3 if result.get("status") in ("partial", "repair_needed", "needs_review", "blocked_outcome") else 0
    except Exception as error:
        if hook:
            # A broken hook must never trap the host in a blocked turn: fail open, report on stderr.
            print(f"agent-workflow harness hook fail-open: {type(error).__name__}: {error}", file=sys.stderr)
            return 0
        if not isinstance(error, (OSError, ValueError, KeyError, sqlite3.Error)):
            raise
        print(json.dumps({"status": "rejected", "reason": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
    finally:
        if engine:
            engine.close()


if __name__ == "__main__":
    sys.exit(main())
