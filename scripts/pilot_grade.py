"""Collect native traces and independently rerun frozen heavy-pilot checkers."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET


def config_checks(root, scenario):
    names = ("before", "after") if scenario == "revision-3d" else ("hero", "front")
    configs = {name: json.loads((root / "configs" / (name + ".json")).read_text(encoding="utf-8")) for name in names}
    rows = []
    if scenario == "revision-3d":
        defaults = {"camera": "three-quarter", "finish": "studio", "samples": 64, "light": 1,
                    "padding_pct": 12, "yaw_deg": 0, "roughness": 0.45, "color": "#f2f2f2"}
        rows.append({"name": "consistent_revision_settings",
                     "passed": all(configs["before"].get(key, value) == configs["after"].get(key, value) for key, value in defaults.items())})
    else:
        rows.append({"name": "hero_three_quarter_studio",
                     "passed": configs["hero"].get("camera", "three-quarter") == "three-quarter" and configs["hero"].get("finish", "studio") == "studio"})
        rows.append({"name": "front_frontal_flat",
                     "passed": configs["front"].get("camera") == "front" and configs["front"].get("finish") == "flat"})
    if scenario == "packaging-3d":
        artwork = configs["hero"].get("artwork", {})
        side = artwork.get("right") if isinstance(artwork, dict) else None
        side_path = (root / "configs" / side).resolve() if side else None
        valid = side_path is not None and side_path.is_relative_to(root.resolve()) and side_path.is_file()
        text = " ".join(" ".join(ET.fromstring(side_path.read_text(encoding="utf-8")).itertext()).split()) if valid else ""
        rows.append({"name": "distinct_right_side_copy", "passed": valid and "SEDUH 3 MENIT" in text and side != artwork.get("front")})
    return rows


def grade_trial(plan_path, trial_id):
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    spec = next(row for row in plan["trials"] if row["trial_id"] == trial_id)
    root = plan_path.parent / trial_id
    evidence = root / "evidence/native-session"
    outcome = json.loads((evidence / "outcome.json").read_text(encoding="utf-8"))
    events = []
    for line in (evidence / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            # Interrupted final JSONL line is not an invented successful event.
            continue
    sessions = [r["thread_id"] for r in events if r.get("type") == "thread.started"]
    if len(sessions) != 1:
        raise ValueError("Expected one fresh native thread ID in trace")
    altered = []
    for name, expected in spec["protected_sha256"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            altered.append(name)
    commands = [r["item"] for r in events if r.get("type") == "item.completed" and r.get("item", {}).get("type") == "command_execution"]
    usage = [r["usage"] for r in events if r.get("type") == "turn.completed" and "usage" in r]
    workflow_status = None
    if spec["arm"] == "B" and (root / ".agent-workflow/state.sqlite").is_file():
        uri = (root / ".agent-workflow/state.sqlite").resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            row = db.execute("SELECT status FROM tasks WHERE id='design-trial'").fetchone()
            workflow_status = row[0] if row else None
    violations = len(altered) + int(spec["arm"] == "A" and (root / ".agent-workflow").exists())
    guidance_findings = []
    if spec["arm"] == "B":
        for row in commands:
            command = row.get("command", "")
            count = len(row.get("aggregated_output", "").encode())
            if "Get-Content" in command and "diagnostic.txt" in command and count > 64000:
                guidance_findings.append({"criterion": "bounded_output_guidance", "output_bytes": count,
                                          "command": command, "finding": "Large existing diagnostic log returned inline after workflow skill was read"})
        violations += len(guidance_findings)
    checker_result = None
    checker_exit = None
    # Never execute a checker or dependency the trial altered.
    if not altered:
        checked = subprocess.run([sys.executable, "check_delivery.py"], cwd=root, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=90)
        checker_exit = checked.returncode
        checker_result = json.loads(checked.stdout) if checked.stdout.strip().startswith("{") else {"passed": False, "diagnostic": checked.stderr}
        (evidence / "independent-check.stdout.txt").write_text(checked.stdout, encoding="utf-8")
        (evidence / "independent-check.stderr.txt").write_text(checked.stderr, encoding="utf-8")
    failed_commands = [row for row in commands if row.get("exit_code") not in (0, None)]
    seen_failed, repeated_after_failure = set(), 0
    for row in commands:
        command = row.get("command", "")
        if command in seen_failed:
            repeated_after_failure += 1
        if row.get("exit_code") not in (0, None):
            seen_failed.add(command)
    semantic_checks = []
    if checker_exit == 0:
        semantic_checks = config_checks(root, spec["scenario"])
    success = (outcome["exit_code"] == 0 and not outcome["timed_out"] and not altered
               and checker_exit == 0 and (spec["arm"] != "B" or workflow_status == "completed"))
    success = success and all(row["passed"] for row in semantic_checks)
    result = {
        "trial_id": trial_id, "session_id": sessions[0],
        **{name: outcome[name] for name in ("model", "effort", "client_version", "tool_catalog_sha256", "elapsed_seconds")},
        "success": None, "machine_success": bool(success), "rubric_evidence": "evidence/native-session/grade.json",
        "tool_calls": sum(r.get("type") == "item.completed" and r.get("item", {}).get("type") not in ("agent_message", "reasoning")
                          for r in events),
        "retries": repeated_after_failure, "user_interventions": 0, "instruction_violations": violations,
        "native_tokens": sum(row.get("input_tokens", 0) + row.get("output_tokens", 0) for row in usage) if usage else None,
        "native_usage": usage, "workflow_status": workflow_status, "altered_protected_files": altered,
        "checker_exit": checker_exit, "checker": checker_result, "semantic_checks": semantic_checks, "native_exit": outcome["exit_code"],
        "command_failures": len(failed_commands), "command_output_bytes": sum(len(r.get("aggregated_output", "").encode()) for r in commands),
        "largest_command_output_bytes": max((len(r.get("aggregated_output", "").encode()) for r in commands), default=0),
        "retry_definition": "Exact command repeated after a prior nonzero outcome; not model/network retry telemetry",
        "catalog_scope": outcome["catalog_scope"], "visual_review": "pending",
        "guidance_findings": guidance_findings,
        "violation_scope": "Protected files, arm misuse and observed large diagnostic inline reads on workflow arm; not a proof of every native instruction",
        "limitations": "Machine checks and trace integrity only. Human/native claims may still be wrong; visual review is required.",
    }
    (evidence / "grade.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--trial")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if args.trial:
            result = grade_trial(args.plan, args.trial)
        else:
            if args.output is None or args.output.exists():
                raise ValueError("Choose a new --output for collected trial records")
            result = {"trials": [grade_trial(args.plan, row["trial_id"]) for row in plan["trials"]]}
            args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, StopIteration, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
