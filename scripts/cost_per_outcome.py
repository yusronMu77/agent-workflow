"""K7: cost per accepted outcome per class, with n and spread. Refuses to claim an effect without a comparator."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys

import jsonl_store as store
import owner_feedback
import usage_ledger

# K0 weightings. The price ratio is an assumption, not Anthropic's allowance accounting (K0 left token weights uncalibrated).
WEIGHTINGS = {
    "raw_total": {"input_uncached": 1, "cache_read": 1, "cache_write": 1, "output": 1},
    "uncached_plus_output": {"input_uncached": 1, "cache_read": 0, "cache_write": 0, "output": 1},
    "assumed_price_ratio": {"input_uncached": 1, "cache_read": 0.1, "cache_write": 1.25, "output": 5},
}
CAUSAL_PREFIX = "k9:"


def cost(entry, weights):
    tokens = entry.get("tokens")
    if not tokens:
        return None
    return sum(tokens[key] * weight for key, weight in weights.items())


def accepted_of(entry, feedback_items):
    """Owner mark wins; else a weighty negative owner item on the same label/entry; silence is never acceptance."""
    outcome = entry.get("outcome") or {}
    if outcome.get("accepted") in ("yes", "no"):
        return outcome["accepted"], "owner_mark"
    keys = {entry["entry_id"], entry.get("label")} - {None}
    for item in owner_feedback.feedback_rows(feedback_items):
        if item["task"] in keys and item["status"] in ("candidate", "accepted") and item["weight"] >= 0.5 \
                and item["kind"] == "defect":
            return "no", "owner_feedback"
    return "unknown", None


def spread(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return {"n": len(values), "min": round(values[0], 2), "median": round(statistics.median(values), 2), "max": round(values[-1], 2)}


def wilson(successes, total, z=1.96):
    if not total:
        return None
    p = successes / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    denom = 1 + z * z / total
    return [round((centre - margin) / denom, 3), round((centre + margin) / denom, 3)]


def group_key(entry, fields):
    return "|".join(str(entry.get(f) if f != "tier" else (entry.get("tier") or {}).get("value")) for f in fields)


def summarize(entries, feedback_items):
    marks = [accepted_of(e, feedback_items) for e in entries]
    yes = sum(1 for m in marks if m[0] == "yes")
    no = sum(1 for m in marks if m[0] == "no")
    known = yes + no
    out = {"sessions": len(entries), "accepted_yes": yes, "accepted_no": no, "outcome_unknown": len(entries) - known,
           "acceptance_rate": round(yes / known, 3) if known else None, "acceptance_ci95": wilson(yes, known),
           "requests": spread([e["requests"] for e in entries]), "wall_seconds": spread([e.get("wall_seconds") for e in entries]),
           "cost": {}}
    for name, weights in WEIGHTINGS.items():
        costs = [cost(e, weights) for e in entries]
        usable = [c for c in costs if c is not None]
        total = sum(usable)
        out["cost"][name] = {"total": round(total, 1), "per_session": spread(usable),
                             "per_accepted": round(total / yes, 1) if yes else None,
                             "note": "total cost of every attempt in the class divided by accepted results"}
    out["coverage_warning"] = "fewer than half of the sessions have a known outcome" if known * 2 < len(entries) else None
    return out


def report(ledger, feedback, by=("label", "client")):
    entries = list(usage_ledger.load(ledger).values())
    items = store.rows(feedback)
    groups = {}
    for entry in entries:
        groups.setdefault(group_key(entry, by), []).append(entry)
    return {"schema": "cost-per-outcome/1", "grouped_by": list(by), "weightings": WEIGHTINGS,
            "groups": {key: summarize(rows, items) for key, rows in sorted(groups.items())},
            "note": "descriptive and observational: classes differ in more than the control level, so no group difference is an effect"}


def compare(ledger, feedback, label_a, label_b, min_sessions=5, min_known=5):
    entries = list(usage_ledger.load(ledger).values())
    items = store.rows(feedback)
    rows = {label: [e for e in entries if e.get("label") == label] for label in (label_a, label_b)}
    summary = {label: summarize(group, items) for label, group in rows.items()}
    reasons = []
    for label, group in summary.items():
        if group["sessions"] < min_sessions:
            reasons.append("%s has %d sessions, needs %d" % (label, group["sessions"], min_sessions))
        if group["accepted_yes"] + group["accepted_no"] < min_known:
            reasons.append("%s has %d known outcomes, needs %d" % (label, group["accepted_yes"] + group["accepted_no"], min_known))
    if reasons:
        return {"schema": "cost-compare/1", "status": "refused", "reasons": reasons, "groups": summary}
    causal = label_a.startswith(CAUSAL_PREFIX) and label_b.startswith(CAUSAL_PREFIX)
    a, b = summary[label_a], summary[label_b]
    ratios = {name: (round(a["cost"][name]["per_accepted"] / b["cost"][name]["per_accepted"], 3)
                     if a["cost"][name]["per_accepted"] and b["cost"][name]["per_accepted"] else None) for name in WEIGHTINGS}
    return {"schema": "cost-compare/1", "status": "causal" if causal else "descriptive_only", "a": label_a, "b": label_b,
            "cost_per_accepted_ratio_a_over_b": ratios,
            "acceptance_rate_difference_a_minus_b": round(a["acceptance_rate"] - b["acceptance_rate"], 3),
            "acceptance_ci95": {label_a: a["acceptance_ci95"], label_b: b["acceptance_ci95"]},
            "reading": ("labels come from a randomized K9 batch, so the difference may be read as an effect for this task mix"
                        if causal else "labels were not randomized: report as association only, never as an effect"),
            "groups": summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    rep = commands.add_parser("report")
    rep.add_argument("--by", default="label,client", help="comma list of: label, client, tier, project")
    cmp_ = commands.add_parser("compare")
    cmp_.add_argument("label_a")
    cmp_.add_argument("label_b")
    cmp_.add_argument("--min-sessions", type=int, default=5)
    cmp_.add_argument("--min-known", type=int, default=5)
    for sub in commands.choices.values():
        sub.add_argument("--ledger", default=str(usage_ledger.default_ledger()))
        sub.add_argument("--feedback", default=str(owner_feedback.default_path()))
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            fields = tuple(f for f in args.by.split(",") if f in ("label", "client", "tier", "project"))
            if not fields:
                raise ValueError("--by needs at least one of label, client, tier, project")
            result = report(args.ledger, args.feedback, fields)
        else:
            result = compare(args.ledger, args.feedback, args.label_a, args.label_b, args.min_sessions, args.min_known)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if result.get("status") == "refused" else 0


if __name__ == "__main__":
    sys.exit(main())
