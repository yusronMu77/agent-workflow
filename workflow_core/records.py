"""Event-sourced records for roadmap features: state lives in the existing outbox, schema stays 1."""
import json
import re
import uuid

from .project import Rejected

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}")


def new_id(prefix):
    return prefix + "-" + uuid.uuid4().hex[:12]


def checked_name(value, what="name"):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise Rejected(f"Invalid {what}")
    return value


def events(store, kinds, task_id=None):
    """Payloads of the given event kinds in commit order, each with its event id/kind/created."""
    marks = ",".join("?" for _ in kinds)
    query = f"SELECT id,task_id,kind,payload,created FROM events WHERE kind IN ({marks})"
    values = list(kinds)
    if task_id is not None:
        query += " AND task_id=?"
        values.append(task_id)
    rows = store.connection.execute(query + " ORDER BY created,rowid", values).fetchall()
    return [dict(json.loads(row["payload"]), event_id=row["id"], event_kind=row["kind"],
                 task_id=row["task_id"], created=row["created"]) for row in rows]


def fold(store, kinds, key="id"):
    """Latest state per record id: later events update earlier fields, history is kept."""
    state = {}
    for event in events(store, kinds):
        record = state.setdefault(event[key], {"history": []})
        record.update({k: v for k, v in event.items() if k not in ("event_id", "created")})
        record["history"].append({"kind": event["event_kind"], "created": event["created"], "event_id": event["event_id"]})
    return state


def emit(store, task_id, kind, payload):
    with store.tx() as db:
        event_id = store.event(db, task_id, kind, payload)
    return event_id
