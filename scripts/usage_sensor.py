"""Read native token usage and elapsed time from client session logs; never writes or runs anything."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_jsonl(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            # An interrupted final line is skipped, never invented.
            continue
        if isinstance(row, dict):
            yield row


def tokens(uncached, cache_read, cache_write, output, total):
    return {"input_uncached": uncached, "cache_read": cache_read, "cache_write": cache_write,
            "output": output, "total_incl_cached": total}


def claude_tokens(usages):
    uncached = sum(u["input_tokens"] for u in usages)
    read = sum(u["cache_read_input_tokens"] for u in usages)
    write = sum(u["cache_creation_input_tokens"] for u in usages)
    output = sum(u["output_tokens"] for u in usages)
    return tokens(uncached, read, write, output, uncached + read + write + output)


def normalized(raw):
    return {key: int(raw.get(key) or 0) for key in
            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")}


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))] if ordered else None


def claude_record(path, since=None, until=None, idle_cap=300):
    """Claude Code logs repeat one request's usage on every content-block line; count each request once."""
    requests, tool_start, waits, stamps = {}, {}, [], []
    sessions, models = set(), set()
    cwd = None
    seen_lines = conflicts = user_turns = naive = 0
    tool_ids = set()
    for row in read_jsonl(path):
        ts = parse_ts(row["timestamp"]) if row.get("timestamp") else None
        if ts and ((since and ts < since) or (until and ts > until)):
            continue
        if ts:
            stamps.append(ts)
        if row.get("sessionId"):
            sessions.add(row["sessionId"])
        cwd = cwd or row.get("cwd")
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = message.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if row.get("type") == "assistant":
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("id"):
                    tool_ids.add(block["id"])
                    tool_start.setdefault(block["id"], ts)
            usage = message.get("usage")
            if isinstance(usage, dict) and message.get("model") != "<synthetic>":
                seen_lines += 1
                key = message.get("id") or row.get("requestId") or row.get("uuid")
                entry = {"usage": normalized(usage), "side": bool(row.get("isSidechain")), "ts": ts}
                naive += sum(entry["usage"].values())
                previous = requests.get(key)
                if previous is None:
                    requests[key] = entry
                    models.add(message.get("model"))
                elif previous["usage"] != entry["usage"]:
                    conflicts += 1
                    if entry["usage"]["output_tokens"] > previous["usage"]["output_tokens"]:
                        requests[key] = entry
        elif row.get("type") == "user":
            results = [b for b in blocks if b.get("type") == "tool_result"]
            if not results and (isinstance(content, str) or any(b.get("type") == "text" for b in blocks)):
                user_turns += 1
            for block in results:
                started = tool_start.get(block.get("tool_use_id"))
                if started and ts:
                    waits.append(max(0.0, (ts - started).total_seconds()))
    main = [r for r in requests.values() if not r["side"]]
    side = [r for r in requests.values() if r["side"]]
    deduped = sum(sum(r["usage"].values()) for r in requests.values())
    # Requests keep first-appearance (log) order, so a context that halves marks a compaction or clear.
    breaks = sum(1 for a, b in zip(main, main[1:]) if context(b["usage"]) < context(a["usage"]) * 0.5)
    stamps.sort()
    wall = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0
    active = sum(g for g in ((b - a).total_seconds() for a, b in zip(stamps, stamps[1:])) if g <= idle_cap)
    unavailable = not requests
    return {
        "schema": "cost-record/1", "client": "claude", "session_ids": sorted(sessions), "models": sorted(m for m in models if m),
        "cwd": cwd,
        "window": {"first": stamps[0].isoformat() if stamps else None, "last": stamps[-1].isoformat() if stamps else None,
                   "since": since.isoformat() if since else None, "until": until.isoformat() if until else None},
        "requests": len(main), "sidechain_requests": len(side), "tool_calls": len(tool_ids), "user_turns": user_turns,
        "tokens": None if unavailable else claude_tokens([r["usage"] for r in main]),
        "tokens_sidechain": None if not side else claude_tokens([r["usage"] for r in side]),
        "wall_seconds": round(wall, 3), "active_seconds_estimate": round(active, 3),
        "tool_wait_seconds": {"count": len(waits), "total": round(sum(waits), 3), "p50": percentile(waits, 0.5),
                              "p95": percentile(waits, 0.95), "max": max(waits) if waits else None},
        "human_waits": None,
        "provenance": {
            "tokens": "unavailable" if unavailable else "measured: usage of each unique request (message id)",
            "wall_seconds": "measured: first to last log timestamp, includes idle time",
            "active_seconds_estimate": "estimate: sum of gaps up to %d s" % idle_cap,
            "tool_wait_seconds": "measured, mixed causes: command runtime plus any approval wait, not separable",
            "human_waits": "unavailable: approval prompts are not recorded in the log"},
        "quality": {"usage_lines": seen_lines, "unique_requests": len(requests),
                    "duplicate_usage_lines": seen_lines - len(requests), "dedup_conflicts": conflicts,
                    "naive_total_all_lines": naive, "total_unique_requests": deduped,
                    "overcount_factor_if_not_deduped": round(naive / deduped, 3) if deduped else None,
                    "context_drops_over_half": breaks, "idle_cap_seconds": idle_cap},
    }


