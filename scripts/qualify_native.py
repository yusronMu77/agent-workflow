"""Prepare and execute explicitly authorized native holdout qualifications."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys

from build_release import product_files
from pilot_native import run_trial


def prepare(root):
    root = root.resolve()
    if root.exists():
        raise ValueError("Choose a new holdout root; never overwrite a previous attempt")
    for parent in root.parents:
        if (parent / "CLAUDE.md").exists() or (parent / "AGENTS.md").exists():
            raise ValueError("Use a directory outside agent workspaces")
    product = dict(product_files())
    root.mkdir(parents=True)
    trials = []
    for index, scenario in enumerate(("plain-log", "unicode-log", "mixed-log")):
        expected = [{"code": "E702", "file": "renderer.py", "line": 73, "expected": "RGBA", "actual": "RGB"},
                    {"code": "E941", "file": "uv_map.py", "line": 218, "expected": "front", "actual": "back"}]
        content = [f"INFO frame {n:05d}: material and geometry cache ready.\n" for n in range(14000)]
        content[2631 + index * 17] = "ERROR E702 renderer.py:73 expected=RGBA actual=RGB\n"
        content[12419 - index * 21] = "ERROR E941 uv_map.py:218 expected=front actual=back\n"
        if index == 1:
            content[7061] = "INFO catatan 日本語 😀: bukan error, warna tetap.\n"
        if index == 2:
            content[8117] = "WARN previous ERROR E000 was resolved; not a current ERROR record.\n"
        log = "".join(content)
        for arm in ("A", "B"):
            trial = scenario + "-" + arm
            directory = root / trial
            directory.mkdir()
            files = {"AGENTS.md": "Work within this fixture. Preserve input and tools. No downloads, external writes or agents.\n",
                     "diagnostic.txt": log,
                     "brief.json": json.dumps({"scenario": scenario, "expected": expected}, indent=2)}
            for name, text in files.items():
                (directory / name).write_text(text, encoding="utf-8", newline="\n")
            for name, body in product.items():
                path = directory / "tools/agent-workflow" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            prompt = (
                "Identify current records that START with ERROR in diagnostic.txt. Do not count resolved warnings. "
                "Write answer.json as {errors:[{code,file,line,expected,actual}]} and no extra prose fields. "
                "Preserve original data/tools. Inspect file metadata first and use scoped search; do not dump the whole log. "
                "Each command output must stay below 16000 UTF-8 bytes. "
                "Validate exact error rows using the supplied brief.json and save check.log. "
                "Record ONLY booleans for presence of CODEX_THREAD_ID, CODEX_SESSION_ID, CODEX_CI in environment.json; never print env values. "
                "No network, installation, agents or changes outside this fixture. Time budget five minutes.\n")
            if arm == "B":
                prompt = (
                    "BEFORE inspecting task data, read tools/agent-workflow/skills/agent-workflow/SKILL.md. "
                    "Then initialize Agent Workflow, begin task holdout --workflow code --output project:answer.json "
                    "--check json --check capture_exit_zero --minutes 5, pack and read its context. "
                    "After that, perform the following task; capture the actual final check exit code, "
                    "record evidence and final review, then verify.\n" + prompt)
            else:
                prompt = "Use native tools directly without Agent Workflow.\n" + prompt
            (root / (trial + ".prompt.txt")).write_text(prompt, encoding="utf-8", newline="\n")
            trials.append({"trial_id": trial, "scenario": scenario, "arm": arm,
                           "protected_sha256": {**{n: hashlib.sha256(t.encode()).hexdigest() for n, t in files.items()},
                                                **{"tools/agent-workflow/" + n: hashlib.sha256(b).hexdigest() for n, b in product.items()}},
                           "expected": expected, "max_command_output_bytes": 16000})
    random.Random(71).shuffle(trials)
    plan = {"schema": 1, "suite": "held-out-log-scoping", "seed": 71, "trials": trials,
            "acceptance": "Correct answer; immutable inputs/tools; all command outputs <16000 bytes; B workflow completed",
            "limitations": "New log holdouts, not an A/B estimate of the prior 3D tasks; no billing/causal savings claim"}
    (root / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-authorized-native-sessions", action="store_true")
    args = parser.parse_args()
    plan = prepare(args.output)
    print(json.dumps({"status": "prepared", "trials": len(plan["trials"])}), flush=True)
    if args.run_authorized_native_sessions:
        for trial in plan["trials"]:
            print(json.dumps({"starting": trial["trial_id"]}), flush=True)
            result = run_trial(args.output / "plan.json", trial["trial_id"], 300)
            print(json.dumps({k: result[k] for k in ("trial_id", "elapsed_seconds", "exit_code", "timed_out")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
