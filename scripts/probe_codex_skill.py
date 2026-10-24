"""Read-only native Codex skills/list probe; no thread, model call, or config/auth writes."""
import argparse
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--expected-path', type=Path, help='Require discovery from this exact skill file')
    args = parser.parse_args()
    root = args.project.resolve()
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI not found")
    process = subprocess.Popen([executable, "app-server", "--stdio"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                               encoding="utf-8", bufsize=1)
    incoming = queue.Queue()
    threading.Thread(target=lambda: [incoming.put(line) for line in process.stdout], daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def response(identifier):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                message = json.loads(incoming.get(timeout=min(1, max(.01, deadline - time.monotonic()))))
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError("Owned app-server exited before response")
                continue
            if message.get("id") == identifier:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]
        raise TimeoutError("skills/list timed out")

    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "workflow-skill-probe", "version": "0.1.0"}}})
        response(1)
        send({"method": "initialized"})
        send({"id": 2, "method": "skills/list", "params": {"cwds": [str(root)], "forceReload": True}})
        result = response(2)
        found = []

        def walk(value):
            if isinstance(value, dict):
                if str(value.get("name", "")).split(':')[-1] == "agent-workflow":
                    found.append({key: value[key] for key in ("name", "path", "enabled", "scope") if key in value})
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(result)
        if not found:
            errors = []
            def collect_errors(value):
                if isinstance(value, dict):
                    if 'errors' in value:
                        errors.append(value['errors'])
                    for child in value.values():
                        collect_errors(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_errors(child)
            collect_errors(result)
            raise RuntimeError('Agent Workflow not discovered; native errors: ' + json.dumps(errors))
        if args.expected_path and not any(Path(row.get('path', '')).resolve() == args.expected_path.resolve() for row in found):
            raise RuntimeError('Skill found, but not from expected plugin cache: ' + json.dumps(found))
        print(json.dumps({"status": "discovered", "skills": found, "inference_started": False}, indent=2))
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()  # Only this probe's owned process, never other Codex sessions.
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
