import json
import uuid

from .project import Rejected, digest, plain_path
from .state import now


def collect(store):
    """At-least-once outbox -> idempotent proposals; no prompt/config writes."""
    count = 0
    with store.tx() as db:
        rows = db.execute("SELECT * FROM events WHERE delivered=0 ORDER BY created,id").fetchall()
        for row in rows:
            if row["kind"] in ("validation_failed", "outcome_unknown", "route_corrected"):
                proposal_id = digest(row["id"].encode("utf-8"))[:24]
                payload = {"hypothesis": "Inspect this verified event before proposing a targeted change",
                           "event_kind": row["kind"], "evidence": json.loads(row["payload"]),
                           "task_id": row["task_id"], "activation": "proposal_only"}
                count += db.execute("INSERT OR IGNORE INTO proposals(id,event_id,kind,payload) VALUES(?,?,?,?)",
                                    (proposal_id, row["id"], row["kind"], json.dumps(payload))).rowcount
            db.execute("UPDATE events SET delivered=1 WHERE id=?", (row["id"],))
    total = store.connection.execute("SELECT count(*) FROM proposals").fetchone()[0]
    return {"status": "collected", "new_proposals": count, "total_proposals": total, "auto_apply": False}


def decide(engine, proposal_id, decision, evidence, note):
    """Record a bounded triage decision, never activate or edit configuration."""
    if decision not in ("accept", "reject") or not note.strip() or len(note.encode("utf-8")) > 4000:
        raise Rejected("Choose accept/reject and a nonempty note of at most 4000 bytes")
    store = engine.store
    status = {"accept": "accepted", "reject": "rejected"}[decision]
    with store.tx() as db:
        row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise Rejected("Feedback proposal not found")
        payload = json.loads(row["payload"])
        task_id = payload.get("task_id")
        snapshot = engine.evidence_snapshot(task_id, evidence)
        record = {"proposal_id": proposal_id, "decision": decision, "status": status,
                  "evidence": snapshot, "note": note, "activation": "proposal_only"}
        if row["status"] != "proposed":
            previous = db.execute("SELECT payload FROM events WHERE kind='proposal_decided' ORDER BY created,rowid").fetchall()
            matched = any(json.loads(event[0]) == record for event in previous)
            if row["status"] == status and matched:
                return {"status": status, "proposal_id": proposal_id, "idempotent": True,
                        "auto_apply": False, "configuration_changed": False}
            raise Rejected("Proposal already decided; decisions are immutable, create a follow-up instead")
        db.execute("UPDATE proposals SET status=? WHERE id=? AND status='proposed'", (status, proposal_id))
        store.event(db, task_id, "proposal_decided", record)
    return {"status": status, "proposal_id": proposal_id, "idempotent": False,
            "auto_apply": False, "configuration_changed": False}


def propose_memory(store, source, quote):
    if not quote.strip() or len(quote.encode("utf-8")) > 4000:
        raise Rejected("Quote must be nonempty and bounded")
    snapshot = store.project.snapshot(source)
    if quote not in snapshot["text"]:
        raise Rejected("Exact quote is not present in source")
    store.allowed_release("builtin-v1")
    memory_id = str(uuid.uuid4())
    with store.tx() as db:
        db.execute("INSERT INTO memories(id,status,quote,source,source_hash,release_id,created) VALUES(?,?,?,?,?,?,?)",
                   (memory_id, "candidate", quote, source, snapshot["sha256"], "builtin-v1", now()))
        store.event(db, None, "memory_proposed", {"id": memory_id, "source": source, "sha256": snapshot["sha256"]})
    return {"id": memory_id, "status": "candidate", "kind": "source_quote_not_generalized_fact"}


def commit_memory(store, memory_id):
    row = store.connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if not row or row["status"] not in ("candidate", "prepared", "verified_quote"):
        raise Rejected("Memory candidate not found")
    store.allowed_release(row["release_id"])
    current = store.project.snapshot(row["source"])
    if current["sha256"] != row["source_hash"] or row["quote"] not in current["text"]:
        raise Rejected("Source changed; memory cannot be committed")
    record = {"id": memory_id, "kind": "source_quote", "project_id": store.project.config["project_id"],
              "source": row["source"], "source_sha256": row["source_hash"], "quote": row["quote"],
              "release_id": row["release_id"], "created": row["created"], "authority": "data_only"}
    content = ("# Verified source quote\n\n```json\n" + json.dumps(record, ensure_ascii=False, indent=2) + "\n```\n").encode("utf-8")
    path = plain_path(store.project.home / "knowledge" / (memory_id + ".md"))
    if row["status"] == "verified_quote" and path.exists() and digest(path.read_bytes()) == row["record_hash"]:
        return {"id": memory_id, "status": "verified_quote", "path": str(path), "idempotent": True}
    with store.tx() as db:
        db.execute("UPDATE memories SET status='prepared' WHERE id=? AND status='candidate'", (memory_id,))
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        if digest(path.read_bytes()) != digest(content):
            raise Rejected("Memory file conflicts with prepared content; do not replay")
    else:
        with path.open("xb") as stream:
            stream.write(content)
    with store.tx() as db:
        store.allowed_release(row["release_id"])
        db.execute("UPDATE memories SET status='verified_quote',record_hash=? WHERE id=?", (digest(content), memory_id))
        store.event(db, None, "memory_committed", {"id": memory_id, "sha256": digest(content), "verification": "exact_source_quote_only"})
    return {"id": memory_id, "status": "verified_quote", "path": str(path), "semantic_truth": "not_inferred"}


def recall(store, query):
    rows = store.connection.execute("SELECT * FROM memories WHERE status='verified_quote' ORDER BY created DESC").fetchall()
    hits, skipped = [], 0
    for row in rows:
        if query and query.casefold() not in row["quote"].casefold():
            continue
        try:
            store.allowed_release(row["release_id"])
            current = store.project.snapshot(row["source"])
            path = plain_path(store.project.home / "knowledge" / (row["id"] + ".md"))
            if current["sha256"] != row["source_hash"] or digest(path.read_bytes()) != row["record_hash"]:
                raise Rejected("Stale source/record")
        except (OSError, ValueError):
            skipped += 1
            continue
        hits.append({"id": row["id"], "quote": row["quote"], "source": row["source"],
                     "source_sha256": row["source_hash"], "kind": "source_quote", "authority": "data_only"})
        if len(hits) == 5:
            break
    return {"hits": hits, "stale_or_revoked_skipped": skipped, "scope": store.project.config["project_id"]}
