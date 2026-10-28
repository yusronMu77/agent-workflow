"""Prepare isolated A/B trials and validate recorded results; never runs a model."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_release import product_files

SCENARIOS = {
    "bug": {
        "prompt": "Fix total([]) so it returns 0 and total([2, 3]) returns 5. Run the existing tests. Preserve input values.",
        "rubric": "Both existing tests pass; the function does not mutate its input.",
        "files": {
            "app.py": "def total(values):\n    return sum(values) + 1\n",
            "test_app.py": "import unittest\nfrom app import total\n\nclass Tests(unittest.TestCase):\n    def test_empty(self):\n        self.assertEqual(total([]), 0)\n    def test_values(self):\n        values = [2, 3]\n        self.assertEqual(total(values), 5)\n        self.assertEqual(values, [2, 3])\n",
        },
    },
    "short-output": {
        "prompt": "Read diagnostic.txt and write answer.md naming the file, line, expected value and actual value of the failure.",
        "rubric": "answer.md identifies app.py line 91, expected 42, actual 0; no unsupported explanation.",
        "files": {"diagnostic.txt": "ERROR app.py:91 expected 42 got 0\n"},
    },
    "long-log": {
        "prompt": "Find every ERROR in diagnostic.txt. Write answer.md naming each file, line and expected/actual values.",
        "rubric": "answer.md identifies middle.py line 57 expected 7 got 9 and tail.py line 91 expected 42 got 0.",
        "files": {"diagnostic.txt": "PASS validation\n" * 3000 + "ERROR middle.py:57 expected 7 got 9\n"
                  + "PASS validation\n" * 3000 + "ERROR tail.py:91 expected 42 got 0\n"},
    },
}


def hash_files(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True)
        stream.write("\n")


def prepare(output, seed=12, suite="smoke", design_studio=None):
    output = output.resolve()
    # Preserve previous plans/results and avoid accidentally benchmarking inside
    # a workspace whose parent instructions or skill discovery favor one arm.
    # A project is .agent-workflow/project.json; a bare ~/.agent-workflow (cost ledger) is not one.
    for parent in output.parents:
        if any((parent / name).exists() for name in ("AGENTS.md", "CLAUDE.md", ".agent-workflow/project.json")):
            raise ValueError("Choose a pilot directory outside existing agent workspaces")
    if output.exists():
        raise ValueError("Choose a new pilot directory; existing trials are never overwritten")
    scenario_set, extra_files = SCENARIOS, {}
    if suite == "heavy-3d":
        from pilot_heavy import scenarios, design_files
        if design_studio is None:
            raise ValueError("heavy-3d requires --design-studio pointing to the local backend")
        scenario_set, extra_files = scenarios(), design_files(design_studio)
    elif suite != "smoke":
        raise ValueError("Unknown pilot suite")
    product = dict(product_files())
    kit_hash = hash_files({name: hashlib.sha256(body).hexdigest() for name, body in product.items()})
    output.mkdir(parents=True)
    trials, results = [], []
    for scenario, definition in scenario_set.items():
        for arm in ("A", "B"):
            trial_id = scenario + "-" + arm
            root = output / trial_id
            root.mkdir()
            files = {"AGENTS.md": "Work only in this trial. Preserve evidence. Report actual checks and limitations.\n",
                     **definition["files"]}
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
            for name, body in extra_files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            # Both arms have exactly the same tools and pinned kit snapshot.
            for name, body in product.items():
                path = root / "tools/agent-workflow" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            prompt = definition["prompt"] + "\n"
            if arm == "A":
                prompt += "Use native tools directly without Agent Workflow.\n"
            else:
                prompt += ("Use Agent Workflow at tools/agent-workflow/wf.py for this task. "
                           "Read tools/agent-workflow/skills/agent-workflow/SKILL.md first.\n")
                if suite == "heavy-3d":
                    prompt += ("Initialize the workflow project, begin task design-trial with --workflow design "
                               "--output project:delivery.json --check json --check capture_exit_zero --minutes 12. "
                               "Pack and read context, checkpoint before rendering, capture final checker log "
                               "with actual exit code, record review evidence, then verify.\n")
            # Prompt lives outside the project so project snapshots remain equal.
            (output / (trial_id + ".prompt.txt")).write_text(prompt, encoding="utf-8", newline="\n")
            trials.append({"trial_id": trial_id, "scenario": scenario, "arm": arm,
                           "snapshot_sha256": hash_files(files), "rubric": definition["rubric"],
                           "protected_sha256": {
                               **{name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in files.items()
                                  if name in ("AGENTS.md", "brief.json", "brief.md", "check_delivery.py", "test_pipeline.py", "diagnostic.txt")},
                               **{name: hashlib.sha256(body).hexdigest() for name, body in extra_files.items()},
                               **{"tools/agent-workflow/" + name: hashlib.sha256(body).hexdigest() for name, body in product.items()}}})
            results.append({"trial_id": trial_id, "session_id": None, "model": None, "effort": None,
                            "client_version": None, "tool_catalog_sha256": None,
                            "elapsed_seconds": None, "success": None, "rubric_evidence": None,
                            "tool_calls": None, "retries": None, "user_interventions": None,
                            "instruction_violations": None, "native_tokens": None})
    random.Random(seed).shuffle(trials)
    plan = {"schema": 1, "seed": seed, "suite": suite, "kit_sha256": kit_hash,
            "backend_sha256": hash_files({name: hashlib.sha256(body).hexdigest() for name, body in extra_files.items()}), "trials": trials,
            "acceptance": "Fresh session per trial; same model/effort/client/catalog; source rubric reviewed with trace evidence; zero instruction violations.",
            "limitations": "Three pairs, one trial per arm/scenario: exploratory only, no statistical or token savings claim."}
    write_json(output / "plan.json", plan)
    write_json(output / "results.template.json", {"trials": results})
    return {"status": "prepared_not_run", "plan": str(output / "plan.json"), "trials": len(trials),
            "order": [row["trial_id"] for row in trials], "native_tokens": None}


def summarize(plan_path, results_path):
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    results = json.loads(results_path.read_text(encoding="utf-8"))
    expected = {row["trial_id"]: row for row in plan["trials"]}
    rows = results["trials"]
    ids = [row["trial_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("Results must contain each planned trial exactly once")
    pending = [row["trial_id"] for row in rows if any(row.get(field) is None for field in (
        "session_id", "model", "effort", "client_version", "tool_catalog_sha256", "elapsed_seconds",
        "success", "rubric_evidence", "tool_calls", "retries", "user_interventions", "instruction_violations"))]
    if pending:
        return {"status": "pending_fresh_sessions", "pending": pending, "completed_comparison": False,
                "native_tokens": None}
    sessions, settings, evidence = set(), set(), []
    for row in rows:
        for name in ("session_id", "model", "effort", "client_version", "tool_catalog_sha256", "rubric_evidence"):
            if not isinstance(row[name], str) or not row[name].strip():
                raise ValueError(f"{name} must be a nonempty string")
        if row["session_id"] in sessions:
            raise ValueError("Every trial requires a distinct fresh session")
        sessions.add(row["session_id"])
        settings.add(tuple(row[key] for key in ("model", "effort", "client_version", "tool_catalog_sha256")))
        if len(row["tool_catalog_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in row["tool_catalog_sha256"]):
            raise ValueError("tool_catalog_sha256 must be a SHA-256 hex digest")
        if type(row["success"]) is not bool:
            raise ValueError("success must be a boolean")
        for name in ("elapsed_seconds", "tool_calls", "retries", "user_interventions", "instruction_violations", "native_tokens"):
            value = row[name]
            if name == "native_tokens" and value is None:
                continue
            if type(value) not in (int, float) or not 0 <= value < float("inf"):
                raise ValueError(f"{name} must be a finite nonnegative number")
            if name != "elapsed_seconds" and type(value) is not int:
                raise ValueError(f"{name} must be an integer")
        root = (plan_path.parent / row["trial_id"]).resolve()
        path = (root / row["rubric_evidence"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
            raise ValueError("Rubric evidence must be a nonempty file within its trial")
        evidence.append({"trial_id": row["trial_id"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if len(settings) != 1:
        raise ValueError("Model, effort, client and tool catalog must match across arms/trials")
    pairs = []
    by_id = {row["trial_id"]: row for row in rows}
    for scenario in dict.fromkeys(row["scenario"] for row in plan["trials"]):
        pair = {arm: next(by_id[trial_id] for trial_id, spec in expected.items()
                          if spec["scenario"] == scenario and spec["arm"] == arm) for arm in ("A", "B")}
        pairs.append({"scenario": scenario, "A_success": pair["A"]["success"], "B_success": pair["B"]["success"],
                      "B_minus_A_seconds": pair["B"]["elapsed_seconds"] - pair["A"]["elapsed_seconds"],
                      "A_instruction_violations": pair["A"]["instruction_violations"],
                      "B_instruction_violations": pair["B"]["instruction_violations"]})
    return {"status": "exploratory_complete", "completed_comparison": True, "pairs": pairs, "evidence": evidence,
            "acceptance_passed": all(row["success"] and row["instruction_violations"] == 0 for row in rows),
            "limitations": plan["limitations"], "verification": "Recorded native-session claims; evidence hashes do not prove independent grading or session freshness."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("prepare")
    new.add_argument("--output", type=Path, required=True)
    new.add_argument("--seed", type=int, default=12)
    new.add_argument("--suite", choices=("smoke", "heavy-3d"), default="smoke")
    new.add_argument("--design-studio", type=Path)
    report = sub.add_parser("summarize")
    report.add_argument("--plan", type=Path, required=True)
    report.add_argument("--results", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.output, args.seed, args.suite, args.design_studio)
        else:
            result = summarize(args.plan, args.results)
            write_json(args.output, result)
        print(json.dumps(result))
        return 3 if result["status"] == "pending_fresh_sessions" or result.get("acceptance_passed") is False else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
