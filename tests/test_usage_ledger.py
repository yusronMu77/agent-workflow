"""The ledger records counts per session, keeps owner labels across re-scans, and never stores conversation text."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import usage_ledger as ledger
import usage_sensor as sensor

SECRET = "CANARY-prompt-text-do-not-store"


def usage(inp=10, read=100, write=5, out=20):
    return {"input_tokens": inp, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write, "output_tokens": out}


def claude_rows(session, count):
    rows = [{"type": "user", "timestamp": "2026-10-25T00:00:00Z", "sessionId": session, "cwd": "C:\\work\\demo",
             "message": {"content": SECRET}}]
    for i in range(count):
        rows.append({"type": "assistant", "timestamp": "2026-10-25T00:00:%02dZ" % (i + 1), "sessionId": session,
                     "cwd": "C:\\work\\demo", "message": {"id": "m%d" % i, "model": "claude-test", "usage": usage(),
                                                          "content": [{"type": "text", "text": SECRET}]}})
    return rows


def token_count(ts, total_in, cached, out):
    total = {"input_tokens": total_in, "cached_input_tokens": cached, "cache_write_input_tokens": 0, "output_tokens": out}
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": total}}}


def rollout_rows(session):
    return [
        {"timestamp": "2026-10-25T01:00:00Z", "type": "session_meta", "payload": {"id": session, "cwd": "c:\\work\\other"}},
        {"timestamp": "2026-10-25T01:00:01Z", "type": "turn_context", "payload": {"type": None, "model": "gpt-test"}},
        {"timestamp": "2026-10-25T01:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": SECRET}},
        {"timestamp": "2026-10-25T01:00:02Z", "type": "response_item", "payload": {"type": "custom_tool_call"}},
        token_count("2026-10-25T01:00:03Z", 100, 60, 7),
        token_count("2026-10-25T01:00:03Z", 100, 60, 7),
        token_count("2026-10-25T01:00:09Z", 250, 180, 12),
    ]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="usage-ledger-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.claude, self.codex = self.root / "claude", self.root / "codex"
        (self.claude / "proj").mkdir(parents=True)
        (self.codex / "2026" / "10").mkdir(parents=True)
        self.ledger = self.root / "state" / "ledger.jsonl"

    def put(self, path, rows):
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def scan(self, **kwargs):
        return ledger.scan(self.ledger, self.claude, self.codex, **kwargs)

    def test_rollout_counts_requests_by_changed_total_and_reads_cumulative_usage(self):
        path = self.codex / "rollout-x.jsonl"
        self.put(path, rollout_rows("th1"))
        record = sensor.codex_rollout_record(path)
        self.assertEqual(record["requests"], 2)
        self.assertEqual(record["tokens"], {"input_uncached": 70, "cache_read": 180, "cache_write": 0,
                                            "output": 12, "total_incl_cached": 262})
        self.assertEqual((record["tool_calls"], record["user_turns"], record["models"]), (1, 1, ["gpt-test"]))
        self.assertEqual(record["wall_seconds"], 9.0)

    def test_scan_records_both_clients_without_any_conversation_text(self):
        self.put(self.claude / "proj" / "a.jsonl", claude_rows("s1", 3))
        self.put(self.codex / "2026" / "10" / "rollout-x.jsonl", rollout_rows("th1"))
        result = self.scan()
        self.assertEqual((result["entries"], result["added"]), (2, 2))
        text = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        self.assertNotIn("C:\\\\work", text)
        entries = ledger.load(self.ledger)
        self.assertEqual(entries["claude:s1"]["project"], "demo")
        self.assertEqual(entries["codex:th1"]["project"], "other")
        self.assertEqual(entries["claude:s1"]["requests"], 3)
        self.assertEqual(entries["claude:s1"]["outcome"], {"accepted": "unknown", "source": None})

    def test_rescan_refreshes_a_grown_session_and_keeps_owner_labels(self):
        log = self.claude / "proj" / "a.jsonl"
        self.put(log, claude_rows("s1", 2))
        self.scan()
        ledger.mark(self.ledger, "claude:s1", label="refactor", tier=1, accepted="yes")
        self.put(log, claude_rows("s1", 4))
        result = self.scan()
        self.assertEqual((result["entries"], result["added"], result["refreshed"]), (1, 0, 1))
        entry = ledger.load(self.ledger)["claude:s1"]
        self.assertEqual(entry["requests"], 4)
        self.assertEqual((entry["label"], entry["tier"], entry["outcome"]["accepted"]), ("refactor", {"value": 1, "source": "owner"}, "yes"))

    def test_rescan_without_change_is_idempotent(self):
        self.put(self.claude / "proj" / "a.jsonl", claude_rows("s1", 2))
        self.scan()
        before = self.ledger.read_text(encoding="utf-8")
        result = self.scan()
        self.assertEqual((result["added"], result["refreshed"]), (0, 0))
        self.assertEqual(self.ledger.read_text(encoding="utf-8"), before)

    def test_session_without_usage_is_skipped_not_recorded_as_zero(self):
        self.put(self.claude / "proj" / "empty.jsonl", [{"type": "user", "sessionId": "s9", "message": {"content": "hi"}}])
        result = self.scan()
        self.assertEqual((result["entries"], result["skipped_without_usage"]), (0, 1))

    def test_logs_are_never_modified_and_missing_roots_are_tolerated(self):
        log = self.claude / "proj" / "a.jsonl"
        self.put(log, claude_rows("s1", 2))
        before = log.read_bytes()
        ledger.scan(self.ledger, self.claude, self.root / "absent")
        self.assertEqual(log.read_bytes(), before)

    def test_mark_rejects_an_unknown_entry_and_report_counts_unknown_outcomes(self):
        self.put(self.claude / "proj" / "a.jsonl", claude_rows("s1", 2))
        self.scan()
        with self.assertRaises(KeyError):
            ledger.mark(self.ledger, "claude:nope", label="x")
        summary = ledger.report(self.ledger)
        self.assertEqual((summary["entries"], summary["labeled"], summary["outcome_known"]), (1, 0, 0))
        self.assertEqual(summary["by_client"]["claude"]["requests"]["median"], 2)

    def test_cli_reports_rejection_with_exit_code_two(self):
        errors = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(errors):
            code = ledger.main(["mark", "claude:nope", "--ledger", str(self.ledger), "--label", "x"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(errors.getvalue())["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
