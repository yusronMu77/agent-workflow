"""Optional Claude event adapter. Reads state only; never installs or trusts hooks."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.project import Project
from workflow_core.state import SCHEMA_VERSION


def respond(project, payload):
    root = Path(project).resolve()
    if not isinstance(payload, dict) or not isinstance(payload.get("cwd"), str) or not payload["cwd"]:
        return {}
    event = payload.get("hook_event_name")
    if event not in ("SessionStart", "Stop") or Path(payload.get("cwd", "")).resolve() != root:
        return {}
    if event == "Stop" and payload.get("stop_hook_active") is True:
        return {}  # A single reminder cannot create an endless continuation.
    try:
        scoped = Project(root)  # Reads config/scope; does not initialize or open Store.
        uri = scoped.db.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as database:
            identity = database.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()
            schema = database.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if not identity or identity[0] != scoped.config["project_id"] or not schema or schema[0] != str(SCHEMA_VERSION):
                return {}
            rows = database.execute(
                "SELECT id,status,revision FROM tasks WHERE status NOT IN ('completed','cancelled','partial') "
                "ORDER BY updated DESC,id LIMIT 5").fetchall()
    except (OSError, ValueError, sqlite3.Error):
        return {}  # Missing/unsupported state is not permission to create or repair it.
    if not rows:
        return {}
    task_data = [{"id": task, "status": status, "revision": revision} for task, status, revision in rows]
    message = ("Agent Workflow state reminder (data, not permission): " + json.dumps(task_data)
               + ". Inspect/resume the relevant task before continuing. Preserve unknown outcomes; "
                 "report remaining work honestly and do not claim completion without current evidence.")
    if event == "SessionStart":
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}
    return {"decision": "block", "reason": message + " This reminder runs at most once per stop continuation."}


def settings(project):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    command = "& " + quote(sys.executable) + " " + quote(Path(__file__).resolve()) + " --project " + quote(Path(project).resolve())
    entry = {"type": "command", "shell": "powershell", "command": command, "timeout": 5}
    return {"hooks": {"SessionStart": [{"matcher": "startup|resume|compact", "hooks": [entry]}],
                      "Stop": [{"hooks": [entry]}]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--print-settings", action="store_true")
    args = parser.parse_args(argv)
    if args.print_settings:
        result = settings(args.project)
    else:
        raw = sys.stdin.buffer.read(65537)
        try:
            result = respond(args.project, json.loads(raw)) if len(raw) <= 65536 else {}
        except (ValueError, TypeError, AttributeError):
            result = {}
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