def context(usage):
    return usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]


def codex_record(path):
    rows = list(read_jsonl(path))
    threads = [r["thread_id"] for r in rows if r.get("type") == "thread.started"]
    usage = [r["usage"] for r in rows if r.get("type") == "turn.completed" and isinstance(r.get("usage"), dict)]
    items = [r.get("item", {}) for r in rows if r.get("type") == "item.completed"]
    kinds = {}
    for item in items:
        kinds[item.get("type")] = kinds.get(item.get("type"), 0) + 1
    if usage:
        total_in = sum(int(u.get("input_tokens") or 0) for u in usage)
        cached = sum(int(u.get("cached_input_tokens") or 0) for u in usage)
        write = sum(int(u.get("cache_write_input_tokens") or 0) for u in usage)
        output = sum(int(u.get("output_tokens") or 0) for u in usage)
        record_tokens = tokens(total_in - cached, cached, write, output, total_in + output)
    else:
        record_tokens = None
    return {
        "schema": "cost-record/1", "client": "codex", "session_ids": threads, "models": [],
        "requests": len(usage), "tool_calls": kinds.get("command_execution", 0), "item_types": kinds,
        "tokens": record_tokens, "tokens_sidechain": None, "wall_seconds": None, "active_seconds_estimate": None,
        "tool_wait_seconds": None, "human_waits": None,
        "provenance": {
            "tokens": "unavailable" if not usage else "measured: sum of turn.completed usage; input_tokens includes cached",
            "wall_seconds": "unavailable: trace has no timestamps", "human_waits": "unavailable",
            "cache_write": "assumed included in input_tokens; zero in every pilot trace"},
    }


