"""Gap research: search installed skill/MCP catalogs for a need, then hand web queries to the chat."""
from pathlib import Path
import re

from .project import Rejected, jsonc
from .records import emit

WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "and", "or", "to", "for", "of", "in", "with", "dan", "untuk", "yang", "di", "ke", "dari"}


def terms(text):
    return {w for w in WORD.findall(text.casefold()) if w not in STOP and len(w) > 1}


def frontmatter(text):
    match = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    fields = {}
    for line in (match.group(1).splitlines() if match else []):
        key, sep, value = line.partition(":")
        if sep and key.strip() in ("name", "description"):
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def entries(skill_dirs=(), mcp_configs=()):
    """Names and descriptions only; MCP env values and headers are never read into results."""
    found = []
    for folder in skill_dirs:
        for path in sorted(Path(folder).glob("*/SKILL.md")):
            meta = frontmatter(path.read_text(encoding="utf-8-sig", errors="replace"))
            found.append({"kind": "skill", "name": meta.get("name", path.parent.name),
                          "description": meta.get("description", "")[:500], "location": str(path.parent)})
    for config in mcp_configs:
        data = jsonc(Path(config))
        servers = data.get("mcpServers") or data.get("servers") or data.get("mcp", {}).get("servers") or {}
        for name, server in servers.items():
            text = " ".join([name, server.get("description", ""), server.get("command", ""),
                             " ".join(str(a) for a in server.get("args", [])), server.get("url", "")])
            found.append({"kind": "mcp", "name": name, "description": text[:500], "location": str(config)})
    return found


def research(store, need, skill_dirs=(), mcp_configs=(), task_id=None):
    wanted = terms(need)
    if not wanted:
        raise Rejected("Describe the missing capability with at least one meaningful word")
    scored = []
    for entry in entries(skill_dirs, mcp_configs):
        overlap = wanted & terms(entry["name"] + " " + entry["description"])
        if overlap:
            scored.append({**entry, "score": round(len(overlap) / len(wanted), 3), "matched": sorted(overlap)})
    scored.sort(key=lambda e: (-e["score"], e["name"]))
    queries = [f"{need} MCP server", f"{need} Claude Code skill", f"{need} github open source CLI"]
    covered = bool(scored) and scored[0]["score"] >= 0.5
    payload = {"need": need, "matches": scored[:10], "covered": covered, "web_queries": queries,
               "web_search": "not_performed_by_controller; run queries with the chat's native tools"}
    kind = "gap_researched" if covered else "capability_gap"
    event_id = emit(store, task_id, kind, payload)
    return {"status": kind, "event_id": event_id, **payload}
