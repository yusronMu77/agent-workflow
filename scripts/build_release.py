"""Build a clean, deterministic product ZIP using explicit product paths only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = ('VERSION', 'LICENSE', 'wf.py', 'README.md', 'AGENTS.md', 'CLAUDE.md',
       'plugin.json', '.claude-plugin/plugin.json', '.claude-plugin/marketplace.json',
       '.codex-plugin/plugin.json', '.agents/plugins/marketplace.json',
       'agent-workflow.code-workspace', '.gitignore', '.editorconfig', '.gitattributes')
PRODUCT = TOP + tuple('workflow_core/' + name + '.py' for name in
    ('__init__', 'engine', 'feedback', 'outputs', 'project', 'state', 'runner', 'records', 'harness', 'routing',
     'catalog', 'health', 'benchmarks', 'jobs', 'job_wrapper', 'broker', 'dispatch', 'changes', 'candidates',
     'knowledge', 'recovery', 'rulesync', 'cli_ext')) + (
    'skills/agent-workflow/SKILL.md', 'skills/agent-workflow/references/advanced.md', 'skills/agent-workflow/scripts/wf.py',
    'skills/agent-workflow/agents/openai.yaml', '.github/workflows/ci.yml',
    'scripts/build_release.py', 'scripts/install.py', 'scripts/install_chat.py',
    'scripts/verify_distribution.py', 'scripts/verify_portability.py',
    'scripts/probe_codex_skill.py', 'scripts/probe_claude_skill.py', 'scripts/benchmark_outputs.py', 'scripts/pilot_ab.py', 'scripts/pilot_heavy.py', 'scripts/pilot_native.py', 'scripts/pilot_grade.py', 'scripts/png_probe.py',
    'scripts/qualify_io.py', 'scripts/qualify_native.py', 'scripts/grade_holdout.py', 'scripts/session_reminder.py',
    'tests/test_distribution.py', 'tests/test_workflow.py', 'tests/test_outputs.py', 'tests/test_v11.py',
    'tests/test_migrations.py', 'tests/test_pilot.py', 'tests/test_heavy_pilot.py', 'tests/test_feedback_decisions.py', 'tests/test_review_pack.py', 'tests/test_qualification.py', 'tests/test_efficient_flow.py', 'tests/test_roadmap.py',
    'scripts/worker.py', 'scripts/status_ui.py',
) + tuple('docs/' + name + '.md' for name in (
    'ARCHITECTURE', 'BENCHMARK-GUIDE', 'CHAT-SMOKE', 'CLAUDE-CODE', 'IMPLEMENTATION-PORTABLE-PLUGIN',
    'PRD-PORTABLE-PLUGIN', 'PRD-V1-1', 'PRIVATE-PUBLISH', 'RESEARCH-INTEGRATION', 'RUN-PORTABLE-PLUGIN',
    'TOKEN-OPTIMIZATION', 'ROADMAP-IMPLEMENTATION', 'V1-2-STATUS', 'V1-2-HEAVY-PILOT', 'V1-2-QUALIFICATION', 'WORKFLOW-EFFICIENCY'
)) + tuple(
    f'docs/{folder}/{name}.{extension}' for name in ('native-chat', 'portable-plugin')
    for folder, extension in (('src/diagrams', 'mmd'), ('img/diagrams', 'png'), ('img/diagrams', 'pdf')))


def audit(files):
    """Reject credential/state paths and high-confidence credential literals, without printing values."""
    patterns = (rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{40,}',
                rb'sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}', rb'AKIA[A-Z0-9]{16}')
    for name, body in files.items():
        parts = Path(name).parts
        if any(p in ('.agent-workflow', '.env', '.mcp.json', '.git', 'auth.json', 'credentials.json')
               or p.startswith('.env.') for p in parts) or Path(name).suffix in ('.sqlite', '.db', '.pem', '.key'):
            raise ValueError(f'Private file rejected: {name}')
        if any(re.search(pattern, body) for pattern in patterns):
            raise ValueError(f'Potential credential rejected in {name}; inspect locally')


def product_files(root=ROOT):
    for name in sorted(set(PRODUCT)):
        path = root / name
        ancestors = [path, *(root / p for p in path.relative_to(root).parents)]
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
               for p in ancestors) or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Non-contained product path: {path}')
        yield path.relative_to(root).as_posix(), path.read_bytes()


def version(root=ROOT):
    value = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?', value):
        raise ValueError('Invalid VERSION')
    return value


def build(output, root=ROOT):
    value = version(root)
    files = dict(product_files(root))
    audit(files)
    for name in ('plugin.json', '.claude-plugin/plugin.json', '.codex-plugin/plugin.json'):
        if json.loads(files[name])['version'] != value or json.loads(files[name])['name'] != 'agent-workflow':
            raise ValueError(f'Version drift: {name}')
    claude = json.loads(files['.claude-plugin/marketplace.json'])
    codex = json.loads(files['.agents/plugins/marketplace.json'])
    if claude['plugins'][0]['source'] != './' or codex['plugins'][0]['source'] != {'source': 'local', 'path': './'}:
        raise ValueError('Catalog must reference the tracked self-contained package root')
    manifest = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'agent-workflow-{value}.zip'
    files['MANIFEST.sha256.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, body)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    try:
        print(build(args.output))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
