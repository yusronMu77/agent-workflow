"""CLI wiring for roadmap features: harness, routing, catalog, health, candidates, benchmarks,
jobs, MCP broker, dispatch, staged changes, memory extensions, rulesync, restore and budget."""
from datetime import datetime, timezone
import json
import sys

from .project import Rejected
from . import (acceptance, benchmarks, broker, candidates, catalog, changes, dispatch, harness, health, jobs, knowledge,
               models, records, routing, rulesync)

COMMANDS = ("harness", "route", "catalog", "health", "candidate", "benchmark", "job", "mcp", "dispatch",
            "change", "rules", "budget", "model", "acceptance")


def rest(parser, help_text="command argv after --"):
    # nargs="*" (not REMAINDER) so options such as --timeout still parse before the -- separator.
    parser.add_argument("argv", nargs="*", help=help_text)


def argv_of(args):
    values = list(getattr(args, "argv", None) or [])
    return values[1:] if values[:1] == ["--"] else values


def json_argv(value):
    if value is None:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(p, str) for p in parsed):
        raise Rejected("Commands are JSON arrays of strings, e.g. [\"python\",\"-m\",\"unittest\"]")
    return parsed


def register(commands):
    item = commands.add_parser("harness", help="Run checks whose exit code the controller reads from the process")
    sub = item.add_subparsers(dest="action", required=True)
    configure = sub.add_parser("configure")
    configure.add_argument("name")
    configure.add_argument("--timeout", type=int, default=600)
    rest(configure)
    run = sub.add_parser("run")
    run.add_argument("task")
    run.add_argument("--check")
    rest(run)
    hook = sub.add_parser("hook", help="Host Stop hook: JSON payload on stdin, decision JSON on stdout")
    hook.add_argument("--check")

    item = commands.add_parser("route", help="Routing provenance and corrections")
    sub = item.add_subparsers(dest="action", required=True)
    record = sub.add_parser("record")
    record.add_argument("--task")
    record.add_argument("--chosen", required=True)
    record.add_argument("--candidate", action="append", default=[])
    record.add_argument("--reason", required=True)
    correct = sub.add_parser("correct")
    correct.add_argument("event_id")
    correct.add_argument("--to", required=True)
    correct.add_argument("--reason", required=True)
    history = sub.add_parser("history")
    history.add_argument("--task")
    select = sub.add_parser("select")
    select.add_argument("--request", required=True)
    select.add_argument("--routes", required=True, help="alias:path JSON {route: description}")
    select.add_argument("--task")

    item = commands.add_parser("catalog", help="Research installed skills/MCP for a capability gap")
    item.add_argument("--need", required=True)
    item.add_argument("--skills", action="append", default=[], help="Folder containing */SKILL.md")
    item.add_argument("--mcp-config", action="append", default=[])
    item.add_argument("--task")

    item = commands.add_parser("health", help="Per-MCP/tool health: config, auth presence, optional live probe")
    item.add_argument("--mcp-config", action="append", default=[])
    item.add_argument("--server", action="append", default=[])
    item.add_argument("--probe", action="store_true")
    item.add_argument("--timeout", type=int, default=15)

    item = commands.add_parser("candidate", help="Isolated candidate trial, install, rollback")
    sub = item.add_subparsers(dest="action", required=True)
    trial = sub.add_parser("trial")
    trial.add_argument("--source", required=True)
    trial.add_argument("--install-cmd")
    trial.add_argument("--trial-cmd", required=True)
    trial.add_argument("--timeout", type=int, default=600)
    install = sub.add_parser("install")
    install.add_argument("id")
    install.add_argument("--target", required=True)
    install.add_argument("--verify-cmd")
    back = sub.add_parser("rollback")
    back.add_argument("id")
    back.add_argument("--note", required=True)

    item = commands.add_parser("benchmark", help="Import benchmark results as events/proposals")
    item.add_argument("--input", required=True)
    item.add_argument("--task")

    item = commands.add_parser("job", help="Owned background jobs with host admission")
    sub = item.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start")
    start.add_argument("--task")
    start.add_argument("--label", default="job")
    rest(start)
    sub.add_parser("list")
    sub.add_parser("reap")
    sub.add_parser("admission")
    stop = sub.add_parser("stop")
    stop.add_argument("id")
    stop.add_argument("--note", required=True)

    item = commands.add_parser("mcp", help="MCP broker: leases per target, idle eviction")
    sub = item.add_subparsers(dest="action", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("name")
    reg.add_argument("--url", required=True)
    rest(reg)
    lease = sub.add_parser("lease")
    lease.add_argument("name")
    lease.add_argument("--task")
    release = sub.add_parser("release")
    release.add_argument("lease")
    evict = sub.add_parser("evict")
    evict.add_argument("--idle-minutes", type=int, default=15)

    item = commands.add_parser("dispatch", help="Hand a task to another agent CLI via a qualified profile")
    sub = item.add_subparsers(dest="action", required=True)
    send = sub.add_parser("send")
    send.add_argument("task")
    send.add_argument("--profile", required=True)
    send.add_argument("--execute", action="store_true")
    qualify = sub.add_parser("qualify")
    qualify.add_argument("profile")
    qualify.add_argument("--evidence", required=True)
    rest(qualify)
    sub.add_parser("profiles")

    item = commands.add_parser("change", help="Staged prompt/rules/MCP/hook changes with promotion and rollback")
    sub = item.add_subparsers(dest="action", required=True)
    propose = sub.add_parser("propose")
    propose.add_argument("--kind", required=True, choices=changes.KINDS)
    propose.add_argument("--target", required=True)
    propose.add_argument("--content", required=True, help="alias:path with the complete new content")
    propose.add_argument("--note", required=True)
    propose.add_argument("--proposal")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("id")
    rest(evaluate, "optional fresh-acceptance argv; AGENT_WORKFLOW_SHADOW names the shadow file")
    review = sub.add_parser("review")
    review.add_argument("id")
    review.add_argument("--result", choices=("passed", "failed"), required=True)
    review.add_argument("--evidence", required=True)
    review.add_argument("--note", required=True)
    activate = sub.add_parser("activate")
    activate.add_argument("id")
    rest(activate, "optional post-activation check; nonzero exit rolls back automatically")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("id")
    rollback.add_argument("--note", required=True)
    sub.add_parser("list")

    item = commands.add_parser("rules", help="Rulesync dry-run on a throwaway copy")
    item.add_argument("action", choices=("dry-run",))
    rest(item)

    item = commands.add_parser("budget", help="Task and end-to-end budget consumption")
    item.add_argument("task", nargs="?")

    item = commands.add_parser("model", help="Additional OpenAI-compatible model workers (draft only)")
    sub = item.add_subparsers(dest="action", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("name")
    reg.add_argument("--base-url", required=True, help="e.g. http://127.0.0.1:8080/v1")
    reg.add_argument("--model", required=True)
    reg.add_argument("--api-key-env", help="Name of the environment variable holding the key")
    draft = sub.add_parser("draft")
    draft.add_argument("task")
    draft.add_argument("--model", required=True)
    sub.add_parser("list")

    item = commands.add_parser("acceptance", help="Outcome rates over recorded real tasks")
    item.add_argument("--workflow")


def budget(engine, task_id=None):
    config = engine.project.config["budget"]
    e2e = config.get("e2e_minutes", 240)
    now = datetime.now(timezone.utc)
    rows = engine.store.connection.execute(
        "SELECT id FROM tasks WHERE status NOT IN ('completed','partial','cancelled') ORDER BY updated DESC").fetchall()
    open_minutes = 0.0
    for row in rows:
        open_minutes += (now - datetime.fromisoformat(engine.store.task(row["id"])["created"])).total_seconds() / 60
    result = {"defaults": {**config, "e2e_minutes": e2e}, "open_tasks": len(rows),
              "open_task_minutes": round(open_minutes, 1), "e2e_exceeded": open_minutes > e2e}
    if task_id:
        task = engine.store.task(task_id)
        packs = records.events(engine.store, ("context_prepared",), task_id)
        captures = records.events(engine.store, ("output_captured",), task_id)
        deadline = datetime.fromisoformat(task["spec"]["deadline"])
        result["task"] = {"id": task_id, "status": task["status"],
                          "minutes_remaining": round((deadline - now).total_seconds() / 60, 1),
                          "repairs": f"{task['repairs']}/{config['repairs']}", "packs": len(packs),
                          "pack_bytes": sum(p.get("bytes", 0) for p in packs), "captures": len(captures),
                          "harness_runs": sum(c.get("verifier") == "harness_process" for c in captures),
                          "native_tokens": None}
    return result


def run(engine, args):
    command, action = args.command, getattr(args, "action", None)
    if command == "harness":
        if action == "configure":
            return harness.configure(engine, args.name, argv_of(args), args.timeout)
        if action == "run":
            return harness.run_check(engine, args.task, args.check, argv_of(args))
        payload = json.loads(sys.stdin.read() or "{}")
        return harness.stop_hook(engine, payload, args.check)
    if command == "route":
        if action == "record":
            return routing.record(engine.store, args.task, args.chosen, args.candidate, args.reason)
        if action == "correct":
            return routing.correct(engine.store, args.event_id, args.to, args.reason)
        if action == "history":
            return routing.history(engine.store, args.task)
        routes = json.loads(engine.project.snapshot(args.routes)["text"])
        return routing.select(engine, args.request, routes, args.task)
    if command == "catalog":
        return catalog.research(engine.store, args.need, args.skills, args.mcp_config, args.task)
    if command == "health":
        return health.check(args.mcp_config, args.probe, args.timeout, set(args.server) or None)
    if command == "candidate":
        if action == "trial":
            return candidates.trial(engine, args.source, json_argv(args.install_cmd), json_argv(args.trial_cmd), args.timeout)
        if action == "install":
            return candidates.install(engine, args.id, args.target, json_argv(args.verify_cmd))
        return candidates.rollback(engine, args.id, args.note)
    if command == "benchmark":
        return benchmarks.import_file(engine, args.input, args.task)
    if command == "job":
        if action == "start":
            return jobs.start(engine, args.task, argv_of(args), args.label)
        if action == "stop":
            return jobs.stop(engine, args.id, args.note)
        if action == "reap":
            return jobs.reap(engine)
        if action == "admission":
            return jobs.admission(engine)
        return jobs.show(engine)
    if command == "mcp":
        if action == "register":
            return broker.register(engine, args.name, argv_of(args), args.url)
        if action == "lease":
            return broker.lease(engine, args.name, args.task)
        if action == "release":
            return broker.release(engine, args.lease)
        return broker.evict(engine, args.idle_minutes)
    if command == "dispatch":
        if action == "send":
            return dispatch.dispatch(engine, args.task, args.profile, args.execute)
        if action == "qualify":
            return dispatch.qualify(engine, args.profile, args.evidence, argv_of(args) or None)
        return {"profiles": dispatch.profiles(engine)}
    if command == "change":
        if action == "propose":
            return changes.propose(engine, args.kind, args.target, args.content, args.note, args.proposal)
        if action == "evaluate":
            return changes.evaluate(engine, args.id, argv_of(args) or None)
        if action == "review":
            return changes.review(engine, args.id, args.result, args.evidence, args.note)
        if action == "activate":
            return changes.activate(engine, args.id, argv_of(args) or None)
        if action == "rollback":
            return changes.rollback(engine, args.id, args.note)
        return changes.listing(engine)
    if command == "rules":
        return rulesync.dry_run(engine.project.root, argv_of(args) or None)
    if command == "budget":
        return budget(engine, args.task)
    if command == "model":
        if action == "register":
            return models.register(engine, args.name, args.base_url, args.model, args.api_key_env)
        if action == "draft":
            return models.draft(engine, args.task, args.model)
        return models.registry(engine)
    if command == "acceptance":
        return acceptance.report(engine, args.workflow)
    raise Rejected("Unsupported command")


def memory_extension(engine, args):
    if args.memory_command == "shadow-export":
        return knowledge.shadow_export(engine.store, args.output)
    return knowledge.semantic(engine.store, args.query)
