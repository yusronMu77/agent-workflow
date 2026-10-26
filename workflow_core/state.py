from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from .project import Rejected


SCHEMA_VERSION = 1
# Keys are the source version; each callable upgrades exactly one version using
# db.execute(), never executescript() (which would commit the transaction).
# Keep schema 1 until a real product change needs a migration.
MIGRATIONS = {}


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
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self._open()
        except BaseException:
            self.connection.close()  # A refused project/schema must not keep the database file locked.
            raise

    def _open(self):
        with self.tx() as db:
            project = self.project
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if not tables:
                # executescript() implicitly commits; execute DDL separately so
                # initialization, migrations and identity share the same lock.
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT INTO meta VALUES('project_id',?)", (project.config["project_id"],))
                db.execute("INSERT INTO meta VALUES('schema','1')")
            elif "meta" not in tables:
                raise Rejected("Unversioned database; preserve it and migrate explicitly")
            row = db.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()
            if not row or row[0] != project.config["project_id"]:
                raise Rejected("Database project identity mismatch")
            row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if not row or not isinstance(row[0], str) or not row[0].isascii() or not row[0].isdecimal() or int(row[0]) < 1:
                raise Rejected("Invalid database schema metadata; preserve state for inspection")
            found = int(row[0])
            if found > SCHEMA_VERSION:
                raise Rejected("Database schema is newer than this kit; upgrade the kit, do not downgrade state")
            for version in range(found, SCHEMA_VERSION):
                if not callable(MIGRATIONS.get(version)):
                    raise Rejected(f"Missing database migration {version} -> {version + 1}; upgrade the kit")
            while found < SCHEMA_VERSION:
                MIGRATIONS[found](db)
                self.event(db, None, "schema_migrated", {"from": found, "to": found + 1})
                found += 1
                db.execute("UPDATE meta SET value=? WHERE key='schema'", (str(found),))
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

    def transition(self, task_id, status, kind, payload, expected_revision=None, clear_reviews=False, **values):
        allowed = {"summary", "next_action", "result", "repairs", "last_hash", "spec"}
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
            if clear_reviews:
                db.execute("DELETE FROM reviews WHERE task_id=?", (task_id,))
            if status in ("completed", "partial", "cancelled"):
                db.execute("DELETE FROM reservations WHERE task_id=?", (task_id,))
        return self.task(task_id)
