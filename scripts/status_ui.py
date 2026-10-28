"""Read-only local status page for one project: tasks, proposals, jobs, changes, routes.

Binds to 127.0.0.1 only and exposes no write endpoint. Optional; nothing starts it automatically.
"""
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core import changes, jobs, routing  # noqa: E402
from workflow_core.engine import Engine  # noqa: E402


def snapshot(project):
    engine = Engine(project)
    try:
        db = engine.store.connection
        return {"tasks": [dict(r) for r in db.execute(
                    "SELECT id,status,revision,repairs,next_action,updated FROM tasks ORDER BY updated DESC LIMIT 50")],
                "proposals": [dict(r) for r in db.execute("SELECT id,kind,status FROM proposals ORDER BY rowid DESC LIMIT 50")],
                "jobs": jobs.show(engine)["jobs"], "changes": changes.listing(engine)["changes"],
                "routes": routing.history(engine.store)["routes"][-20:]}
    finally:
        engine.close()


def render(data):
    sections = []
    for name, rows in data.items():
        if not rows:
            sections.append(f"<h2>{name}</h2><p>none</p>")
            continue
        keys = [k for k in rows[0] if k != "history"]
        head = "".join(f"<th>{html.escape(k)}</th>" for k in keys)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(k, '')))}</td>" for k in keys) + "</tr>" for r in rows)
        sections.append(f"<h2>{name}</h2><table><tr>{head}</tr>{body}</table>")
    return ("<!doctype html><meta charset=utf-8><title>Workflow status</title>"
            "<style>body{font:14px system-ui;margin:16px}table{border-collapse:collapse}td,th{border:1px solid #888;padding:4px}</style>"
            "<h1>Workflow status (read-only)</h1>" + "".join(sections)).encode("utf-8")


def handler(project):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = snapshot(project)
            if self.path.startswith("/api"):
                body, kind = json.dumps(data, default=str).encode("utf-8"), "application/json"
            else:
                body, kind = render(data), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(args.project))
    print(json.dumps({"status": "serving", "url": f"http://127.0.0.1:{args.port}/", "writes": "none"}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
