"""Local gateway (roadmap M6): an allowlisted JSON API over one project for other agents/providers.

Binds to 127.0.0.1 and requires a bearer token from AGENT_WORKFLOW_GATEWAY_TOKEN. Only read and
propose operations are exposed; activation, install, dispatch, job control and restore are not.
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core import acceptance, changes, cli_ext, jobs, routing  # noqa: E402
from workflow_core.engine import Engine  # noqa: E402
from workflow_core.feedback import collect, recall  # noqa: E402
from workflow_core.project import Rejected  # noqa: E402

OPERATIONS = {
    "list": lambda e, p: {"tasks": [dict(r) for r in e.store.connection.execute(
        "SELECT id,status,revision,repairs,next_action FROM tasks ORDER BY updated DESC LIMIT 50")]},
    "show": lambda e, p: e.export(p["task"]),
    "budget": lambda e, p: cli_ext.budget(e, p.get("task")),
    "acceptance": lambda e, p: acceptance.report(e, p.get("workflow")),
    "jobs": lambda e, p: jobs.show(e),
    "changes": lambda e, p: changes.listing(e),
    "routes": lambda e, p: routing.history(e.store, p.get("task")),
    "route_select": lambda e, p: routing.select(e, p["request"], p["routes"], p.get("task")),
    "feedback_collect": lambda e, p: collect(e.store),
    "memory_search": lambda e, p: recall(e.store, p.get("query", "")),
}


def call(project, operation, params):
    if operation not in OPERATIONS:
        raise Rejected("Operation not exposed by the gateway")
    engine = Engine(project)
    try:
        return OPERATIONS[operation](engine, params or {})
    finally:
        engine.close()


def handler(project, token):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data):
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
                return self.reply(401, {"status": "unauthorized"})
            try:
                request = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 1000000)))
                return self.reply(200, {"status": "ok", "result": call(project, request["operation"], request.get("params"))})
            except (Rejected, KeyError, ValueError, OSError) as error:
                return self.reply(400, {"status": "rejected", "reason": str(error)})

        def do_GET(self):
            self.reply(200, {"operations": sorted(OPERATIONS), "auth": "bearer", "writes": "propose_only"})

        def log_message(self, *args):
            pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    token = os.environ.get("AGENT_WORKFLOW_GATEWAY_TOKEN", "")
    if len(token) < 16:
        sys.exit("Set AGENT_WORKFLOW_GATEWAY_TOKEN (16+ characters) before starting the gateway")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(args.project, token))
    print(json.dumps({"status": "serving", "url": f"http://127.0.0.1:{args.port}/", "operations": sorted(OPERATIONS)}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
