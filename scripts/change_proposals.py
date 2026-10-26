"""K8: proposals for triage thresholds and wording, judged on a frozen regression set. Never applies anything; decisions are chained and immutable."""
import argparse
import copy
import json
from pathlib import Path
import sys

import jsonl_store as store

SCHEMA = "change-proposal/1"
BASE_CONFIG = {"lazy_requests": 30, "risk_write_tier": 2, "explicit_tier": 1}
OWNER_ONLY = ("claude_md", "permission", "scope")
# A metric may not get worse by more than its tolerance; unsafe misses may not increase at all.
TOLERANCE = {"agreement": 0.0, "false_adopt": 0, "missed_adopt": 0, "unsafe_miss": 0}
HIGHER_IS_BETTER = {"agreement": True, "false_adopt": False, "missed_adopt": False, "unsafe_miss": False}


def paths(root):
    root = Path(root)
    return {"proposals": root / "proposals.jsonl", "decisions": root / "decisions.jsonl", "sets": root / "sets",
            "evaluations": root / "evaluations.jsonl"}


def default_root():
    return store.home() / "proposals"


def triage(case, config):
    """Triage v0 from K4: explicit call is honored, risky writes get full control, lazy adoption at a request threshold."""
    features = case["features"]
    if features.get("risk_write"):
        return config["risk_write_tier"]
    if features.get("explicit"):
        return config["explicit_tier"]
    if features.get("requests_so_far", 0) >= config["lazy_requests"]:
        return 1
    return 0


def metrics(cases, config):
    agree = false_adopt = missed = unsafe = 0
    for case in cases:
        got, want = triage(case, config), case["expected_tier"]
        agree += got == want
        false_adopt += want == 0 and got > 0
        missed += want > 0 and got < want
        unsafe += want == 2 and got < 2
    return {"agreement": round(agree / len(cases), 4), "false_adopt": false_adopt, "missed_adopt": missed, "unsafe_miss": unsafe}


def set_hash(cases):
    return store.digest(store.canonical(cases))


def freeze(root, name, cases_path):
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases or any(not {"id", "features", "expected_tier"} <= set(c) for c in cases):
        raise ValueError("cases must be a nonempty list of {id, features, expected_tier}")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("case ids must be unique")
    target = paths(root)["sets"] / (name + ".json")
    if target.exists():
        raise ValueError("a frozen set is never overwritten; choose a new name")
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"schema": "regression-set/1", "name": name, "frozen_at": store.now_iso(), "sha256": set_hash(cases), "cases": cases}
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"status": "frozen", "name": name, "cases": len(cases), "sha256": record["sha256"]}


def load_set(root, name):
    record = json.loads((paths(root)["sets"] / (name + ".json")).read_text(encoding="utf-8"))
    if set_hash(record["cases"]) != record["sha256"]:
        raise ValueError("frozen set %s was modified after freezing" % name)
    return record


def propose(root, kind, target, new_value, rationale, derived_from_set=None, evidence=None):
    if not rationale.strip():
        raise ValueError("a rationale is required")
    files = paths(root)
    seq = len(store.rows(files["proposals"])) + 1
    proposal = {"schema": SCHEMA, "id": "p%04d" % seq, "kind": kind, "target": target, "to": new_value, "rationale": rationale,
                "derived_from_set": derived_from_set, "evidence": evidence, "created": store.now_iso(), "activation": "proposal_only"}
    if kind in OWNER_ONLY:
        proposal["owner_only"] = True
    store.append(files["proposals"], proposal)
    return {"status": "owner_only" if kind in OWNER_ONLY else "proposed", "id": proposal["id"], "activation": "proposal_only",
            "next": "owner decides directly" if kind in OWNER_ONLY else "evaluate against the frozen set"}


def find(root, proposal_id):
    for item in store.rows(paths(root)["proposals"]):
        if item["id"] == proposal_id:
            return item
    raise KeyError("no such proposal: %s" % proposal_id)


def evaluate(root, proposal_id, set_name, acceptance_path=None):
    proposal = find(root, proposal_id)
    files = paths(root)
    if proposal.get("owner_only"):
        raise ValueError("owner-only proposal kinds (CLAUDE.md, permissions, scope) are not evaluated or learned")
    record = load_set(root, set_name)
    result = {"schema": "change-evaluation/1", "proposal": proposal_id, "set": set_name, "set_sha256": record["sha256"]}
    if proposal["kind"] != "threshold" or proposal["target"] not in BASE_CONFIG:
        result.update(verdict="needs_owner_review", reason="wording and memory changes have no deterministic check; the set only guards thresholds")
    else:
        candidate = copy.deepcopy(BASE_CONFIG)
        candidate[proposal["target"]] = proposal["to"]
        before, after = metrics(record["cases"], BASE_CONFIG), metrics(record["cases"], candidate)
        worse, better = [], []
        for name, higher in HIGHER_IS_BETTER.items():
            delta = after[name] - before[name]
            delta = delta if higher else -delta
            if delta < -TOLERANCE[name]:
                worse.append(name)
            elif delta > 0:
                better.append(name)
        if "unsafe_miss" in worse:
            verdict = "fail"
        elif worse:
            verdict = "tradeoff_for_owner"
        else:
            verdict = "pass"
        result.update(verdict=verdict, baseline=before, candidate=after, worse=worse, better=better,
                      candidate_config=candidate)
    # The proposer has seen its own derivation set: passing it is a regression check, not new acceptance.
    fresh = None
    if acceptance_path:
        cases = json.loads(Path(acceptance_path).read_text(encoding="utf-8"))
        known = {c["id"] for c in record["cases"]}
        overlap = sorted(known & {c["id"] for c in cases})
        fresh = {"cases": len(cases), "overlap_with_regression_set": overlap}
        if not overlap and proposal["kind"] == "threshold" and proposal["target"] in BASE_CONFIG and cases:
            candidate_config = result.get("candidate_config", BASE_CONFIG)
            fresh["baseline"], fresh["candidate"] = metrics(cases, BASE_CONFIG), metrics(cases, candidate_config)
            if fresh["candidate"]["unsafe_miss"] > fresh["baseline"]["unsafe_miss"] or fresh["candidate"]["agreement"] < fresh["baseline"]["agreement"]:
                result["verdict"] = "fail" if result["verdict"] == "pass" else result["verdict"]
                fresh["worse_on_acceptance"] = True
    result["acceptance"] = fresh
    result["acceptance_status"] = ("checked on %d new cases" % fresh["cases"]) if fresh and not fresh["overlap_with_regression_set"] else \
        "missing: promotion needs cases the proposer has not seen"
    result["created"] = store.now_iso()
    store.append(files["evaluations"], result)
    return result


