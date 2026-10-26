"""K6: owner feedback intake. Screens every item before it can influence anything; a single correction is a candidate, never a fact."""
import argparse
import json
from pathlib import Path
import re
import sys

import jsonl_store as store

SCHEMA = "owner-feedback/1"
SOURCES = ("owner_chat", "tool_output", "web", "file", "third_party", "unknown")
KINDS = ("defect", "requirement", "preference", "satisfaction", "unknown")
POLARITY = {"defect": "negative", "satisfaction": "positive"}
VIAS = ("chat", "answer", "silence", "revert", "edit", "rerequest")
ROUTES = {"defect": "regression_case", "requirement": "new_requirement_version",
          "preference": "narrow_scope_memory", "satisfaction": "weak_signal", "unknown": "ask_followup"}
MAX_TEXT = 2000
GENERALIZE_MIN_TASKS = 3
TOOL_OUTPUT = re.compile(r"\x1b\[|Traceback \(most recent call last\)|^\s*\$ |\"type\"\s*:\s*\"tool_result\"|^\s*at \S+ \(.+:\d+:\d+\)", re.M)
LEADING = re.compile(r"^\s*(ya|iya|yes|ok|oke|sudah|bagus|good|y)[\s.!]*$", re.I)


def default_path():
    return store.home() / "owner-feedback.jsonl"


def load(path):
    return store.rows(path)


def feedback_rows(items):
    return [r for r in items if r.get("record") == "feedback"]


def item_id(task, artifact, kind, text):
    return store.digest(store.canonical([task, artifact, kind, text]))[:16]


def artifact_hash(sha, path):
    if sha:
        return sha
    if path:
        return store.digest(Path(path).read_bytes())
    return None


def screen(entry, existing):
    """Return status, weight and reasons. Failing a screen lowers the status; nothing is thrown away."""
    reasons, weight, status = [], 1.0, "candidate"
    text = entry["text"]
    if entry["source"] != "owner_chat":
        return {"status": "not_feedback", "weight": 0.0, "reasons": ["source %s is data, not owner feedback" % entry["source"]], "conflicts": []}
    if TOOL_OUTPUT.search(text) or len(text.encode("utf-8")) > MAX_TEXT:
        return {"status": "not_feedback", "weight": 0.0, "reasons": ["looks like tool output or is longer than %d bytes" % MAX_TEXT], "conflicts": []}
    if entry["via"] == "silence":
        return {"status": "unknown", "weight": 0.0, "reasons": ["silence is unknown, never positive"], "conflicts": []}
    if entry["kind"] == "satisfaction" or LEADING.match(text) or entry["via"] == "answer" and entry["leading"]:
        weight = min(weight, 0.25)
        reasons.append("satisfaction or leading yes/no answer is a weak signal")
    if not entry["artifact_sha256"] or not entry["part"]:
        weight = min(weight, 0.25)
        reasons.append("no artifact hash or part: ask one sharpening question")
    if entry["via"] in ("revert", "edit") and entry["kind"] == "unknown":
        weight = min(weight, 0.5)
        reasons.append("revert or edit may be preference, not a defect")
    if not entry["evidence"]:
        reasons.append("owner claim only: cannot change validators or acceptance")
    if entry["age_days"] is not None and entry["age_days"] > 7:
        reasons.append("late discovery: bound to the old artifact hash, not to a running task")
    conflicts = []
    polarity = POLARITY.get(entry["kind"])
    for other in feedback_rows(existing):
        if other["id"] == entry["id"] or other["status"] in ("rejected", "not_feedback"):
            continue
        same_target = other["artifact_sha256"] and other["artifact_sha256"] == entry["artifact_sha256"] and (
            not (other["part"] and entry["part"]) or other["part"] == entry["part"])
        other_polarity = POLARITY.get(other["kind"])
        if same_target and polarity and other_polarity and polarity != other_polarity:
            conflicts.append(other["id"])
    if conflicts:
        reasons.append("conflicts with %s: owner decides, never averaged" % ", ".join(conflicts))
    return {"status": status, "weight": weight, "reasons": reasons, "conflicts": conflicts}


def add(path, task, text, source="owner_chat", kind="unknown", part=None, evidence=None, via="chat",
        artifact_sha256=None, scope="task", age_days=None, leading=False):
    if source not in SOURCES or kind not in KINDS or via not in VIAS or scope not in ("task", "project", "global"):
        raise ValueError("unknown source, kind, via or scope")
    if not task.strip() or (not text.strip() and via != "silence"):
        raise ValueError("task and text are required (text may be empty only for silence)")
    items = load(path)
    entry = {"record": "feedback", "schema": SCHEMA, "id": item_id(task, artifact_sha256, kind, text), "task": task,
             "artifact_sha256": artifact_sha256, "source": source, "kind": kind, "part": part, "text": text[:MAX_TEXT * 2],
             "evidence": evidence, "via": via, "scope_requested": scope, "scope_effective": "task", "age_days": age_days,
             "leading": leading, "route": ROUTES[kind], "created": store.now_iso(), "decision": None,
             "may_change_validators": False}
    for old in feedback_rows(items):
        if old["id"] == entry["id"]:
            return {"status": "duplicate", "id": old["id"], "state": old["status"]}
    verdict = screen(entry, items)
    entry.update(status=verdict["status"], weight=verdict["weight"], reasons=verdict["reasons"], conflicts=verdict["conflicts"])
    if verdict["conflicts"]:
        for other in feedback_rows(items):
            if other["id"] in verdict["conflicts"]:
                other["conflicts"] = sorted(set(other.get("conflicts", []) + [entry["id"]]))
    items.append(entry)
    store.save(path, items)
    return {"status": entry["status"], "id": entry["id"], "weight": entry["weight"], "route": entry["route"],
            "reasons": entry["reasons"], "needs_owner_decision": bool(entry["conflicts"]),
            "followup_question": "Bagian mana yang perlu diubah?" if not entry["part"] and entry["status"] == "candidate" else None}


