"""Rulesync dry-run on a throwaway copy (roadmap M4): report what generation would change, write nothing."""
from pathlib import Path
import shutil
import tempfile

from .project import Rejected, digest
from . import runner

RULE_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "rulesync.jsonc", ".rulesync", ".claude", ".codex",
              ".cursor", ".github/copilot-instructions.md")


def files(folder):
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): digest(p.read_bytes())
            for p in folder.rglob("*") if p.is_file() and not any(part in (".git", "node_modules") for part in p.relative_to(folder).parts)}


def dry_run(root, argv=None, timeout=300):
    root = Path(root).resolve()
    command = list(argv) if argv else None
    if command is None:
        executable = shutil.which("rulesync") or shutil.which("npx")
        if not executable:
            return {"status": "tool_missing", "install_hint": "npm install -g rulesync (or provide -- argv)",
                    "project_changed": False}
        command = [executable, "generate"] if Path(executable).stem.lower() == "rulesync" else [executable, "--yes", "rulesync", "generate"]
    with tempfile.TemporaryDirectory(prefix="workflow-rulesync-") as temporary:
        copy = Path(temporary) / "copy"
        copy.mkdir()
        for name in RULE_FILES:
            source = root / name
            if source.is_dir():
                shutil.copytree(source, copy / name, ignore=shutil.ignore_patterns("*.sqlite", "settings.local.json"))
            elif source.is_file():
                (copy / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, copy / name)
        if not any(copy.iterdir()):
            raise Rejected("No rule files found to dry-run")
        before = files(copy)
        run = runner.run(command, cwd=copy, timeout=timeout)
        after = files(copy)
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    return {"status": "dry_run_passed" if run["exit_code"] == 0 else "dry_run_failed", "exit_code": run["exit_code"],
            "created": sorted(after.keys() - before.keys()), "changed": changed,
            "removed": sorted(before.keys() - after.keys()), "tail": run["output"].decode("utf-8")[-1500:],
            "project_changed": False}
