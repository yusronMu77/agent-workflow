from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from .project import Rejected


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,revoked INTEGER NOT NULL DEFAULT 0,reason TEXT);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,status TEXT NOT NULL,revision INTEGER NOT NULL,
 spec TEXT NOT NULL,repairs INTEGER NOT NULL DEFAULT 0,last_hash TEXT,summary TEXT,next_action TEXT,
 result TEXT,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reservations(path TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS reviews(task_id TEXT NOT NULL,criterion TEXT NOT NULL,result TEXT NOT NULL,
 evidence TEXT NOT NULL,note TEXT NOT NULL,PRIMARY KEY(task_id,criterion));
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,task_id TEXT,kind TEXT NOT NULL,payload TEXT NOT NULL,
 created TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,
 kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'proposed');
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,status TEXT NOT NULL,quote TEXT NOT NULL,
 source TEXT NOT NULL,source_hash TEXT NOT NULL,release_id TEXT NOT NULL,
 created TEXT NOT NULL,record_hash TEXT);
"""


class Store:
    def __init__(self, project):
        self.project = project
        self.connection = sqlite3.connect(project.db, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(SCHEMA)
        with self.tx() as db:
            row = db.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()
            if row and row[0] != project.config["project_id"]:
                raise Rejected("Database project identity mismatch")
            db.execute("INSERT OR IGNORE INTO meta VALUES('project_id',?)", (project.config["project_id"],))
            db.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")
            db.execute("INSERT OR IGNORE INTO releases(id) VALUES('builtin-v1')")

    def close(self):
        self.connection.close()

    @contextmanager
    def tx(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def task(self, task_id):
        row = self.connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise Rejected("Task not found")
        result = dict(row)
        result["spec"] = json.loads(result["spec"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def allowed_release(self, release_id):
        row = self.connection.execute("SELECT revoked FROM releases WHERE id=?", (release_id,)).fetchone()
        if not row or row[0]:
            raise Rejected("Release revoked or unavailable; native review required")

    def event(self, db, task_id, kind, payload):
        event_id = str(uuid.uuid4())
        db.execute("INSERT INTO events(id,task_id,kind,payload,created) VALUES(?,?,?,?,?)",
                   (event_id, task_id, kind, json.dumps(payload, ensure_ascii=False), now()))
        return event_id

    def transition(self, task_id, status, kind, payload, expected_revision=None, **values):
        allowed = {"summary", "next_action", "result", "repairs", "last_hash"}
        if not set(values).issubset(allowed):
            raise Rejected("Unsupported transition fields")
        with self.tx() as db:
            task = self.task(task_id)
            if expected_revision is not None and task["revision"] != expected_revision:
                raise Rejected("Stale task revision")
            self.allowed_release(task["spec"]["release_id"])
            updates = {"status": status, "revision": task["revision"] + 1, "updated": now(), **values}
            db.execute("UPDATE tasks SET " + ",".join(key + "=?" for key in updates) + " WHERE id=?",
                       [*updates.values(), task_id])
            self.event(db, task_id, kind, payload)
            if status in ("completed", "partial", "cancelled"):
                db.execute("DELETE FROM reservations WHERE task_id=?", (task_id,))
        return self.task(task_id)
