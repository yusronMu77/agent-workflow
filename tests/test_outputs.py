import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_core.engine import Engine
from workflow_core.outputs import Outputs, wire
from workflow_core.project import Project, Rejected


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workflow-output-')
        self.root = Path(self.temp.name).resolve()
        (self.root / 'AGENTS.md').write_text('Read all mandatory instructions.\n', encoding='utf-8')
        Project.init(self.root)
        self.engine = Engine(self.root)
        self.engine.create('test', 'Inspect test output', 'code', 'project:answer.md')
        self.engine.pack('test')
        self.outputs = Outputs(self.engine)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def capture(self, text, **kwargs):
        (self.root / 'tool.txt').write_text(text, encoding='utf-8')
        return self.outputs.capture('test', 'project:tool.txt', **kwargs)

    def test_late_failure_survives_preview_and_exit_code(self):
        result = self.capture('passed\n' * 3000 + 'ERROR broken at app.py:91\n' + 'done\n' * 200, exit_code=7, budget=2048)
        self.assertTrue(any('app.py:91' in x['text'] for x in result['lines']))
        self.assertEqual(result['exit_code'], 7)
        self.assertFalse(result['complete'])
        self.assertLessEqual(len(wire(result)), 2048)

    def test_archive_exact_after_source_changes(self):
        result = self.capture('first\nneedle exact value\nlast\n')
        (self.root / 'tool.txt').write_text('source changed')
        fetched = self.outputs.read('test', result['key'], start=2, end=2)
        self.assertEqual(fetched['lines'], [dict(line=2, column_start=1, text='needle exact value', clipped=False)])
        self.assertFalse(fetched['complete'])

    def test_literal_search_and_no_match(self):
        result = self.capture('a.*b\nother\nA.*B\n')
        found = self.outputs.read('test', result['key'], find='a.*b')
        self.assertEqual(found['matched_lines'], 2)
        self.assertEqual([x['line'] for x in found['lines']], [1, 3])
        self.assertEqual(self.outputs.read('test', result['key'], find='absent')['lines'], [])

    def test_long_unicode_line_budget_and_clipping(self):
        result = self.capture('故障Ω' * 15000, budget=2048)
        self.assertLessEqual(len(wire(result)), 2048)
        self.assertFalse(result['complete'])
        # Use a larger view to prove clipping is marked when the line fits.
        fetched = self.outputs.read('test', result['key'], budget=6000)
        self.assertTrue(fetched['lines'][0]['clipped'])

    def test_small_payload_no_savings_claim_and_repeat_reads_counted(self):
        result = self.capture('ok\n')
        self.outputs.read('test', result['key'])
        self.outputs.read('test', result['key'])
        metrics = self.outputs.metrics('test')
        self.assertEqual(metrics['captures'], 1)
        self.assertEqual(metrics['reads'], 2)
        self.assertLess(metrics['net_payload_bytes_proxy'], 0)
        self.assertIsNone(metrics['native_tokens'])
        self.assertIsNone(metrics['provider_cost'])
        self.assertEqual(result['delivered_bytes'], len(wire(result)))

    def test_long_line_search_returns_matching_window(self):
        result = self.capture('x' * 10000 + 'NEEDLE exact' + 'y' * 10000)
        fetched = self.outputs.read('test', result['key'], find='NEEDLE')
        self.assertIn('NEEDLE exact', fetched['lines'][0]['text'])
        self.assertGreater(fetched['lines'][0]['column_start'], 1)
        self.assertTrue(fetched['lines'][0]['clipped'])

    def test_raw_tamper_rejected(self):
        result = self.capture('raw\n')
        (self.outputs.directory('test', result['key']) / 'raw.txt').write_text('tamper')
        with self.assertRaises(Rejected):
            self.outputs.read('test', result['key'])

    def test_manifest_tamper_rejected(self):
        result = self.capture('raw\n', exit_code=4)
        path = self.outputs.directory('test', result['key']) / 'manifest.json'
        metadata = json.loads(path.read_text())
        metadata['exit_code'] = 0
        path.write_text(json.dumps(metadata))
        with self.assertRaises(Rejected):
            self.outputs.read('test', result['key'])

    def test_path_credential_and_instruction_guards(self):
        for ref in ('project:../tool.txt', 'project:.mcp.json', 'project:.env', 'project:AGENTS.md'):
            with self.assertRaises(Rejected):
                self.outputs.capture('test', ref)
        with self.assertRaises(Rejected):
            self.outputs.read('test', '../elsewhere')

    def test_invalid_utf8_or_budget_creates_no_archive(self):
        (self.root / 'tool.txt').write_bytes(b'\xff\xfe\x00')
        with self.assertRaises(UnicodeError):
            self.outputs.capture('test', 'project:tool.txt')
        with self.assertRaises(Rejected):
            self.capture('ok', budget=1)
        self.assertEqual(self.outputs.metrics('test')['captures'], 0)

    def test_blocked_task_cannot_capture(self):
        self.engine.unknown('test', 'uncertain effect')
        with self.assertRaises(Rejected):
            self.capture('ok')

    def test_failed_event_transaction_leaves_untrusted_orphan(self):
        with patch.object(self.engine.store, 'event', side_effect=RuntimeError('crash before event commit')):
            with self.assertRaises(RuntimeError):
                self.capture('ok')
        keys = list((self.engine.directory('test') / 'outputs').iterdir())
        self.assertEqual(len(keys), 1)
        with self.assertRaises(Rejected):
            self.outputs.read('test', keys[0].name)
        self.assertEqual(self.outputs.metrics('test')['captures'], 0)

    def test_pack_still_has_complete_policy_and_metric(self):
        metrics = self.outputs.metrics('test')
        self.assertGreater(metrics['policy_bytes'], 0)
        self.assertGreater(metrics['pack_bytes'], metrics['policy_bytes'])
        pack = self.engine.pack('test')
        self.assertIn('Read all mandatory instructions.', Path(pack['context_path']).read_text())


if __name__ == '__main__':
    unittest.main()
