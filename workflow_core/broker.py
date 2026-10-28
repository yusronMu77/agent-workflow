"""MCP lifecycle broker (roadmap M3): leases per target, shared server jobs, idle eviction.

Only servers the broker can start itself (local HTTP MCP with a start argv in
.agent-workflow/broker.json) are managed. Stdio servers belong to the client that spawns them.
"""
from datetime import datetime, timedelta, timezone
import json

from .project import Rejected, plain_path
from .records import checked_name, emit, fold, new_id
from . import jobs

KINDS = ("mcp_leased", "mcp_released")


def config(engine):
    path = plain_path(engine.project.home / "broker.json")
    if not path.is_file():
        return {"servers": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def register(engine, server, argv, url):
    checked_name(server, "server name")
    if not argv or not url.startswith(("http://127.0.0.1", "http://localhost")):
        raise Rejected("Broker manages local HTTP servers only: give -- start argv and a localhost --url")
    data = config(engine)
    data["servers"][server] = {"argv": list(argv), "url": url}
    engine.write_owned(plain_path(engine.project.home / "broker.json"), (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    return {"status": "registered", "server": server, "url": url}


def server_job(engine, server):
    running = [j for j in jobs.listing(engine).values() if j.get("label") == "mcp:" + server and j["state"] == "running"]
    return running[-1] if running else None


def leases(engine):
    return {k: v for k, v in fold(engine.store, KINDS).items() if v["event_kind"] == "mcp_leased"}


def lease(engine, server, task_id=None):
    entry = config(engine)["servers"].get(server)
    if not entry:
        raise Rejected("Server not registered with the broker")
    job = server_job(engine, server)
    started = False
    if job is None:
        job = jobs.start(engine, None, entry["argv"], label="mcp:" + server)
        started = True
    lease_id = new_id("lease")
    emit(engine.store, task_id, "mcp_leased", {"id": lease_id, "server": server, "job": job["id"], "target": task_id})
    return {"status": "leased", "lease": lease_id, "server": server, "url": entry["url"], "job": job["id"],
            "started": started}


def release(engine, lease_id):
    active = leases(engine).get(lease_id)
    if active is None:
        raise Rejected("Lease not active")
    emit(engine.store, active.get("target"), "mcp_released", {"id": lease_id, "server": active["server"]})
    return {"status": "released", "lease": lease_id}


def evict(engine, idle_minutes=15, now=None):
    now = now or datetime.now(timezone.utc)
    held = {v["server"] for v in leases(engine).values()}
    last_use = {}
    for record in fold(engine.store, KINDS).values():
        last_use[record["server"]] = max(last_use.get(record["server"], ""), record["history"][-1]["created"])
    evicted = []
    for server in config(engine)["servers"]:
        job = server_job(engine, server)
        if job is None or server in held:
            continue
        last = datetime.fromisoformat(last_use.get(server) or job["history"][0]["created"])
        if now - last >= timedelta(minutes=idle_minutes):
            jobs.stop(engine, job["id"], f"idle eviction after {idle_minutes} minutes without leases")
            evicted.append(server)
    return {"status": "evicted" if evicted else "nothing_idle", "evicted": evicted, "held": sorted(held)}