def decide(root, proposal_id, decision, note, owner_accepts_tradeoff=False):
    if decision not in ("accept", "reject") or not note.strip():
        raise ValueError("decision must be accept|reject with a nonempty note")
    files = paths(root)
    proposal = find(root, proposal_id)
    previous = [d for d in store.rows(files["decisions"]) if d["proposal"] == proposal_id]
    if previous:
        if previous[-1]["decision"] == decision:
            return {"status": decision, "proposal": proposal_id, "idempotent": True}
        raise ValueError("decision is immutable; create a follow-up proposal")
    if decision == "accept" and not proposal.get("owner_only"):
        evals = [e for e in store.rows(files["evaluations"]) if e["proposal"] == proposal_id]
        if not evals:
            raise ValueError("accept needs an evaluation against the frozen set first")
        last = evals[-1]
        if load_set(root, last["set"])["sha256"] != last["set_sha256"]:
            raise ValueError("the frozen set changed after the evaluation")
        allowed = last["verdict"] == "pass" or (last["verdict"] == "tradeoff_for_owner" and owner_accepts_tradeoff)
        if last["verdict"] == "needs_owner_review":
            allowed = True
        if not allowed:
            raise ValueError("verdict %s does not allow accept" % last["verdict"])
        if last["acceptance_status"].startswith("missing") and last["verdict"] != "needs_owner_review":
            raise ValueError("passing the regression set is not acceptance: evaluate with --acceptance on unseen cases first")
    row = store.chained_append(files["decisions"], {"proposal": proposal_id, "decision": decision, "note": note, "at": store.now_iso(),
                                                   "tradeoff_accepted": owner_accepts_tradeoff})
    return {"status": decision + "ed", "proposal": proposal_id, "idempotent": False, "auto_apply": False,
            "configuration_changed": False, "chain_ok": store.chain_ok(files["decisions"]), "decision_hash": store.digest(store.canonical(row))}


def status(root):
    files = paths(root)
    proposals = store.rows(files["proposals"])
    decided = {d["proposal"]: d["decision"] for d in store.rows(files["decisions"])}
    return {"proposals": [{"id": p["id"], "kind": p["kind"], "target": p["target"], "to": p["to"], "decision": decided.get(p["id"], "open")}
                          for p in proposals], "decision_chain_ok": store.chain_ok(files["decisions"]), "base_config": BASE_CONFIG}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(default_root()))
    commands = parser.add_subparsers(dest="command", required=True)
    frozen = commands.add_parser("freeze")
    frozen.add_argument("name")
    frozen.add_argument("--cases", required=True)
    prop = commands.add_parser("propose")
    prop.add_argument("--kind", choices=("threshold", "wording", "memory") + OWNER_ONLY, required=True)
    prop.add_argument("--target", required=True)
    prop.add_argument("--to", required=True, help="JSON value, for example 20")
    prop.add_argument("--rationale", required=True)
    prop.add_argument("--derived-from-set")
    prop.add_argument("--evidence")
    ev = commands.add_parser("evaluate")
    ev.add_argument("proposal")
    ev.add_argument("--set", required=True)
    ev.add_argument("--acceptance", help="JSON file of unseen cases in the regression-set case format")
    dec = commands.add_parser("decide")
    dec.add_argument("proposal")
    dec.add_argument("--decision", choices=("accept", "reject"), required=True)
    dec.add_argument("--note", required=True)
    dec.add_argument("--owner-accepts-tradeoff", action="store_true")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            result = freeze(args.root, args.name, args.cases)
        elif args.command == "propose":
            try:
                value = json.loads(args.to)
            except ValueError:
                value = args.to
            result = propose(args.root, args.kind, args.target, value, args.rationale, args.derived_from_set, args.evidence)
        elif args.command == "evaluate":
            result = evaluate(args.root, args.proposal, args.set, args.acceptance)
        elif args.command == "decide":
            result = decide(args.root, args.proposal, args.decision, args.note, args.owner_accepts_tradeoff)
        else:
            result = status(args.root)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if result.get("verdict") in ("fail", "tradeoff_for_owner") else 0


if __name__ == "__main__":
    sys.exit(main())
