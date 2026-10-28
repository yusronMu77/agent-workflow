"""Candidate trial in an isolated copy, install into a registered root, automatic rollback (B4).

Isolation is a throwaway copy plus redirected HOME/USERPROFILE/APPDATA/TEMP; it is not an OS
sandbox, so run only candidates the native owner has approved for execution.
"""
from pathlib import Path
import shutil
import tempfile

from .project import Rejected, digest
from .records import emit, fold, new_id
from . import runner

EVENTS = ("candidate_trialed", "candidate_installed", "candidate_rolled_back")


def tree_digest(folder):
    folder = Path(folder)
    lines = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and ".git" not in p.relative_to(folder).parts):
        lines.append(path.relative_to(folder).as_posix() + " " + digest(path.read_bytes()))
    if not lines:
        raise Rejected("Candidate folder has no files")
    return digest("\n".join(lines).encode("utf-8"))


def fill(argv, candidate):
    return [part.replace("{candidate}", str(candidate)) for part in argv]


def trial(engine, source, install_argv=None, trial_argv=None, timeout=600):
    source = Path(source).resolve()
    if not source.is_dir() or source.is_relative_to(engine.project.home):
        raise Rejected("Candidate source must be an existing folder outside workflow state")
    if not trial_argv:
        raise Rejected("A trial command is required; use {candidate} for the isolated copy path")
    tree = tree_digest(source)
    steps = {}
    with tempfile.TemporaryDirectory(prefix="workflow-candidate-") as temporary:
        root = Path(temporary)
        candidate, home = root / "candidate", root / "home"
        home.mkdir()
        shutil.copytree(source, candidate, ignore=shutil.ignore_patterns(".git"))
        env = runner.sandbox_env(home)
        for name, argv in (("install", install_argv), ("trial", trial_argv)):
            if not argv:
                continue
            run = runner.run(fill(argv, candidate), cwd=candidate, timeout=timeout, env=env)
            steps[name] = {"exit_code": run["exit_code"], "timed_out": run["timed_out"],
                           "tail": run["output"].decode("utf-8")[-800:]}
            if run["exit_code"] != 0:
                break
    passed = bool(steps) and all(s["exit_code"] == 0 for s in steps.values()) and "trial" in steps
    trial_id = new_id("candidate")
    emit(engine.store, None, "candidate_trialed", {"id": trial_id, "source": str(source), "tree_sha256": tree,
                                                   "steps": steps, "passed": passed, "state": "trialed",
                                                   "isolation": "temporary_copy_and_redirected_profile_not_os_sandbox"})
    return {"status": "passed" if passed else "failed", "id": trial_id, "steps": steps, "isolated_copy": "removed"}


def install(engine, trial_id, target, verify_argv=None, timeout=600):
    record = fold(engine.store, EVENTS).get(trial_id)
    if not record or record["state"] != "trialed" or not record["passed"]:
        raise Rejected("Install only a candidate whose isolated trial passed")
    source = Path(record["source"])
    if tree_digest(source) != record["tree_sha256"]:
        raise Rejected("Candidate source changed after its trial; trial it again")
    path = engine.project.resolve(target)
    if path.exists():
        raise Rejected("Install target already exists; choose a new folder")
    shutil.copytree(source, path, ignore=shutil.ignore_patterns(".git"))
    installed = tree_digest(path)
    emit(engine.store, None, "candidate_installed", {"id": trial_id, "target": target, "installed_sha256": installed,
                                                     "state": "installed"})
    result = {"status": "installed", "id": trial_id, "target": target}
    if verify_argv:
        run = runner.run(fill(verify_argv, path), cwd=path, timeout=timeout)
        result["verify_exit_code"] = run["exit_code"]
        if run["exit_code"] != 0:
            rollback(engine, trial_id, "automatic rollback: post-install check failed", automatic=True)
            result["status"] = "rolled_back"
    return result


def rollback(engine, trial_id, note, automatic=False):
    record = fold(engine.store, EVENTS).get(trial_id)
    if not record or record["state"] != "installed" or not note.strip():
        raise Rejected("Only an installed candidate can be rolled back, with a note")
    path = engine.project.resolve(record["target"])
    if path.exists() and tree_digest(path) != record["installed_sha256"]:
        raise Rejected("Installed files changed; reconcile manually instead of deleting edits")
    if path.exists():
        shutil.rmtree(path)
    emit(engine.store, None, "candidate_rolled_back", {"id": trial_id, "state": "rolled_back", "note": note,
                                                       "automatic": automatic})
    return {"status": "rolled_back", "id": trial_id, "automatic": automatic}
