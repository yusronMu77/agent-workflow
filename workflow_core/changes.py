"""Staged changes to prompts/rules/MCP/hooks (B2) with promotion: shadow evaluation, review, activation, rollback (M5).

Nothing is written to a target until `activate`, which requires a passing shadow evaluation of the
exact staged bytes, a passed review with evidence, an accepted linked proposal (when given) and an
unchanged target. A failing post-activation check rolls back automatically.
"""
import ast
import json
import os

from .project import Rejected, digest, plain_path
from .records import checked_name, emit, fold, new_id
from . import runner

KINDS = ("prompt", "rules", "mcp", "hook", "config", "memory")
EVENTS = ("change_proposed", "change_evaluated", "change_reviewed", "change_activated", "change_rolled_back")


def folder(engine, change_id):
    checked_name(change_id, "change id")
    return plain_path(engine.project.home / "changes" / change_id)


def current_hash(path):
    return digest(path.read_bytes()) if path.is_file() else None


def state(engine, change_id):
    record = fold(engine.store, EVENTS).get(change_id)
    if record is None:
        raise Rejected("Change not found")
    return record


def propose(engine, kind, target, content_ref, note, proposal_id=None):
    if kind not in KINDS or not note.strip():
        raise Rejected("Choose a change kind (" + ", ".join(KINDS) + ") and give a note")
    path = engine.project.resolve(target)
    if not path.parent.is_dir():
        raise Rejected("Target folder must exist")
    if proposal_id and not engine.store.connection.execute("SELECT 1 FROM proposals WHERE id=?", (proposal_id,)).fetchone():
        raise Rejected("Linked feedback proposal not found")
    after = engine.project.snapshot(content_ref)["text"].encode("utf-8")
    before = path.read_bytes() if path.is_file() else None
    change_id = new_id("change")
    staged = folder(engine, change_id)
    staged.mkdir(parents=True)
    engine.write_owned(staged / "after.bin", after)
    if before is not None:
        engine.write_owned(staged / "before.bin", before)
    payload = {"id": change_id, "kind": kind, "target": target, "before_sha256": current_hash(path),
               "after_sha256": digest(after), "proposal": proposal_id, "note": note, "state": "proposed"}
    emit(engine.store, None, "change_proposed", payload)
    return {"status": "proposed", **payload, "target_written": False}


def structural(name, data):
    text = data.decode("utf-8")
    if name.endswith(".json"):
        json.loads(text)
    elif name.endswith(".py"):
        ast.parse(text)
    if not text.strip():
        raise ValueError("empty content")


def evaluate(engine, change_id, argv=None, timeout=600):
    record = state(engine, change_id)
    if record["state"] not in ("proposed", "evaluated", "reviewed"):
        raise Rejected("Only a staged change can be evaluated")
    staged = folder(engine, change_id)
    after = (staged / "after.bin").read_bytes()
    if digest(after) != record["after_sha256"]:
        raise Rejected("Staged content changed; propose again")
    shadow = staged / "shadow" / engine.project.resolve(record["target"]).name
    engine.write_owned(shadow, after)
    checks = {}
    try:
        structural(shadow.name, after)
        checks["structural"] = "passed"
    except (ValueError, SyntaxError, UnicodeDecodeError):
        checks["structural"] = "failed"
    exit_code = None
    if argv:
        env = dict(os.environ, AGENT_WORKFLOW_SHADOW=str(shadow),
                   AGENT_WORKFLOW_TARGET=str(engine.project.resolve(record["target"])))
        exit_code = runner.run(argv, cwd=engine.project.root, timeout=timeout, env=env)["exit_code"]
        checks["fresh_acceptance"] = "passed" if exit_code == 0 else "failed"
    passed = all(v == "passed" for v in checks.values())
    emit(engine.store, None, "change_evaluated", {"id": change_id, "evaluated_sha256": record["after_sha256"],
                                                  "checks": checks, "exit_code": exit_code, "passed": passed,
                                                  "state": "evaluated"})
    return {"status": "evaluated", "id": change_id, "passed": passed, "checks": checks, "shadow": str(shadow)}


def review(engine, change_id, result, evidence, note):
    record = state(engine, change_id)
    if result not in ("passed", "failed") or not note.strip() or record["state"] not in ("evaluated", "reviewed"):
        raise Rejected("Review an evaluated change with passed/failed and a note")
    if evidence.startswith(("task:", "output:")):
        raise Rejected("Change review evidence must be a registered-alias:path file")
    proof = engine.evidence_snapshot(None, evidence)
    emit(engine.store, None, "change_reviewed", {"id": change_id, "review": result, "review_evidence": proof,
                                                 "review_note": note, "reviewed_sha256": record["after_sha256"],
                                                 "state": "reviewed"})
    return {"status": "reviewed", "id": change_id, "review": result}


def activate(engine, change_id, verify_argv=None, timeout=600):
    record = state(engine, change_id)
    path = engine.project.resolve(record["target"])
    if record["state"] != "reviewed" or record.get("review") != "passed" or not record.get("passed"):
        raise Rejected("Activation needs a passing shadow evaluation and a passed review")
    if record.get("evaluated_sha256") != record["after_sha256"] or record.get("reviewed_sha256") != record["after_sha256"]:
        raise Rejected("Evaluation/review are not bound to the staged content")
    if record.get("proposal"):
        row = engine.store.connection.execute("SELECT status FROM proposals WHERE id=?", (record["proposal"],)).fetchone()
        if not row or row["status"] != "accepted":
            raise Rejected("Linked feedback proposal must be accepted first")
    if current_hash(path) != record["before_sha256"]:
        raise Rejected("Target changed since the change was staged; propose again")
    after = (folder(engine, change_id) / "after.bin").read_bytes()
    engine.write_owned(path, after)
    emit(engine.store, None, "change_activated", {"id": change_id, "state": "activated", "active_sha256": digest(after)})
    result = {"status": "activated", "id": change_id, "target": record["target"]}
    if verify_argv:
        run = runner.run(verify_argv, cwd=engine.project.root, timeout=timeout)
        result["post_check_exit_code"] = run["exit_code"]
        if run["exit_code"] != 0:
            rollback(engine, change_id, "automatic rollback: post-activation check failed", automatic=True)
            result["status"] = "rolled_back"
    return result


def rollback(engine, change_id, note, automatic=False):
    record = state(engine, change_id)
    path = engine.project.resolve(record["target"])
    if record["state"] != "activated" or not note.strip():
        raise Rejected("Only an activated change can be rolled back, with a note")
    if current_hash(path) != record["after_sha256"]:
        raise Rejected("Target was edited after activation; reconcile manually instead of overwriting")
    before = folder(engine, change_id) / "before.bin"
    if before.is_file():
        engine.write_owned(path, before.read_bytes())
    else:
        path.unlink()
    emit(engine.store, None, "change_rolled_back", {"id": change_id, "state": "rolled_back", "rollback_note": note,
                                                    "automatic": automatic})
    return {"status": "rolled_back", "id": change_id, "automatic": automatic}


def listing(engine):
    return {"changes": [{k: v.get(k) for k in ("id", "kind", "target", "state", "proposal", "passed", "review")}
                        for v in fold(engine.store, EVENTS).values()][-50:]}
