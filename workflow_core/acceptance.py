"""Task acceptance on real work: outcome rates from recorded tasks, not from fixtures or claims."""
from datetime import datetime
import statistics

from .records import events


def report(engine, workflow=None):
    rows = engine.store.connection.execute("SELECT id FROM tasks ORDER BY created").fetchall()
    tasks = [engine.store.task(row["id"]) for row in rows]
    if workflow:
        tasks = [t for t in tasks if t["spec"]["workflow"] == workflow]
    harness = {e["task_id"] for e in events(engine.store, ("output_captured",))
               if e.get("verifier") == "harness_process" and e.get("exit_code") == 0}
    unknown = {e["task_id"] for e in events(engine.store, ("outcome_unknown",))}
    by_status, minutes, per_workflow = {}, [], {}
    for task in tasks:
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
        kind = per_workflow.setdefault(task["spec"]["workflow"], {"tasks": 0, "completed": 0})
        kind["tasks"] += 1
        kind["completed"] += task["status"] == "completed"
        if task["status"] in ("completed", "partial"):
            span = datetime.fromisoformat(task["updated"]) - datetime.fromisoformat(task["created"])
            minutes.append(span.total_seconds() / 60)
    decided = [t for t in tasks if t["status"] in ("completed", "partial")]
    completed = [t for t in decided if t["status"] == "completed"]
    rate = lambda part, whole: round(len(part) / len(whole), 3) if whole else None  # noqa: E731
    return {"tasks": len(tasks), "by_status": by_status, "decided": len(decided),
            "acceptance_rate": rate(completed, decided),
            "first_pass_rate": rate([t for t in completed if t["repairs"] == 0], decided),
            "harness_verified_completed": sum(t["id"] in harness for t in completed),
            "outcome_unknown_tasks": len(unknown & {t["id"] for t in tasks}),
            "median_minutes_to_decision": round(statistics.median(minutes), 1) if minutes else None,
            "per_workflow": per_workflow,
            "caveat": "Acceptance = validators + native review recorded by the chat; not an independent owner rating."}
