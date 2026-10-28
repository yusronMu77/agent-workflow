"""Per-MCP/tool health: configuration, auth presence and an optional real initialize/tools-list probe."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

from .project import jsonc

VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "agent-workflow-health", "version": "1"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def servers(config):
    data = jsonc(Path(config))
    return data.get("mcpServers") or data.get("servers") or data.get("mcp", {}).get("servers") or {}


def expand(value, env):
    return VARIABLE.sub(lambda m: env.get(m.group(1), ""), value)


def static(name, server, env):
    """Never returns secret values: only variable names and whether they are set."""
    text = json.dumps(server)
    variables = sorted(set(VARIABLE.findall(text)))
    literal = [k for k, v in server.get("env", {}).items() if isinstance(v, str) and v and not VARIABLE.search(v)]
    report = {"server": name, "transport": "http" if server.get("url") else "stdio",
              "auth_variables": {v: bool(env.get(v)) for v in variables}, "literal_env_keys": literal}
    if server.get("url"):
        report["config"] = "ok" if re.match(r"https?://", server["url"]) else "invalid_url"
    else:
        command = server.get("command", "")
        found = shutil.which(command) or (Path(command).is_file() and command)
        report["config"] = "ok" if found else "command_missing"
    report["auth"] = "missing_variable" if not all(report["auth_variables"].values()) else "configured"
    return report


def read_message(stream, wanted_id, timeout):
    box = {}

    def reader():
        for line in stream:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == wanted_id:
                box["message"] = message
                return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(timeout)
    return box.get("message")


def probe_stdio(server, env, timeout):
    command = [shutil.which(server["command"]) or server["command"], *[expand(str(a), env) for a in server.get("args", [])]]
    child_env = dict(env, **{k: expand(str(v), env) for k, v in server.get("env", {}).items()})
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=child_env, cwd=server.get("cwd"), text=True, encoding="utf-8", errors="replace")
    try:
        process.stdin.write(json.dumps(INITIALIZE) + "\n")
        process.stdin.flush()
        first = read_message(process.stdout, 1, timeout)
        if not first or "result" not in first:
            return {"probe": "no_initialize_response" if not first else "initialize_error"}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        process.stdin.write(json.dumps(LIST) + "\n")
        process.stdin.flush()
        tools = read_message(process.stdout, 2, timeout)
        count = len(tools.get("result", {}).get("tools", [])) if tools else None
        return {"probe": "ok" if count is not None else "tools_list_failed", "tools": count,
                "server_info": first["result"].get("serverInfo", {}).get("name")}
    except (OSError, ValueError) as error:
        return {"probe": "error", "error": type(error).__name__}
    finally:
        process.kill()
        process.wait(timeout=10)
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                pass


def probe_http(server, env, timeout):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               **{k: expand(str(v), env) for k, v in server.get("headers", {}).items()}}
    request = urllib.request.Request(expand(server["url"], env), data=json.dumps(INITIALIZE).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(200000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return {"probe": "auth_failed" if error.code in (401, 403) else "http_error", "http_status": error.code}
    except (urllib.error.URLError, OSError) as error:
        return {"probe": "unreachable", "error": type(error).__name__}
    payload = next((line[5:].strip() for line in body.splitlines() if line.startswith("data:")), body)
    try:
        ok = "result" in json.loads(payload)
    except ValueError:
        ok = False
    return {"probe": "ok" if ok else "initialize_error"}


def check(configs=(), probe=False, timeout=15, only=None, tools=("git", "codex", "claude", "rtk")):
    env = dict(os.environ)
    report = {"tools": {name: bool(shutil.which(name)) for name in tools}, "servers": []}
    for config in configs:
        for name, server in servers(config).items():
            if only and name not in only:
                continue
            item = static(name, server, env)
            item["source"] = Path(config).name
            if probe and item["config"] == "ok" and item["auth"] == "configured":
                item.update(probe_http(server, env, timeout) if server.get("url") else probe_stdio(server, env, timeout))
            elif probe:
                item["probe"] = "skipped_static_failure"
            report["servers"].append(item)
    bad = [s["server"] for s in report["servers"]
           if s["config"] != "ok" or s["auth"] != "configured" or s.get("probe", "ok") != "ok"]
    report["status"] = "ok" if not bad else "degraded"
    report["unhealthy"] = bad
    return report
