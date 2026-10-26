"""K4 trigger: a PostToolUse hook that counts main-chain model requests in the current turn and reminds once when the turn gets long.

It only adds context (data, not permission). It never starts a task, never blocks, and reads each transcript incrementally."""
import argparse
import json
from pathlib import Path
import sys

import jsonl_store as store

DEFAULT_THRESHOLD = 30  # candidate from the K4 amortization estimate, not calibrated


def state_path():
    return store.home() / "lazy-state.json"


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def advance(session, transcript):
    """Read only the bytes appended since the last call; count unique assistant request ids since the last real user message."""
    path = Path(transcript)
    size = path.stat().st_size
    if size < session.get("offset", 0):
        session.update(offset=0, turn=0, ids=[], fired_turn=-1)
    with path.open("rb") as handle:
        handle.seek(session.get("offset", 0))
        chunk = handle.read()
    end = chunk.rfind(b"\n") + 1  # a half-written last line waits for the next call
    ids = set(session.get("ids", []))
    for line in chunk[:end].decode("utf-8", "replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = message.get("content")
        if row.get("type") == "user" and not row.get("isSidechain"):
            blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            if isinstance(content, str) or (blocks and not any(b.get("type") == "tool_result" for b in blocks)):
                session["turn"] = session.get("turn", 0) + 1
                ids = set()
        elif row.get("type") == "assistant" and not row.get("isSidechain") and isinstance(message.get("usage"), dict) \
                and message.get("model") != "<synthetic>":
            ids.add(message.get("id") or row.get("requestId") or row.get("uuid"))
    session["offset"] = session.get("offset", 0) + end
    session["ids"] = sorted(i for i in ids if i)
    return len(session["ids"])


def respond(payload, threshold=DEFAULT_THRESHOLD, path=None):
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "PostToolUse":
        return {}
    transcript, sid = payload.get("transcript_path"), payload.get("session_id")
    if not isinstance(transcript, str) or not isinstance(sid, str) or not Path(transcript).is_file():
        return {}
    path = path or state_path()
    state = load_state(path)
    session = state.setdefault(sid, {"offset": 0, "turn": 0, "ids": [], "fired_turn": -1})
    try:
        count = advance(session, transcript)
    except OSError:
        return {}
    fire = count >= threshold and session["fired_turn"] != session["turn"]
    if fire:
        session["fired_turn"] = session["turn"]
        session["fired_count"] = session.get("fired_count", 0) + 1
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass
    if not fire:
        return {}
    message = ("Agent Workflow lazy-adoption reminder (data, not permission): this turn has made %d model requests. "
              "If substantial work remains and it is multi-file or writes outside the project, consider "
              "`wf.py start` (tier 1) for the rest; otherwise continue natively. Reminded once per turn." % count)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message}}


def settings(threshold):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    command = "& %s %s --threshold %d" % (quote(sys.executable), quote(Path(__file__).resolve()), threshold)
    return {"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "shell": "powershell", "command": command, "timeout": 5}]}]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--print-settings", action="store_true")
    args = parser.parse_args(argv)
    if args.print_settings:
        result = settings(args.threshold)
    else:
        raw = sys.stdin.buffer.read(65537)
        try:
            result = respond(json.loads(raw), args.threshold) if len(raw) <= 65536 else {}
        except (ValueError, TypeError, AttributeError):
            result = {}
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
