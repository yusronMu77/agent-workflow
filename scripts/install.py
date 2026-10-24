"""Install independent direct skills without modifying project rules or task state."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from build_release import ROOT, audit, product_files, version

RECEIPT = '.agent-workflow-receipt.json'


def inventory(folder):
    result = {}
    for path in folder.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()) or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Linked installation file preserved')
        if path.is_file() and path != folder / RECEIPT:
            if path.suffix == '.pyc' and path.parent.name == '__pycache__' and (path.parent.parent / (path.name.split('.')[0] + '.py')).is_file():
                continue
            result[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def owned(folder):
    if folder.is_symlink() or folder.resolve() != folder.absolute():
        raise ValueError(f'Existing linked skill preserved: {folder}')
    receipt = json.loads((folder / RECEIPT).read_text(encoding='utf-8'))
    if receipt.get('product') != 'agent-workflow' or receipt.get('files') != inventory(folder):
        raise ValueError(f'Foreign or modified skill preserved: {folder}')
    return receipt


def payload(root):
    result = {}
    for name, body in product_files(root):
        if name.startswith('skills/agent-workflow/'):
            result[name.removeprefix('skills/agent-workflow/')] = body
        elif name in ('wf.py', 'VERSION', 'README.md') or name.startswith(('workflow_core/', 'docs/')):
            result['_kit/' + name] = body
    audit(result)
    return result


def execute(action, client, scope, project=None, home=None, dry_run=False, selected=None, root=ROOT):
    release = version(root)
    if action == 'update' and selected != release:
        raise ValueError('--version must equal the explicitly selected checkout VERSION')
    base = Path(project if scope == 'project' else home or Path.home()).resolve()
    if not base.is_dir():
        raise ValueError('Target directory must already exist')
    clients = ('codex', 'claude') if client == 'both' else (client,)
    targets = [base / ('.agents' if item == 'codex' else '.claude') / 'skills/agent-workflow' for item in clients]
    plans = []
    # Preflight every destination before performing any writes.
    for target in targets:
        if target.parent.resolve() != target.parent.absolute():
            raise ValueError('Linked installation parent preserved')
        journal = target.parent / '.agent-workflow-install-pending'
        if journal.exists():
            raise ValueError(f'Unknown interrupted install: inspect {journal}; reconcile before retry')
        prior = owned(target) if target.exists() or target.is_symlink() else None
        if action == 'check' and not prior:
            raise ValueError(f'Not installed: {target}')
        plans.append({'target': str(target), 'installed_version': prior['version'] if prior else None})
    report = {'action': action, 'client': client, 'scope': scope, 'version': release,
              'dry_run': dry_run, 'targets': plans, 'python': sys.version.split()[0],
              'tools': {name: bool(shutil.which(name)) for name in ('git', 'codex', 'claude', 'rtk')},
              'runtime_proof': 'not_native_smoke'}
    if dry_run or action == 'check':
        return report
    files = payload(root)
    for target in targets:
        if action == 'uninstall' and not target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        journal = target.parent / '.agent-workflow-install-pending'
        # Exclusive journal is also a per-scope writer lock. Survives abrupt termination.
        with journal.open('x', encoding='utf-8') as stream:
            json.dump({'target': str(target), 'action': action, 'version': release}, stream)
        staging = Path(tempfile.mkdtemp(prefix='.agent-workflow-stage-', dir=target.parent))
        backup = staging / 'previous'
        candidate = staging / 'candidate'
        try:
            if action != 'uninstall':
                candidate.mkdir()
                for name, body in files.items():
                    path = candidate / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                (candidate / RECEIPT).write_text(json.dumps({'product': 'agent-workflow', 'version': release,
                    'files': inventory(candidate)}, sort_keys=True), encoding='utf-8')
            if target.exists():
                owned(target)  # Recheck immediately before mutation.
                target.rename(backup)
            if action != 'uninstall':
                candidate.rename(target)
        except BaseException:
            if backup.exists() and not target.exists():
                backup.rename(target)
            raise
        else:
            shutil.rmtree(staging)
            journal.unlink()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'check', 'update', 'uninstall'))
    parser.add_argument('--client', choices=('codex', 'claude', 'both'), required=True)
    parser.add_argument('--scope', choices=('project', 'user'), required=True)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--home', type=Path, help='Explicit user profile root (also useful for isolated fixtures)')
    parser.add_argument('--version')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        if args.scope == 'project' and args.project is None:
            raise ValueError('--project is required for project scope')
        print(json.dumps(execute(args.action, args.client, args.scope, args.project, args.home,
                                 args.dry_run, args.version), indent=2))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'status': 'rejected', 'reason': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
