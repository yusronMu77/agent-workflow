"""Benchmark importer: measurement JSON -> events/proposals without manual transcription."""
import json

from .project import Rejected, digest
from .records import events

ROW_KEYS = ("results", "trials", "cases", "rows")
ID_KEYS = ("id", "trial_id", "case", "name", "scenario")


def rows(data):
    for key in ROW_KEYS:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    if isinstance(data, list):
        return data
    raise Rejected("Benchmark JSON needs a list or one of: " + ", ".join(ROW_KEYS))


def outcome(row):
    """True/False when the row states success, None when it only carries measurements."""
    for key in ("success", "passed", "ok", "acceptance"):
        if isinstance(row.get(key), bool):
            return row[key]
    if isinstance(row.get("exit_code"), int):
        return row["exit_code"] == 0
    if isinstance(row.get("status"), str):
        return row["status"] in ("passed", "completed", "ok", "success")
    return None


def import_file(engine, reference, task_id=None):
    snapshot = engine.project.snapshot(reference)
    data = json.loads(snapshot["text"])
    seen = {e["import_key"] for e in events(engine.store, ("benchmark_result", "benchmark_failed"))}
    imported = skipped = failed = 0
    with engine.store.tx() as db:
        for index, row in enumerate(rows(data)):
            if not isinstance(row, dict):
                raise Rejected(f"Benchmark row {index} is not an object")
            name = next((str(row[k]) for k in ID_KEYS if row.get(k) is not None), f"row-{index}")
            key = digest(f"{snapshot['sha256']}:{index}:{name}".encode("utf-8"))[:32]
            if key in seen:
                skipped += 1
                continue
            success = outcome(row)
            payload = {"import_key": key, "case": name, "success": success, "source": reference,
                       "source_sha256": snapshot["sha256"],
                       "metrics": {k: v for k, v in row.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}}
            engine.store.event(db, task_id, "benchmark_failed" if success is False else "benchmark_result", payload)
            imported += 1
            failed += success is False
    return {"status": "imported", "imported": imported, "failed": failed, "already_imported": skipped,
            "source_sha256": snapshot["sha256"], "next": "wf.py feedback --collect turns failures into proposals"}
