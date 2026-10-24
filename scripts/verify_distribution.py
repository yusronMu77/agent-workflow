"""Verify archive hashes, clean contents, rebuild equality and isolated cache execution."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from build_release import ROOT, build, product_files


def verify(archive):
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert len(names) == len(set(names))
        manifest = json.loads(bundle.read('MANIFEST.sha256.json'))
        assert set(names) == set(manifest) | {'MANIFEST.sha256.json'}
        for name, expected in manifest.items():
            path = Path(name)
            assert not path.is_absolute() and '..' not in path.parts
            assert not any(p in ('.agent-workflow', '__pycache__', '.env', '.mcp.json', '.git') for p in path.parts)
            assert path.suffix not in ('.sqlite', '.pyc', '.pem')
            assert hashlib.sha256(bundle.read(name)).hexdigest() == expected
        assert archive.with_suffix('.zip.sha256').read_text().split()[0] == hashlib.sha256(archive.read_bytes()).hexdigest()
        fixture = Path(tempfile.mkdtemp(prefix='workflow-distribution-'))
        cache = fixture / 'cache spasi Unicode Ω'
        bundle.extractall(cache)
    rebuilt = build(fixture / 'rebuilt', cache)
    with zipfile.ZipFile(rebuilt) as bundle:
        assert json.loads(bundle.read('MANIFEST.sha256.json')) == manifest
    # No imports or checkout references survive: Python isolated mode, cache-only paths, foreign cwd.
    target = fixture / 'target Ω'
    target.mkdir()
    launcher = cache / 'skills/agent-workflow/scripts/wf.py'
    def run(*args):
        result = subprocess.run([sys.executable, '-I', str(launcher), '--project', str(target), *args],
                                cwd=fixture, capture_output=True, text=True)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)
    run('init')
    run('doctor')
    run('begin', 'cache', '--request', 'cache smoke', '--workflow', 'code', '--output', 'project:answer.md')
    run('pack', 'cache')
    (target / 'answer.md').write_text('cache-only verified\n', encoding='utf-8')
    run('evidence', 'cache', '--name', 'proof.txt', '--text', 'Isolated cache execution succeeded')
    run('review', 'cache', '--result', 'passed', '--evidence', 'task:proof.txt', '--note', 'Fixture review')
    assert run('verify', 'cache')['status'] == 'completed'
    assert not (cache / '.agent-workflow').exists()
    assert subprocess.run([sys.executable, str(launcher), 'doctor'], capture_output=True).returncode != 0
    print(json.dumps({'status': 'passed', 'files': len(manifest), 'cache_runtime': True,
                      'rebuild_manifest_equal': True, 'fixture': str(fixture)}))


if __name__ == '__main__':
    # Build from a copied product source and make that source unavailable before runtime.
    origin = Path(tempfile.mkdtemp(prefix='workflow-origin-'))
    source = origin / 'source'
    source.mkdir()
    for name, body in product_files():
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
    archive = build(ROOT / 'dist', source)
    source.rename(origin / 'source-unavailable')
    verify(archive)
