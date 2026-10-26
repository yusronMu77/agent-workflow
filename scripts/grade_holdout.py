"""Grade fresh holdout outputs, trace sizes and preserved fixture hashes."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def grade(plan_path):
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rows = []
    for trial in plan["trials"]:
        root = plan_path.parent / trial["trial_id"]
        evidence = root / "evidence/native-session"
        outcome = json.loads((evidence / "outcome.json").read_text(encoding="utf-8"))
        events = []
        for line in (evidence / "trace.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        commands = [e["item"] for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "command_execution"]
        maximum = max((len(c.get("aggregated_output", "").encode("utf-8")) for c in commands), default=0)
        altered = [name for name, value in trial["protected_sha256"].items()
                   if not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != value]
        try:
            answer = json.loads((root / "answer.json").read_text(encoding="utf-8-sig"))
            accurate = set(answer) == {"errors"} and sorted(answer["errors"], key=lambda x: x["code"]) == trial["expected"]
        except (OSError, ValueError, KeyError, TypeError):
            accurate = False
        status = None
        if trial["arm"] == "B" and (root / ".agent-workflow/state.sqlite").is_file():
            with closing(sqlite3.connect((root / ".agent-workflow/state.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT status FROM tasks WHERE id='holdout'").fetchone()
                status = row[0] if row else None
        usage = [e["usage"] for e in events if e.get("type") == "turn.completed" and "usage" in e]
        sessions = [e["thread_id"] for e in events if e.get("type") == "thread.started"]
        try:
            environment = json.loads((root / "environment.json").read_text(encoding="utf-8-sig"))
            if not all(type(value) is bool for value in environment.values()):
                environment = {"invalid": True}
        except (OSError, ValueError):
            environment = {}
        passed = (accurate and not altered and maximum < trial["max_command_output_bytes"] and not outcome["timed_out"]
                  and outcome["exit_code"] == 0 and (trial["arm"] != "B" or status == "completed"))
        rows.append({"trial_id": trial["trial_id"], "session_id": sessions[0] if len(sessions) == 1 else None,
                     "passed": passed, "answer_correct": accurate, "altered_files": altered,
                     "largest_command_output_bytes": maximum, "workflow_status": status,
                     "elapsed_seconds": outcome["elapsed_seconds"], "command_calls": len(commands),
                     "native_usage": usage, "environment_presence_only": environment,
                     "model": outcome["model"], "effort": outcome["effort"], "client": outcome["client_version"]})
    return {"passed": all(row["passed"] for row in rows),
            "unique_fresh_sessions": len({row["session_id"] for row in rows}) == len(rows),
            "trials": rows, "limitations": plan["limitations"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report; previous evidence is retained")
    result = grade(args.plan.resolve())
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"passed": result["passed"], "unique_fresh_sessions": result["unique_fresh_sessions"],
                      "trials": [{k: row[k] for k in ("trial_id", "passed", "largest_command_output_bytes", "workflow_status")} for row in result["trials"]]}))
    return 0 if result["passed"] and result["unique_fresh_sessions"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
