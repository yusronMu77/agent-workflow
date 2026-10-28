"""Routing provenance: which tool/MCP/skill the chat chose, why, and later corrections."""
import json
import re

from .project import Rejected, plain_path
from .records import checked_name, emit, events
from . import runner

WORD = re.compile(r"[a-z0-9]+")


def record(store, task_id, chosen, candidates=(), reason="", provenance="native_chat"):
    """Producer of route_selected: one immutable record of a routing decision."""
    checked_name(chosen, "route")
    candidates = [checked_name(c, "candidate route") for c in dict.fromkeys(candidates)]
    if task_id is not None:
        store.task(task_id)
    if not reason.strip() or len(reason.encode("utf-8")) > 2000:
        raise Rejected("Routing reason must be nonempty and at most 2000 bytes")
    payload = {"chosen": chosen, "candidates": candidates, "reason": reason, "provenance": provenance}
    return {"status": "route_recorded", "event_id": emit(store, task_id, "route_selected", payload), **payload}


def correct(store, route_event_id, corrected, reason):
    """Producer of route_corrected: the collector turns it into a feedback proposal."""
    checked_name(corrected, "route")
    original = next((e for e in events(store, ("route_selected",)) if e["event_id"] == route_event_id), None)
    if original is None:
        raise Rejected("Route selection not found")
    if corrected == original["chosen"] or not reason.strip():
        raise Rejected("A correction needs a different route and a reason")
    payload = {"route_event_id": route_event_id, "from": original["chosen"], "to": corrected,
               "reason": reason, "original_provenance": original["provenance"]}
    return {"status": "route_corrected", "event_id": emit(store, original["task_id"], "route_corrected", payload), **payload}


def history(store, task_id=None):
    rows = events(store, ("route_selected", "route_corrected"), task_id)
    return {"routes": rows[-50:], "selected": sum(r["event_kind"] == "route_selected" for r in rows),
            "corrected": sum(r["event_kind"] == "route_corrected" for r in rows)}


def baseline(request, routes):
    """Deterministic keyword overlap; the fallback when no external selector is configured or it fails."""
    words = set(WORD.findall(request.casefold()))
    scored = []
    for name, description in routes.items():
        terms = set(WORD.findall((name + " " + description).casefold()))
        scored.append((len(words & terms) / (len(terms) or 1), name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    best, name = scored[0]
    return {"route": name, "confidence": round(best, 3), "selector": "keyword_baseline"}


def select(engine, request, routes, task_id=None):
    """Pick a route with the optional external selector (e.g. a small local Qwen), record provenance."""
    if not request.strip() or not isinstance(routes, dict) or not routes:
        raise Rejected("A request and a nonempty {route: description} map are required")
    config_file = plain_path(engine.project.home / "selector.json")
    choice, note = None, "no external selector configured"
    if config_file.is_file():
        config = json.loads(config_file.read_text(encoding="utf-8"))
        result = runner.run(config["argv"], cwd=engine.project.root, timeout=config.get("timeout", 30),
                            stdin=json.dumps({"request": request, "routes": routes}).encode("utf-8"))
        try:
            answer = json.loads(result["output"].decode("utf-8").strip().splitlines()[-1])
            if result["exit_code"] == 0 and answer.get("route") in routes:
                choice = {"route": answer["route"], "confidence": answer.get("confidence"),
                          "selector": "external:" + config.get("name", config["argv"][0])}
            else:
                note = "external selector returned no known route; baseline used"
        except (ValueError, IndexError, AttributeError):
            note = "external selector output was not JSON; baseline used"
    choice = choice or dict(baseline(request, routes), note=note)
    recorded = record(engine.store, task_id, choice["route"], list(routes), f"{choice['selector']} for: {request[:300]}",
                      provenance=choice["selector"])
    return {**choice, "event_id": recorded["event_id"], "authority": "suggestion_only_chat_decides"}
