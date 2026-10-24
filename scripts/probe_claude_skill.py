"""Read-only Claude Code skill/plugin probe; no chat, no model call, no config or auth writes."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

KIT = Path(__file__).resolve().parents[1]


def frontmatter(path):
    text = path.read_text(encoding="utf-8-sig")
    match = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not match:
        raise ValueError("SKILL.md has no frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line.startswith(" "):
            fields[key.strip()] = value.strip()
    return fields


def check(kit=KIT, project=None, run_claude=True):
    """Return a report; `errors` is empty when the Claude-facing packaging is consistent."""
    kit = Path(kit).resolve()
    errors, report = [], {"kit": str(kit)}
    skill = kit / "skills/agent-workflow/SKILL.md"
    fields = frontmatter(skill)
    if fields.get("name") != skill.parent.name or not re.fullmatch(r"[a-z0-9-]{1,64}", fields.get("name", "")):
        errors.append("Skill name must match its directory and use lowercase letters, digits and hyphens")
    description = fields.get("description", "")
    if not 40 <= len(description) <= 1024:
        errors.append("Skill description must be 40-1024 characters")
    if not re.search(r"\bnot for\b|\bdo not use\b|\bdon't use\b", description.lower()):
        errors.append("Skill description must say when NOT to trigger, so Claude does not open a task for simple requests")
    report["description_chars"] = len(description)
    version = (kit / "VERSION").read_text(encoding="utf-8").strip()
    for manifest in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        value = json.loads((kit / manifest).read_text(encoding="utf-8")).get("version")
        if value != version:
            errors.append(f"{manifest} version {value} differs from VERSION {version}")
    json.loads((kit / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    launcher = subprocess.run([sys.executable, "-I", str(kit / "skills/agent-workflow/scripts/wf.py"), "--version"],
                              cwd=kit.parent, capture_output=True, text=True)
    if launcher.returncode != 0 or launcher.stdout.strip() != version:
        errors.append("Skill launcher --version failed from a foreign working directory")
    if project:
        link = Path(project).resolve() / ".claude/skills/agent-workflow/SKILL.md"
        report["project_skill"] = str(link)
        if not link.is_file() or link.resolve() != skill.resolve():
            errors.append("Project .claude/skills/agent-workflow does not resolve to this kit's skill")
    executable = shutil.which("claude") if run_claude else None
    if executable:
        validation = subprocess.run([executable, "plugin", "validate", str(kit)], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
        report["claude_plugin_validate"] = {"exit_code": validation.returncode}
        if validation.returncode != 0:
            errors.append("claude plugin validate failed: " + (validation.stdout + validation.stderr).strip()[:400])
    else:
        report["claude_plugin_validate"] = "skipped_claude_cli_not_found" if run_claude else "skipped_by_flag"
    report["errors"] = errors
    report["status"] = "failed" if errors else "passed"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Also require this project's .claude/skills link to resolve to the kit")
    parser.add_argument("--no-claude", action="store_true", help="Skip `claude plugin validate` even if the CLI exists")
    arguments = parser.parse_args()
    result = check(KIT, arguments.project, not arguments.no_claude)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "passed" else 2)
