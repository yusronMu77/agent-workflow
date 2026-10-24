"""Repeatable payload benchmark, not model-token or billing measurement."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.engine import Engine
from workflow_core.outputs import Outputs
from workflow_core.project import Project


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fixture = Path(tempfile.mkdtemp(prefix='workflow-output-benchmark-'))
    (fixture/'AGENTS.md').write_text('Keep mandatory instructions complete.\n', encoding='utf-8')
    Project.init(fixture)
    rows = []
    with_engine = Engine(fixture)
    try:
        for name, body in (
            ('verbose', ''.join(f'PASS test_{i:04d}: validation successful\n' for i in range(3000)) + 'ERROR app.py:91 expected 42 got 0\n'),
            ('scoped', 'ERROR app.py:91 expected 42 got 0\n'),
        ):
            with_engine.create(name, 'Inspect failure', 'code', f'project:{name}-answer.md')
            pack = with_engine.pack(name)
            (fixture/f'{name}.txt').write_text(body, encoding='utf-8')
            output = Outputs(with_engine)
            initial = output.capture(name, f'project:{name}.txt', exit_code=1, budget=2048)
            detail = output.read(name, initial['key'], find='expected 42', budget=2048)
            assert detail['lines'][-1]['text'] == 'ERROR app.py:91 expected 42 got 0'
            metrics = output.metrics(name)
            rows.append(dict(case=name, initial_view_bytes=initial['delivered_bytes'],
                             detail_view_bytes=detail['delivered_bytes'], **metrics))
        report = dict(fixture=str(fixture), workload='Synthetic log fixtures; one capture + one exact retrieval each',
                      cases=rows, limitations='Payload bytes only. Short scoped output can be cheaper without capture. No native provider usage measured.')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps(report))
    finally:
        with_engine.close()


if __name__ == '__main__':
    main()
