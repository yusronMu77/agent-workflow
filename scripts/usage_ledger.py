"""Observation-only cost ledger: one counts-only row per native session; never touches the sessions or stores their text."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile

import usage_sensor as sensor

SCHEMA = "ledger-entry/1"
OWNER_FIELDS = ("label", "tier", "outcome")


def default_ledger():
    return Path.home() / ".agent-workflow" / "cost-ledger.jsonl"


def entry_from(record, source):
    """Keep counts, timestamps and provenance only: no prompt, answer, file content or full path."""
    ids = record["session_ids"]
    session = ids[0] if ids else source.stem
    project = Path(str(record["cwd"]).replace("\\", "/")).name if record.get("cwd") else None
    return {
        "schema": SCHEMA, "entry_id": "%s:%s" % (record["client"], session), "client": record["client"],
        "session_id": session, "project": project, "models": record["models"], "window": record["window"],
        "requests": record["requests"], "sidechain_requests": record.get("sidechain_requests", 0),
        "tool_calls": record["tool_calls"], "user_turns": record.get("user_turns"),
        "tokens": record["tokens"], "tokens_sidechain": record.get("tokens_sidechain"),
        "wall_seconds": record["wall_seconds"], "active_seconds_estimate": record.get("active_seconds_estimate"),
        "context_drops_over_half": (record.get("quality") or {}).get("context_drops_over_half"),
        "provenance": record["provenance"],
        "label": None, "tier": None, "outcome": {"accepted": "unknown", "source": None},
    }


def load(path):
    path = Path(path)
    return {e["entry_id"]: e for e in sensor.read_jsonl(path)} if path.is_file() else {}


def save(path, entries):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        for key in sorted(entries):
            handle.write(json.dumps(entries[key], ensure_ascii=False) + "\n")
    os.replace(temp, path)


def sources(claude_root, codex_root):
    if claude_root and Path(claude_root).is_dir():
        for path in sorted(Path(claude_root).glob("*/*.jsonl")):
            yield path, sensor.claude_record
    if codex_root and Path(codex_root).is_dir():
        for path in sorted(Path(codex_root).glob("**/rollout-*.jsonl")):
            yield path, sensor.codex_rollout_record


def scan(ledger, claude_root, codex_root, min_requests=1):
    """Upsert one entry per session. A re-scan refreshes the counts of a session that grew and keeps owner labels."""
    entries = load(ledger)
    added = updated = skipped = unreadable = 0
    for path, reader in sources(claude_root, codex_root):
        try:
            record = reader(path)
        except (OSError, UnicodeDecodeError):
            unreadable += 1
            continue
        if record["tokens"] is None or record["requests"] < min_requests:
            skipped += 1
            continue
        entry = entry_from(record, path)
        previous = entries.get(entry["entry_id"])
        if previous:
            for field in OWNER_FIELDS:
                entry[field] = previous.get(field, entry[field])
            updated += previous != entry
        else:
            added += 1
        entries[entry["entry_id"]] = entry
    save(ledger, entries)
    return {"schema": "ledger-scan/1", "ledger": str(ledger), "entries": len(entries), "added": added,
            "refreshed": updated, "skipped_without_usage": skipped, "unreadable": unreadable}


def mark(ledger, entry_id, label=None, tier=None, accepted=None):
    entries = load(ledger)
    if entry_id not in entries:
        raise KeyError("no such entry: %s" % entry_id)
    entry = entries[entry_id]
    if label is not None:
        entry["label"] = label
    if tier is not None:
        entry["tier"] = {"value": tier, "source": "owner"}
    if accepted is not None:
        entry["outcome"] = {"accepted": accepted, "source": "owner"}
    save(ledger, entries)
    return entry


def spread(values):
    values = sorted(values)
    return {"n": len(values), "min": values[0], "median": int(statistics.median(values)), "max": values[-1]} if values else None


def report(ledger):
    entries = list(load(ledger).values())
    groups = {}
    for entry in entries:
        groups.setdefault(entry["client"], []).append(entry)
    return {
        "schema": "ledger-report/1", "entries": len(entries),
        "labeled": sum(1 for e in entries if e["label"]), "outcome_known": sum(1 for e in entries if e["outcome"]["accepted"] != "unknown"),
        "by_client": {client: {
            "sessions": len(rows),
            "total_tokens": spread([r["tokens"]["total_incl_cached"] for r in rows]),
            "requests": spread([r["requests"] for r in rows]),
            "wall_seconds": spread([int(r["wall_seconds"]) for r in rows])} for client, rows in sorted(groups.items())},
        "note": "per-session spread, not per-task; sessions without a known outcome cannot yield cost per accepted result",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scanner = commands.add_parser("scan")
    scanner.add_argument("--ledger", default=str(default_ledger()))
    scanner.add_argument("--claude-root", default=str(Path.home() / ".claude" / "projects"))
    scanner.add_argument("--codex-root", default=str(Path.home() / ".codex" / "sessions"))
    scanner.add_argument("--min-requests", type=int, default=1)
    marker = commands.add_parser("mark")
    marker.add_argument("entry_id")
    marker.add_argument("--ledger", default=str(default_ledger()))
    marker.add_argument("--label")
    marker.add_argument("--tier", type=int, choices=(0, 1, 2))
    marker.add_argument("--accepted", choices=("yes", "no", "unknown"))
    reporter = commands.add_parser("report")
    reporter.add_argument("--ledger", default=str(default_ledger()))
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            result = scan(args.ledger, args.claude_root, args.codex_root, args.min_requests)
        elif args.command == "mark":
            result = mark(args.ledger, args.entry_id, args.label, args.tier, args.accepted)
        else:
            result = report(args.ledger)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