def codex_rollout_record(path):
    """Codex's own session log (~/.codex/sessions): cumulative usage per model request, so requests are counted exactly."""
    sessions, models, cwd = [], set(), None
    stamps, totals, last_total = [], 0, None
    tool_calls = user_turns = 0
    for row in read_jsonl(path):
        ts = parse_ts(row["timestamp"]) if row.get("timestamp") else None
        if ts:
            stamps.append(ts)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        kind = payload.get("type")
        if row.get("type") == "session_meta":
            if payload.get("id") and payload["id"] not in sessions:
                sessions.append(payload["id"])
            cwd = cwd or payload.get("cwd")
        elif row.get("type") == "turn_context":
            models.add(payload.get("model"))
            cwd = cwd or payload.get("cwd")
        elif row.get("type") == "response_item" and kind in ("function_call", "custom_tool_call"):
            tool_calls += 1
        elif row.get("type") == "event_msg" and kind == "user_message":
            user_turns += 1
        elif row.get("type") == "event_msg" and kind == "token_count" and isinstance(payload.get("info"), dict):
            total = payload["info"].get("total_token_usage")
            if isinstance(total, dict) and total != last_total:
                totals, last_total = totals + 1, total
    stamps.sort()
    if last_total:
        total_in = int(last_total.get("input_tokens") or 0)
        cached = int(last_total.get("cached_input_tokens") or 0)
        output = int(last_total.get("output_tokens") or 0)
        record_tokens = tokens(total_in - cached, cached, int(last_total.get("cache_write_input_tokens") or 0),
                               output, total_in + output)
    else:
        record_tokens = None
    return {
        "schema": "cost-record/1", "client": "codex", "session_ids": sessions, "models": sorted(m for m in models if m),
        "cwd": cwd,
        "window": {"first": stamps[0].isoformat() if stamps else None, "last": stamps[-1].isoformat() if stamps else None},
        "requests": totals, "sidechain_requests": 0, "tool_calls": tool_calls, "user_turns": user_turns,
        "tokens": record_tokens, "tokens_sidechain": None,
        "wall_seconds": round((stamps[-1] - stamps[0]).total_seconds(), 3) if len(stamps) > 1 else 0.0,
        "active_seconds_estimate": None, "tool_wait_seconds": None, "human_waits": None,
        "provenance": {
            "tokens": "unavailable" if not last_total else "measured: last cumulative total_token_usage; input_tokens includes cached",
            "requests": "measured: token_count events whose cumulative total changed",
            "wall_seconds": "measured: first to last log timestamp, includes idle time", "human_waits": "unavailable"},
    }


def verify_pilot(trials_dir, csv_path):
    rows = []
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            trace = Path(trials_dir) / row["trial_id"] / "evidence/native-session/trace.jsonl"
            if not trace.is_file():
                rows.append({"trial_id": row["trial_id"], "match": False, "reason": "trace missing"})
                continue
            got = codex_record(trace)["tokens"]
            expected = {"total_incl_cached": int(row["native_tokens"]),
                        "output": int(row["output_tokens"]),
                        "cache_read": int(row["cached_input_tokens"]),
                        "input_uncached": int(row["input_tokens"]) - int(row["cached_input_tokens"])}
            diffs = {k: [got[k] if got else None, v] for k, v in expected.items() if not got or got[k] != v}
            rows.append({"trial_id": row["trial_id"], "match": not diffs, "diffs": diffs})
    return {"schema": "sensor-verification/1", "matched": sum(r["match"] for r in rows), "trials": len(rows),
            "passed": bool(rows) and all(r["match"] for r in rows), "rows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    claude = commands.add_parser("claude")
    claude.add_argument("transcript")
    claude.add_argument("--since")
    claude.add_argument("--until")
    claude.add_argument("--idle-cap", type=int, default=300)
    codex = commands.add_parser("codex")
    codex.add_argument("trace")
    rollout = commands.add_parser("codex-rollout", help="Codex session log from ~/.codex/sessions")
    rollout.add_argument("rollout")
    verify = commands.add_parser("verify-pilot", help="Recompute pilot CSV totals from raw native traces")
    verify.add_argument("--trials-dir", required=True)
    verify.add_argument("--csv", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "claude":
            bounds = {k: parse_ts(v) if v else None for k, v in (("since", args.since), ("until", args.until))}
            if (args.since and bounds["since"] is None) or (args.until and bounds["until"] is None):
                raise ValueError("--since/--until must be ISO 8601 timestamps with an offset, for example 2026-10-25T01:00:00Z")
            result = claude_record(args.transcript, bounds["since"], bounds["until"], args.idle_cap)
        elif args.command == "codex":
            result = codex_record(args.trace)
        elif args.command == "codex-rollout":
            result = codex_rollout_record(args.rollout)
        else:
            result = verify_pilot(args.trials_dir, args.csv)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed", True) else 3


if __name__ == "__main__":
    sys.exit(main())
