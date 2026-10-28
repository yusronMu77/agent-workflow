"""Force interleaving of independent processes writing the same owned artifact."""
import argparse
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.engine import Engine
from workflow_core.project import Project, Rejected
from contextlib import closing
import sqlite3


def writer(target, data, barrier, queue):
    original = Path.replace
    hits = 0
    def interleave(path, destination):
        nonlocal hits
        if Path(destination) == Path(target) and hits == 0:
            hits += 1
            barrier.wait(timeout=20)
        return original(path, destination)
    Path.replace = interleave
    try:
        Engine.write_owned(None, Path(target), data)
        queue.put({"status": "written", "barrier_hits": hits})
    except Exception as error:
        queue.put({"status": "failed", "type": type(error).__name__, "barrier_hits": hits})


def probe(rounds=3):
    root = Path(tempfile.mkdtemp(prefix="workflow-io-qualification-")).resolve()
    context = multiprocessing.get_context("spawn")
    records = []
    for index in range(rounds):
        target = root / f"artifact-{index}.txt"
        bodies = [(f"writer-{i}\n" * 20000).encode() for i in range(2)]
        barrier, queue = context.Barrier(2), context.Queue()
        processes = [context.Process(target=writer, args=(str(target), body, barrier, queue)) for body in bodies]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=35)
            if process.is_alive():
                process.terminate()
                process.join()
        results = []
        for _ in processes:
            try:
                results.append(queue.get(timeout=3))
            except Exception:
                results.append({"status": "missing"})
        body = target.read_bytes() if target.exists() else b""
        records.append({"round": index, "writers": results, "whole_artifact": body in bodies,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "temporary_files": [p.name for p in root.glob(target.name + "*.tmp")]})
        queue.close()
        queue.join_thread()
    return {"passed": all(all(w["status"] == "written" and w["barrier_hits"] == 1 for w in r["writers"]) and r["whole_artifact"]
                         and not r["temporary_files"] for r in records),
            "records": records, "fixture": str(root),
            "scope": "Atomic whole-file writes under forced interleaving; not semantic coordination of task owners"}


def reservation_writer(root, task_id, barrier, queue):
    engine = None
    try:
        engine = Engine(Path(root))
        barrier.wait(timeout=20)
        engine.create(task_id, "Concurrent reservation qualification", "code", "project:shared.json")
        queue.put({"status": "created", "task": task_id})
    except Rejected as error:
        queue.put({"status": "rejected", "reason": str(error)})
    except Exception as error:
        queue.put({"status": "failed", "type": type(error).__name__})
    finally:
        if engine:
            engine.close()


def probe_reservations():
    root = Path(tempfile.mkdtemp(prefix="workflow-reservation-qualification-")).resolve()
    Project.init(root)
    engine = Engine(root)
    database = engine.project.db
    engine.close()
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(2), context.Queue()
    processes = [context.Process(target=reservation_writer, args=(str(root), f"writer{i}", barrier, queue)) for i in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=35)
        if process.is_alive():
            process.terminate()
            process.join()
    results = [queue.get(timeout=3) for _ in processes]
    queue.close()
    queue.join_thread()
    with closing(sqlite3.connect(database)) as db:
        counts = {name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
                  for name in ("tasks", "reservations", "events")}
        check = db.execute("PRAGMA quick_check").fetchone()[0]
    passed = (sorted(r["status"] for r in results) == ["created", "rejected"]
              and counts == {"tasks": 1, "reservations": 1, "events": 1} and check == "ok")
    return {"passed": passed, "writers": results, "counts": counts, "database_check": check,
            "fixture": str(root), "scope": "Two independent processes contend for one output; only one task/state/event commits"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.rounds <= 20:
        parser.error("Choose a new output and 1..20 rounds")
    result = probe(args.rounds)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"passed": result["passed"], "rounds": len(result["records"]), "output": str(args.output)}))
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    sys.exit(main())
