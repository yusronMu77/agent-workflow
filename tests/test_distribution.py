"""Distribution/lifecycle acceptance fixtures; no client inference or credentials."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_release import ROOT, audit, build, product_files
from install import execute, inventory


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workflow-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'project spasi Ω'
        self.project.mkdir()

    def test_clean_reproducible_build_and_version_drift(self):
        source = self.base / 'source'
        source.mkdir()
        for name, body in product_files():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        (source / '.env').write_text('PRIVATE=fixture')
        (source / '.agent-workflow').mkdir()
        (source / '.agent-workflow/state.sqlite').write_bytes(b'private')
        (source / 'docs/private-notes.md').write_text('private note not a product file')
        a = build(self.base / 'a', source)
        b = build(self.base / 'b', source)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        with zipfile.ZipFile(a) as bundle:
            self.assertNotIn('.env', bundle.namelist())
            self.assertNotIn('docs/private-notes.md', bundle.namelist())
            self.assertFalse(any('.agent-workflow/' in n for n in bundle.namelist()))
            self.assertIn('workflow_core/engine.py', bundle.namelist())
        (source / 'plugin.json').write_text('{"version":"99.0.0"}')
        with self.assertRaises(ValueError):
            build(self.base / 'bad', source)

    def test_clients_scopes_dry_run_idempotence(self):
        for scope in ('project', 'user'):
            for client in ('codex', 'claude', 'both'):
                target = self.project / (scope + client)
                target.mkdir()
                args = dict(client=client, scope=scope, project=target, home=target)
                execute('install', dry_run=True, **args)
                self.assertEqual(list(target.iterdir()), [])
                execute('install', **args)
                first = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                execute('install', **args)
                self.assertEqual(first, {str(p.relative_to(target)): p.read_bytes() for p in target.rglob('*') if p.is_file()})
                execute('check', **args)
                execute('uninstall', **args)
                self.assertFalse(any(p.name == 'SKILL.md' for p in target.rglob('*')))

    def test_collision_modified_and_all_client_preflight(self):
        args = dict(client='both', scope='project', project=self.project)
        foreign = self.project / '.claude/skills/agent-workflow'
        foreign.mkdir(parents=True)
        (foreign / 'SKILL.md').write_text('user')
        with self.assertRaises((OSError, ValueError)):
            execute('install', **args)
        self.assertFalse((self.project / '.agents').exists())
        shutil.rmtree(foreign)
        execute('install', **args)
        skill = self.project / '.agents/skills/agent-workflow/SKILL.md'
        skill.write_text('user edit')
        for action in ('install', 'update', 'uninstall', 'check'):
            with self.assertRaises(ValueError):
                execute(action, selected='1.0.0', **args)
        self.assertEqual(skill.read_text(), 'user edit')

    def test_upgrade_rollback_preserves_state_and_rules(self):
        args = dict(client='both', scope='project', project=self.project)
        state = self.project / '.agent-workflow'
        state.mkdir()
        (state / 'state.sqlite').write_bytes(b'unchanged database fixture')
        (state / 'checkpoint.json').write_bytes(b'unchanged checkpoint fixture')
        (self.project / 'AGENTS.md').write_text('user rules')
        before = {p.name: p.read_bytes() for p in state.iterdir()}
        execute('install', **args)
        source = self.base / 'v2'
        source.mkdir()
        for name, body in product_files():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        (source / 'VERSION').write_text('1.1.0')
        execute('update', selected='1.1.0', root=source, **args)
        receipt = self.project / '.agents/skills/agent-workflow/.agent-workflow-receipt.json'
        self.assertEqual(json.loads(receipt.read_text())['version'], '1.1.0')
        execute('update', selected='1.0.0', **args)
        self.assertEqual(json.loads(receipt.read_text())['version'], '1.0.0')
        execute('uninstall', **args)
        self.assertEqual(before, {p.name: p.read_bytes() for p in state.iterdir()})
        self.assertEqual((self.project / 'AGENTS.md').read_text(), 'user rules')

    def test_interruption_blocks_replay_and_rollback(self):
        args = dict(client='codex', scope='project', project=self.project)
        execute('install', **args)
        target = self.project / '.agents/skills/agent-workflow'
        before = inventory(target)
        original = Path.rename
        def fail_candidate(path, destination):
            if path.name == 'candidate':
                raise OSError('simulated interruption')
            return original(path, destination)
        with patch.object(Path, 'rename', fail_candidate):
            with self.assertRaises(OSError):
                execute('install', **args)
        self.assertEqual(before, inventory(target))
        with self.assertRaisesRegex(ValueError, 'Unknown interrupted'):
            execute('install', **args)

    def test_direct_skill_is_independent_and_requires_target(self):
        execute('install', 'codex', 'project', project=self.project)
        launcher = self.project / '.agents/skills/agent-workflow/scripts/wf.py'
        docs = launcher.parents[1] / '_kit'
        self.assertTrue((docs / 'README.md').is_file())
        self.assertTrue((docs / 'docs/PRIVATE-PUBLISH.md').is_file())
        target = self.base / 'other project'
        target.mkdir()
        result = subprocess.run([sys.executable, '-I', str(launcher), '--project', str(target), 'init'],
                                cwd=self.base, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((launcher.parents[1] / '.agent-workflow').exists())
        result = subprocess.run([sys.executable, str(launcher), 'doctor'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_secret_and_private_path_audit(self):
        for name in ('docs/.env', 'scripts/auth.json', 'docs/state.sqlite', 'workflow_core/key.pem'):
            with self.assertRaises(ValueError):
                audit({name: b'private'})
        with self.assertRaises(ValueError):
            audit({'README.md': ('gh' + 'p_' + 'a' * 36).encode()})
        audit({'README.md': b'Use native login; no credentials included'})

    def test_unknown_nested_receipt_is_preserved(self):
        execute('install', 'codex', 'project', project=self.project)
        target = self.project / '.agents/skills/agent-workflow'
        (target / 'scripts/.agent-workflow-receipt.json').write_text('user file')
        with self.assertRaises(ValueError):
            execute('uninstall', 'codex', 'project', project=self.project)


if __name__ == '__main__':
    unittest.main()
