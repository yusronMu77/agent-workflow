"""Explicit opt-in native Codex benchmark harness, not a workflow executor."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def native_settings():
    try:
        import tomllib
    except ImportError as error:
        raise ValueError("Optional native benchmark harness needs Python 3.11+; core still supports 3.10+") from error
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    path = home / "config.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cli = shutil.which("codex")
    if not cli:
        raise ValueError("Codex CLI unavailable")
    version = subprocess.run([cli, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    # Do not copy configuration, provider credentials, URLs or auth databases.
    catalog = {"client": version, "mcp_names": sorted(config.get("mcp_servers", {})),
               "native_tools": "CLI built-ins; same user config, workspace-write sandbox and never approvals",
               "design_apps_root": os.environ.get("DESIGN_APPS_ROOT"),
               "blender": os.environ.get("BLENDER")}
    return cli, {"model": config.get("model", "client-default-unrecorded"),
                 "effort": config.get("model_reasoning_effort", "client-default-unrecorded"),
                 "client_version": version,
                 "tool_catalog_sha256": hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest(),
                 "catalog_scope": "Configured names/settings fingerprint, not a protocol dump of every exposed tool",
                 "catalog": catalog}


def run_trial(plan_path, trial_id, timeout=720):
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    spec = next(row for row in plan["trials"] if row["trial_id"] == trial_id)
    root = plan_path.parent / trial_id
    if root.resolve().parent != plan_path.parent:
        raise ValueError("Trial must be directly within plan directory")
    for name, expected in spec["protected_sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Trial snapshot changed before execution: " + name)
    evidence = root / "evidence/native-session"
    if evidence.exists():
        raise ValueError("Native session already attempted; inspect it, never silently replay")
    prompt = (plan_path.parent / (trial_id + ".prompt.txt")).read_text(encoding="utf-8")
    cli, settings = native_settings()
    evidence.mkdir(parents=True)
    metadata = {"trial_id": trial_id, **settings, "started": datetime.now(timezone.utc).isoformat(),
                "timeout_seconds": timeout, "sandbox": "workspace-write", "approval_policy": "never"}
    (evidence / "invocation.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    argv = [cli, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write",
            "-c", 'approval_policy="never"', "--cd", str(root), "--output-last-message",
            str(evidence / "final.txt"), "-"]
    started = time.monotonic()
    timed_out = False
    with (evidence / "trace.jsonl").open("wb") as stdout, (evidence / "stderr.txt").open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
        try:
            process.communicate(prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Only this benchmark's owned process tree. A timeout is recorded,
            # never resumed or replayed by this harness.
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            else:
                process.kill()
            process.wait()
    metadata.update(exit_code=process.returncode, timed_out=timed_out,
                    elapsed_seconds=round(time.monotonic() - started, 3),
                    finished=datetime.now(timezone.utc).isoformat())
    (evidence / "outcome.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--authorized-native-sessions", action="store_true")
    parser.add_argument("--timeout", type=int, default=720)
    args = parser.parse_args(argv)
    try:
        if not args.authorized_native_sessions:
            raise ValueError("Requires explicit user authorization and --authorized-native-sessions")
        if not 60 <= args.timeout <= 1800:
            raise ValueError("timeout must be 60..1800 seconds")
        result = run_trial(args.plan, args.trial, args.timeout)
        print(json.dumps(result))
        return 3 if result["timed_out"] or result["exit_code"] else 0
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
