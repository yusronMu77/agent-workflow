"""Dispatch a task to another agent CLI (claude/codex/acpx) through a qualified profile (roadmap M2).

Unqualified profiles, or calls without --execute, return a manual handoff: the full-policy context
pack plus the suggested command. Manual handoff stays the fallback in every case.
"""
import json

from .project import Rejected, plain_path
from .records import checked_name, emit
from . import jobs

PROMPT = ("Read {context_file} completely. It is an agent-workflow task pack with every required instruction. "
          "Execute that task in this project, then report the output path and the checks you ran.")
TEMPLATES = {
    "claude": ["claude", "-p", PROMPT],
    "codex": ["codex", "exec", PROMPT],
    "acpx": ["acpx", "codex", PROMPT],
}


def path(engine):
    return plain_path(engine.project.home / "agents.json")


def profiles(engine):
    stored = json.loads(path(engine).read_text(encoding="utf-8")) if path(engine).is_file() else {}
    merged = {name: {"argv": argv, "qualified": False, "evidence": None, "source": "template"}
              for name, argv in TEMPLATES.items()}
    merged.update(stored.get("profiles", {}))
    return merged


def qualify(engine, name, evidence, argv=None):
    """Mark a profile qualified after a real native trial; the evidence file hash is kept."""
    checked_name(name, "profile")
    current = profiles(engine).get(name)
    if current is None and not argv:
        raise Rejected("Unknown profile; give its -- argv containing {context_file}")
    argv = list(argv) if argv else current["argv"]
    if not any("{context_file}" in part for part in argv):
        raise Rejected("Profile argv must reference {context_file}")
    proof = engine.evidence_snapshot(None, evidence)
    stored = json.loads(path(engine).read_text(encoding="utf-8")) if path(engine).is_file() else {"profiles": {}}
    stored["profiles"][name] = {"argv": argv, "qualified": True, "evidence": proof, "source": "qualified"}
    engine.write_owned(path(engine), (json.dumps(stored, indent=2) + "\n").encode("utf-8"))
    emit(engine.store, None, "profile_qualified", {"profile": name, "evidence": proof})
    return {"status": "qualified", "profile": name, "evidence": proof}


def dispatch(engine, task_id, profile, execute=False):
    chosen = profiles(engine).get(profile)
    if chosen is None:
        raise Rejected("Unknown dispatch profile")
    pack = engine.pack(task_id, host="none")  # A fresh agent gets every policy inline.
    argv = [part.replace("{context_file}", pack["context_path"]) for part in chosen["argv"]]
    if not execute or not chosen["qualified"]:
        reason = "not requested (--execute)" if not execute else "profile not qualified"
        return {"status": "manual_handoff", "reason": reason, "context_path": pack["context_path"],
                "suggested_argv": argv, "profile": profile, "native_authorization": "not_granted_by_task_record"}
    job = jobs.start(engine, task_id, argv, label="dispatch:" + profile)
    emit(engine.store, task_id, "task_dispatched", {"profile": profile, "job": job["id"],
                                                    "profile_evidence": chosen["evidence"]})
    return {"status": "dispatched", "profile": profile, "job": job["id"], "log": job["log"],
            "ownership": "dispatched agent executes; parent verifies with finish/verify after the job ends"}
