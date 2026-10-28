"""Process/job ownership and host resource admission (roadmap M1 remainder)."""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .project import Rejected, plain_path
from .records import checked_name, emit, events, fold, new_id

KINDS = ("job_started", "job_finished", "job_stopped", "job_lost")
DEFAULTS = {"max_jobs": 2, "min_free_mb": 1024}
WRAPPER = Path(__file__).resolve().parent / "job_wrapper.py"


def free_memory_mb():
    """Available physical memory in MB, or None when this platform is not supported."""
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                        ("page_available", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                        ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available // (1024 * 1024)
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def limits(engine):
    path = plain_path(engine.project.home / "host.json")
    configured = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {**DEFAULTS, **{k: v for k, v in configured.items() if k in DEFAULTS and type(v) is int and v >= 0}}


def folder(engine, job_id):
    checked_name(job_id, "job id")
    return plain_path(engine.project.home / "jobs" / job_id)


def observe(engine, job):
    """Current state of one recorded job from its owned folder and process table."""
    path = folder(engine, job["id"])
    if job["event_kind"] in ("job_finished", "job_stopped", "job_lost"):
        return job["event_kind"][4:]
    if (path / "exit.json").is_file():
        return "finished"
    return "running" if alive(job.get("pid")) else "lost"


def listing(engine):
    jobs = fold(engine.store, KINDS)
    for job in jobs.values():
        job["state"] = observe(engine, job)
    return jobs


def admission(engine, extra=0):
    limit = limits(engine)
    running = [j for j in listing(engine).values() if j["state"] == "running"]
    free = free_memory_mb()
    reasons = []
    if len(running) + extra > limit["max_jobs"]:
        reasons.append(f"{len(running)} owned jobs running; limit {limit['max_jobs']}")
    if free is not None and free < limit["min_free_mb"]:
        reasons.append(f"{free} MB free memory; minimum {limit['min_free_mb']} MB")
    return {"admitted": not reasons, "reasons": reasons, "running_jobs": len(running), "free_memory_mb": free,
            "cpu_count": os.cpu_count(), "limits": limit, "memory_probe": "unsupported" if free is None else "ok"}


def start(engine, task_id, argv, label="job"):
    if not argv:
        raise Rejected("Give the job command after --")
    if task_id is not None:
        engine.guard(task_id)
    decision = admission(engine, extra=1)
    if not decision["admitted"]:
        raise Rejected("Host admission refused: " + "; ".join(decision["reasons"]))
    job_id = new_id("job")
    path = folder(engine, job_id)
    path.mkdir(parents=True)
    # CREATE_NO_WINDOW (not DETACHED_PROCESS): the wrapper gets a hidden console that console
    # children inherit, so Windows never opens a visible terminal window per job.
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" \
        else {"start_new_session": True}
    process = subprocess.Popen([sys.executable, str(WRAPPER), str(path), "--", *argv], cwd=engine.project.root,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    payload = {"id": job_id, "pid": process.pid, "argv": list(argv), "label": label, "owner_task": task_id,
               "admission": {k: decision[k] for k in ("running_jobs", "free_memory_mb", "cpu_count")}}
    emit(engine.store, task_id, "job_started", payload)
    return {"status": "started", **payload, "log": str(path / "log.txt")}


def stop(engine, job_id, note):
    job = listing(engine).get(job_id)
    if job is None:
        raise Rejected("Job not owned by this project; refusing to touch unrecorded processes")
    if job["state"] != "running" or not note.strip():
        raise Rejected("Only a running owned job can be stopped, with a note")
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(job["pid"]), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(job["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    # Return only after the tree is gone, so its log file is no longer held open.
    child = folder(engine, job_id) / "child.json"
    pids = [job["pid"]] + ([json.loads(child.read_text(encoding="utf-8"))["pid"]] if child.is_file() else [])
    deadline = time.monotonic() + 10
    while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    emit(engine.store, job.get("owner_task"), "job_stopped", {"id": job_id, "note": note})
    return {"status": "stopped", "id": job_id}


def reap(engine):
    """Record terminal states once so later listings do not depend on the process table."""
    recorded = 0
    for job_id, job in listing(engine).items():
        if job["event_kind"] != "job_started" or job["state"] == "running":
            continue
        exit_file = folder(engine, job_id) / "exit.json"
        code = json.loads(exit_file.read_text(encoding="utf-8"))["exit_code"] if exit_file.is_file() else None
        emit(engine.store, job.get("owner_task"), "job_finished" if job["state"] == "finished" else "job_lost",
             {"id": job_id, "exit_code": code})
        recorded += 1
    return {"status": "reaped", "recorded": recorded}


def show(engine):
    jobs = listing(engine)
    return {"jobs": [{k: j.get(k) for k in ("id", "state", "pid", "argv", "label", "owner_task", "exit_code")}
                     for j in jobs.values()][-50:], "admission": admission(engine), "events": len(events(engine.store, KINDS))}
