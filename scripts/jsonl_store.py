"""Small JSONL helpers shared by the owner-feedback, cost, proposal, assignment and recipe tools: stdlib only, atomic rewrite, append-only logs."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def home():
    return Path(os.environ.get("AGENT_WORKFLOW_HOME") or (Path.home() / ".agent-workflow"))


def digest(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def rows(path):
    path = Path(path)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # an interrupted last line is skipped, never invented
        if isinstance(row, dict):
            out.append(row)
    return out


def save(path, items):
    """Rewrite the whole file atomically; use for mutable stores (feedback, proposals)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def append(path, item):
    """Append one row; use for immutable logs (decisions, batches, replay runs)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def head_path(path):
    path = Path(path)
    return path.with_name(path.name + ".head")


def chained_append(path, item):
    """Append with a hash chain so an edited or removed row is detectable: row.prev is the sha256 of the previous line.
    A head file keeps the hash of the last row, so editing or dropping the final row is detected too.
    Someone who rewrites both files can still forge history; this guards against accidents and unnoticed edits."""
    path = Path(path)
    previous = rows(path)
    prev = digest(canonical(previous[-1])) if previous else "0" * 64
    row = {**item, "prev": prev}
    append(path, row)
    head_path(path).write_text(digest(canonical(row)), encoding="utf-8")
    return row


def chain_ok(path):
    items = rows(path)
    prev = "0" * 64
    for item in items:
        if item.get("prev") != prev:
            return False
        prev = digest(canonical(item))
    if not items:
        return not head_path(path).exists()
    try:
        return head_path(path).read_text(encoding="utf-8").strip() == prev
    except OSError:
        return False
