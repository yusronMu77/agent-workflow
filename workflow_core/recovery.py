"""Verified database restore from backups, manual or automatic newest-valid (roadmap M4)."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .project import Project, Rejected, digest, plain_path
from .state import SCHEMA_VERSION


def inspect(project, candidate):
    """Integrity, identity and schema of a backup file, read-only."""
    candidate = Path(candidate).resolve()
    if not candidate.is_file():
        return {"path": str(candidate), "valid": False, "reason": "missing"}
    try:
        with closing(sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)) as db:
            check = db.execute("PRAGMA quick_check").fetchone()[0]
            meta = dict(db.execute("SELECT key,value FROM meta").fetchall())
    except sqlite3.Error as error:
        return {"path": str(candidate), "valid": False, "reason": "unreadable: " + type(error).__name__}
    reason = None
    if check != "ok":
        reason = "integrity_check_failed"
    elif meta.get("project_id") != project.config["project_id"]:
        reason = "different_project"
    elif not str(meta.get("schema", "")).isdecimal() or not 1 <= int(meta["schema"]) <= SCHEMA_VERSION:
        reason = "unsupported_schema"
    return {"path": str(candidate), "valid": reason is None, "reason": reason,
            "sha256": digest(candidate.read_bytes()), "modified": candidate.stat().st_mtime}


def live_health(project):
    try:
        with closing(sqlite3.connect(project.db, timeout=10)) as db:
            return db.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.Error as error:
        return "unreadable: " + type(error).__name__


def restore(root, source=None, backups=None, auto=False):
    """Replace live state with a verified backup; the current database is preserved first.

    auto=True restores only when the live database fails quick_check, picking the newest valid backup."""
    project = Project(root)
    health = live_health(project)
    if auto and health == "ok":
        return {"status": "healthy_no_restore", "database_check": health}
    if source:
        chosen = inspect(project, source)
    else:
        if not backups:
            raise Rejected("Give --from FILE or --backups DIR")
        found = sorted((inspect(project, p) for p in Path(backups).glob("*.sqlite")), key=lambda r: -r.get("modified", 0))
        chosen = next((r for r in found if r["valid"]), {"valid": False, "reason": "no valid backup in folder"})
    if not chosen["valid"]:
        raise Rejected("Backup rejected: " + chosen["reason"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    preserved = plain_path(project.home / f"state.before-restore-{stamp}.sqlite")
    if project.db.is_file():
        with closing(sqlite3.connect(project.db, timeout=10)) as live, closing(sqlite3.connect(preserved)) as copy:
            try:
                live.backup(copy)
            except sqlite3.Error:
                preserved.write_bytes(project.db.read_bytes())
    with closing(sqlite3.connect(chosen["path"])) as backup, closing(sqlite3.connect(project.db, timeout=10)) as live:
        backup.backup(live)
    return {"status": "restored", "from": chosen["path"], "sha256": chosen["sha256"], "previous_check": health,
            "preserved_previous_state": str(preserved),
            "next": "resume each active task; newer events after the backup are lost unless reconciled from the preserved copy"}
