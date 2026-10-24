"""Exercise copied kits in two independent, multi-folder projects. Fixtures are retained."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

KIT = Path(__file__).resolve().parents[1]


def main():
    fixture = Path(tempfile.mkdtemp(prefix="agent-workflow-portability-"))
    reports = []
    for mode in ("document", "code"):
        case = fixture / mode
        project = case / "project with spaces"
        library = case / "library"
        library.mkdir(parents=True)
        (library / "spec.txt").write_text("The answer is 42.\n", encoding="utf-8")
        copied = project / "tools/agent-workflow"
        shutil.copytree(KIT, copied, ignore=shutil.ignore_patterns(
            ".agent-workflow", ".agents", ".claude", ".git", "dist", "__pycache__", "outputs"))
        (project / "AGENTS.md").write_text("Validate the requested answer before completion.\n", encoding="utf-8")
        workspace = case / "example.code-workspace"
        workspace.write_text('{// portable multi-folder\n"folders":[{"path":"project with spaces"},'
                             '{"name":"library","path":"library"},],}', encoding="utf-8")

        def run(*args, expected=0):
            process = subprocess.run([sys.executable, str(copied / "wf.py"), "--project", str(project), *args],
                                     cwd=case, text=True, capture_output=True, encoding="utf-8")
            if process.returncode != expected:
                raise AssertionError((args, process.returncode, process.stdout, process.stderr))
            return json.loads(process.stderr if expected == 2 else process.stdout)

        init = run("init", "--workspace", str(workspace))
        subprocess.run([sys.executable, str(copied / "scripts/install_chat.py"), "--project", str(project)],
                       check=True, capture_output=True)
        installed = project / ".agents/skills/agent-workflow"
        assert installed.resolve() == (copied / "skills/agent-workflow").resolve()
        suffix = "json" if mode == "code" else "md"
        output = project / f"answer.{suffix}"
        args = ["begin", "portable", "--request", "Report the answer from the source", "--workflow", mode,
                "--output", f"project:answer.{suffix}", "--source", "library:spec.txt:1:1", "--require", "42"]
        if mode == "code":
            args += ["--check", "json"]
        run(*args)
        pack = run("pack", "portable")
        assert "The answer is 42" in Path(pack["context_path"]).read_text(encoding="utf-8")
        # Output archive must travel with copied project state, not depend on kit/source paths.
        (library / 'tool.txt').write_text('passed\n' * 2000 + 'ERROR exact failure line\n', encoding='utf-8')
        archived = run('capture', 'portable', '--input', 'library:tool.txt', '--exit-code', '9', '--max-bytes', '2048')
        assert archived['exit_code'] == 9 and archived['delivered_bytes'] <= 2048
        assert any('exact failure line' in row['text'] for row in archived['lines'])
        expanded = run('read-output', 'portable', archived['key'], '--find', 'exact failure')
        assert expanded['matched_lines'] == 1
        metrics = run('metrics', 'portable')
        assert metrics['captures'] == 1 and metrics['reads'] == 1 and metrics['native_tokens'] is None
        output.write_text('{"answer": 0}\n' if mode == "code" else "Wrong answer\n", encoding="utf-8")
        assert run("verify", "portable", expected=3)["status"] == "repair_needed"
        first = run("feedback", "--collect")
        again = run("feedback", "--collect")
        assert len(first["proposals"]) == len(again["proposals"]) == 1
        output.write_text('{"answer": 42}\n' if mode == "code" else "# Answer\n\nThe answer is 42.\n", encoding="utf-8")
        run("evidence", "portable", "--name", "proof.txt", "--text", "Fixture assertion: source and output both contain answer 42.")
        run("review", "portable", "--result", "passed", "--evidence", "task:proof.txt", "--note", "Synthetic integration assertion; not independent semantic review.")
        assert run("verify", "portable")["status"] == "completed"
        assert run("resume", "portable")["status"] == "completed"
        candidate = run("memory", "propose", "--source", "library:spec.txt", "--quote", "The answer is 42.")
        run("memory", "verify-quote", candidate["id"])
        assert len(run("memory", "search", "--query", "42")["hits"]) == 1
        backup = case / "backup.sqlite"
        run("backup", "--output", str(backup))
        with sqlite3.connect(backup) as database:
            assert database.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert database.execute("SELECT status FROM tasks").fetchone()[0] == "completed"
        run("revoke", "builtin-v1", "--reason", "Fixture revocation")
        assert not run("memory", "search", "--query", "42")["hits"]
        run("resume", "portable", expected=2)
        reports.append({"mode": mode, "project_id": init["project_id"], "status": "passed",
                        "copied_kit": True, "skill_link": True, "multi_folder": True,
                        "output_capture_retrieval_metrics": True,
                        "bounded_repair_feedback_memory_revocation_backup": True})
    assert reports[0]["project_id"] != reports[1]["project_id"]
    print(json.dumps({"status": "passed", "fixtures_retained": str(fixture), "cases": reports}, indent=2))


if __name__ == "__main__":
    main()