def review(path, entry_id, decision, note):
    """Owner decision, immutable like feedback --decide in the engine."""
    if decision not in ("accepted", "rejected") or not note.strip():
        raise ValueError("decision must be accepted|rejected with a nonempty note")
    items = load(path)
    for entry in feedback_rows(items):
        if entry["id"] == entry_id:
            if entry["status"] in ("accepted", "rejected"):
                if entry["status"] == decision:
                    return {"status": decision, "id": entry_id, "idempotent": True}
                raise ValueError("decision is immutable; add a follow-up item instead")
            if entry["status"] in ("not_feedback", "unknown"):
                raise ValueError("item is %s: it cannot be accepted as owner feedback" % entry["status"])
            entry["status"] = decision
            entry["decision"] = {"note": note, "at": store.now_iso()}
            store.save(path, items)
            return {"status": decision, "id": entry_id, "idempotent": False, "auto_apply": False}
    raise KeyError("no such feedback item: %s" % entry_id)


def should_ask(path, task, risk, tier=None, every=5, daily_budget=3, internal_failed=False, owner_corrected=False, today=None):
    """Asking costs owner time: ask always on risky or full-control tasks, sample the light ones, respect a daily budget."""
    today = today or store.now_iso()[:10]
    asked_today = sum(1 for r in load(path) if r.get("record") == "ask" and r["created"][:10] == today and r["asked"])
    if internal_failed or owner_corrected:
        decision, reason = False, "internal signal already failed or owner already corrected: do not ask"
    elif risk == "write" or tier == 2:
        decision, reason = True, "risky or full-control task: always ask"
    elif every and int(store.digest(task)[:8], 16) % every == 0:
        decision, reason = True, "sampled 1 in %d light tasks" % every
    else:
        decision, reason = False, "light task not sampled"
    if decision and risk != "write" and asked_today >= daily_budget:
        decision, reason = False, "daily interruption budget (%d) used" % daily_budget
    store.append(path, {"record": "ask", "task": task, "asked": decision, "reason": reason, "created": store.now_iso()})
    return {"ask": decision, "reason": reason, "asked_today": asked_today,
            "questions": ["Ada yang perlu diubah? tidak / ya, bagian apa", "Perlu dikerjakan ulang? (hanya bila ada yang perlu diubah)"] if decision else []}


def generalizable(items):
    """A preference or defect pattern applies beyond one task only after independent events in several tasks."""
    groups = {}
    for entry in feedback_rows(items):
        if entry["status"] in ("candidate", "accepted") and entry["weight"] >= 0.5:
            key = (entry["kind"], (entry["part"] or "").casefold())
            groups.setdefault(key, set()).add(entry["task"])
    return [{"kind": k[0], "part": k[1], "tasks": len(v)} for k, v in sorted(groups.items()) if len(v) >= GENERALIZE_MIN_TASKS and k[1]]


def report(path):
    items = load(path)
    rows = feedback_rows(items)
    by_status = {}
    for entry in rows:
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
    asks = [r for r in items if r.get("record") == "ask"]
    return {"schema": "owner-feedback-report/1", "items": len(rows), "by_status": by_status,
            "open_conflicts": sorted(e["id"] for e in rows if e.get("conflicts") and e["status"] == "candidate"),
            "generalizable_patterns": generalizable(items),
            "asks": {"total": len(asks), "asked": sum(1 for a in asks if a["asked"])},
            "note": "candidates are not facts; only owner-accepted items may feed a proposal, and proposals stay proposal_only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    adder = commands.add_parser("add")
    adder.add_argument("task")
    adder.add_argument("--text", default="")
    adder.add_argument("--source", choices=SOURCES, default="owner_chat")
    adder.add_argument("--kind", choices=KINDS, default="unknown")
    adder.add_argument("--part")
    adder.add_argument("--evidence")
    adder.add_argument("--via", choices=VIAS, default="chat")
    adder.add_argument("--artifact-sha256")
    adder.add_argument("--artifact", help="Hash this file instead of passing --artifact-sha256")
    adder.add_argument("--scope", choices=("task", "project", "global"), default="task")
    adder.add_argument("--age-days", type=float)
    adder.add_argument("--leading", action="store_true", help="The owner answered a yes/no question that suggested the answer")
    decider = commands.add_parser("review")
    decider.add_argument("id")
    decider.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    decider.add_argument("--note", required=True)
    asker = commands.add_parser("ask")
    asker.add_argument("task")
    asker.add_argument("--risk", choices=("light", "write"), default="light")
    asker.add_argument("--tier", type=int, choices=(0, 1, 2))
    asker.add_argument("--every", type=int, default=5)
    asker.add_argument("--daily-budget", type=int, default=3)
    asker.add_argument("--internal-failed", action="store_true")
    asker.add_argument("--owner-corrected", action="store_true")
    commands.add_parser("list")
    commands.add_parser("report")
    for sub in commands.choices.values():
        sub.add_argument("--store", default=str(default_path()))
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            result = add(args.store, args.task, args.text, args.source, args.kind, args.part, args.evidence, args.via,
                         artifact_hash(args.artifact_sha256, args.artifact), args.scope, args.age_days, args.leading)
        elif args.command == "review":
            result = review(args.store, args.id, args.decision, args.note)
        elif args.command == "ask":
            result = should_ask(args.store, args.task, args.risk, args.tier, args.every, args.daily_budget,
                                args.internal_failed, args.owner_corrected)
        elif args.command == "list":
            result = {"items": feedback_rows(load(args.store))}
        else:
            result = report(args.store)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
