"""K9: limited random assignment of tasks to native or workflow, to learn the effect. Needs explicit owner approval; never touches irreversible writes."""
import argparse
import json
import re
import sys

import jsonl_store as store
import usage_ledger

# Built-in exclusions cannot be removed: effects that cannot be undone are never randomized.
BUILTIN_EXCLUSIONS = [
    ("delete_or_drop", r"\b(delete|remove permanently|drop (table|database)|rm -rf|truncate|hapus)\b"),
    ("push_or_publish", r"\b(git push|force[- ]push|publish|release|deploy|kirim|send (email|message))\b"),
    ("external_service_write", r"\b(production|prod\b|payment|payout|invoice|transfer|migrate (the )?database)\b"),
]
ARMS = ("native", "workflow")
ARM_SUFFIX = {"native": "Use native tools directly without Agent Workflow.",
              "workflow": "Use Agent Workflow at wf.py for this task. Read skills/agent-workflow/SKILL.md first."}
FIRST_BATCH_CAP = 20


def files(root):
    return {"exclusions": root / "exclusions.jsonl", "batches": root / "batches.jsonl", "assignments": root / "assignments.jsonl"}


def default_root():
    return store.home() / "assignment"


def exclusion_rules(root):
    custom = [(r["reason"], r["pattern"]) for r in store.rows(files(root)["exclusions"])]
    return BUILTIN_EXCLUSIONS + custom


def add_exclusion(root, pattern, reason):
    re.compile(pattern)
    if not reason.strip():
        raise ValueError("a reason is required")
    store.append(files(root)["exclusions"], {"pattern": pattern, "reason": reason, "created": store.now_iso()})
    return {"status": "added", "rules": len(exclusion_rules(root))}


def approve(root, batch, max_tasks, seed, owner_note):
    """Owner approval record. The agent must not run this on its own; it is the owner's explicit permission."""
    path = files(root)["batches"]
    existing = store.rows(path)
    if any(b["batch"] == batch for b in existing):
        raise ValueError("batch %s already exists" % batch)
    if not owner_note.strip():
        raise ValueError("--owner-note must quote the owner's approval")
    if not existing and max_tasks > FIRST_BATCH_CAP:
        raise ValueError("the first batch is capped at %d tasks" % FIRST_BATCH_CAP)
    if existing and not any(b.get("closed") for b in existing):
        raise ValueError("close the open batch before starting another")
    store.append(path, {"batch": batch, "max_tasks": max_tasks, "seed": seed, "owner_note": owner_note, "approved_by": "owner_attested",
                        "created": store.now_iso(), "closed": False})
    return {"status": "approved", "batch": batch, "max_tasks": max_tasks, "exclusions": [r for r, _ in exclusion_rules(root)]}


def batch_of(root, name):
    for item in store.rows(files(root)["batches"]):
        if item["batch"] == name:
            return item
    raise KeyError("no such batch (approve it first): %s" % name)


def arm_for(batch, index):
    """Blocks of two with a seeded order: arms stay balanced however many tasks arrive."""
    block = index // 2
    first = int(store.digest("%s|%s|%d" % (batch["seed"], batch["batch"], block))[:8], 16) % 2
    return ARMS[first] if index % 2 == 0 else ARMS[1 - first]


def assign(root, batch_name, task, request, risky=False):
    batch = batch_of(root, batch_name)
    rows = store.rows(files(root)["assignments"])
    done = [r for r in rows if r["batch"] == batch_name]
    for row in done:
        if row["task"] == task:
            return {**row, "idempotent": True}
    hits = [reason for reason, pattern in exclusion_rules(root) if re.search(pattern, request, re.I)]
    if risky:
        hits.append("declared_risky_by_owner_or_agent")
    if hits:
        row = {"batch": batch_name, "task": task, "excluded": True, "arm": None, "reasons": hits, "created": store.now_iso(),
               "instruction": "not randomized: use the default policy (native, workflow only for explicit or risky-write requests)"}
        store.append(files(root)["assignments"], row)
        return row
    randomized = [r for r in done if not r["excluded"]]
    if len(randomized) >= batch["max_tasks"] or batch.get("closed"):
        raise ValueError("batch %s is full or closed" % batch_name)
    arm = arm_for(batch, len(randomized))
    row = {"batch": batch_name, "task": task, "excluded": False, "arm": arm, "label": "k9:%s:%s" % (batch_name, arm),
           "instruction": ARM_SUFFIX[arm], "created": store.now_iso()}
    store.append(files(root)["assignments"], row)
    return row


def link(root, batch_name, task, entry_id, ledger):
    row = next((r for r in store.rows(files(root)["assignments"]) if r["batch"] == batch_name and r["task"] == task and not r["excluded"]), None)
    if row is None:
        raise KeyError("no randomized assignment for this task")
    usage_ledger.mark(ledger, entry_id, label=row["label"])
    return {"status": "linked", "entry": entry_id, "label": row["label"]}


def close(root, batch_name):
    path = files(root)["batches"]
    items = store.rows(path)
    for item in items:
        if item["batch"] == batch_name:
            item["closed"] = True
    store.save(path, items)
    return status(root, batch_name)


def status(root, batch_name):
    batch = batch_of(root, batch_name)
    rows = [r for r in store.rows(files(root)["assignments"]) if r["batch"] == batch_name]
    return {"batch": batch_name, "max_tasks": batch["max_tasks"], "closed": batch.get("closed", False),
            "randomized": {arm: sum(1 for r in rows if r["arm"] == arm) for arm in ARMS},
            "excluded": sum(1 for r in rows if r["excluded"]), "remaining": batch["max_tasks"] - sum(1 for r in rows if not r["excluded"]),
            "note": "read the effect with cost_per_outcome.py compare k9:%s:native k9:%s:workflow" % (batch_name, batch_name)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=lambda v: __import__("pathlib").Path(v), default=default_root())
    commands = parser.add_subparsers(dest="command", required=True)
    excl = commands.add_parser("exclude")
    excl.add_argument("--pattern", required=True)
    excl.add_argument("--reason", required=True)
    commands.add_parser("rules")
    appr = commands.add_parser("approve", help="Owner only: records explicit permission for one batch")
    appr.add_argument("batch")
    appr.add_argument("--max-tasks", type=int, default=10)
    appr.add_argument("--seed", default="k9")
    appr.add_argument("--owner-note", required=True)
    asg = commands.add_parser("assign")
    asg.add_argument("batch")
    asg.add_argument("task")
    asg.add_argument("--request", required=True)
    asg.add_argument("--risky", action="store_true")
    lnk = commands.add_parser("link")
    lnk.add_argument("batch")
    lnk.add_argument("task")
    lnk.add_argument("--entry", required=True)
    lnk.add_argument("--ledger", default=str(usage_ledger.default_ledger()))
    for name in ("status", "close"):
        commands.add_parser(name).add_argument("batch")
    args = parser.parse_args(argv)
    try:
        if args.command == "exclude":
            result = add_exclusion(args.root, args.pattern, args.reason)
        elif args.command == "rules":
            result = {"rules": [{"reason": r, "pattern": p} for r, p in exclusion_rules(args.root)]}
        elif args.command == "approve":
            result = approve(args.root, args.batch, args.max_tasks, args.seed, args.owner_note)
        elif args.command == "assign":
            result = assign(args.root, args.batch, args.task, args.request, args.risky)
        elif args.command == "link":
            result = link(args.root, args.batch, args.task, args.entry, args.ledger)
        elif args.command == "close":
            result = close(args.root, args.batch)
        else:
            result = status(args.root, args.batch)
    except (OSError, ValueError, KeyError, re.error) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
