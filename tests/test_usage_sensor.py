"""The usage sensor counts each native request once and never invents missing measurements."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import usage_sensor as sensor


def usage(inp=10, read=100, write=5, out=20):
    return {"input_tokens": inp, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": write, "output_tokens": out}


def assistant(msg_id, ts, blocks, use=None, side=False, model="claude-test"):
    row = {"type": "assistant", "timestamp": ts, "sessionId": "s1", "isSidechain": side,
           "message": {"id": msg_id, "model": model, "content": blocks}}
    if use is not None:
        row["message"]["usage"] = use
    return row


def tool_result(tool_id, ts):
    return {"type": "user", "timestamp": ts, "sessionId": "s1",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


def prompt(ts):
    return {"type": "user", "timestamp": ts, "sessionId": "s1", "message": {"content": "do it"}}


class UsageSensorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="usage-sensor-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, rows, raw_tail=""):
        path = self.root / name
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + raw_tail, encoding="utf-8")
        return path

    def test_request_repeated_per_content_block_is_counted_once(self):
        path = self.write("c.jsonl", [
            prompt("2026-10-25T00:00:00Z"),
            assistant("m1", "2026-10-25T00:00:01Z", [{"type": "thinking"}], usage()),
            assistant("m1", "2026-10-25T00:00:02Z", [{"type": "tool_use", "id": "t1"}], usage()),
            assistant("m1", "2026-10-25T00:00:02Z", [{"type": "text", "text": "x"}], usage()),
            tool_result("t1", "2026-10-25T00:00:07Z"),
            assistant("m2", "2026-10-25T00:00:08Z", [{"type": "text", "text": "done"}], usage(out=30)),
        ])
        record = sensor.claude_record(path)
        self.assertEqual(record["requests"], 2)
        self.assertEqual(record["tokens"], {"input_uncached": 20, "cache_read": 200, "cache_write": 10,
                                            "output": 50, "total_incl_cached": 280})
        self.assertEqual(record["quality"]["usage_lines"], 4)
        self.assertEqual(record["quality"]["duplicate_usage_lines"], 2)
        self.assertGreater(record["quality"]["overcount_factor_if_not_deduped"], 1.4)
        self.assertEqual(record["tool_calls"], 1)
        self.assertEqual(record["user_turns"], 1)
        self.assertEqual(record["tool_wait_seconds"]["p50"], 5.0)
        self.assertIsNone(record["human_waits"])
        self.assertEqual(record["wall_seconds"], 8.0)

    def test_conflicting_duplicates_are_reported_and_keep_the_larger_output(self):
        path = self.write("c.jsonl", [
            assistant("m1", "2026-10-25T00:00:01Z", [{"type": "text"}], usage(out=5)),
            assistant("m1", "2026-10-25T00:00:02Z", [{"type": "text"}], usage(out=9)),
        ])
        record = sensor.claude_record(path)
        self.assertEqual(record["quality"]["dedup_conflicts"], 1)
        self.assertEqual(record["tokens"]["output"], 9)

    def test_sidechain_is_separate_and_synthetic_is_excluded(self):
        path = self.write("c.jsonl", [
            assistant("m1", "2026-10-25T00:00:01Z", [{"type": "text"}], usage()),
            assistant("m2", "2026-10-25T00:00:02Z", [{"type": "text"}], usage(out=7), side=True),
            assistant("m3", "2026-10-25T00:00:03Z", [{"type": "text"}], usage(out=999), model="<synthetic>"),
        ])
        record = sensor.claude_record(path)
        self.assertEqual(record["requests"], 1)
        self.assertEqual(record["sidechain_requests"], 1)
        self.assertEqual(record["tokens"]["output"], 20)
        self.assertEqual(record["tokens_sidechain"]["output"], 7)

    def test_window_selects_only_requests_inside_it(self):
        path = self.write("c.jsonl", [
            assistant("m1", "2026-10-25T00:00:01Z", [{"type": "text"}], usage()),
            assistant("m2", "2026-10-25T01:00:00Z", [{"type": "text"}], usage(out=40)),
        ])
        since = sensor.parse_ts("2026-10-25T00:30:00Z")
        record = sensor.claude_record(path, since=since)
        self.assertEqual(record["requests"], 1)
        self.assertEqual(record["tokens"]["output"], 40)

    def test_missing_usage_is_unavailable_and_bad_lines_are_skipped(self):
        path = self.write("c.jsonl", [assistant("m1", "2026-10-25T00:00:01Z", [{"type": "text"}])],
                          raw_tail='{"type": "assistant", "message": {"id": "m9"')
        record = sensor.claude_record(path)
        self.assertIsNone(record["tokens"])
        self.assertEqual(record["provenance"]["tokens"], "unavailable")

    def test_codex_sums_turns_and_separates_cached_input(self):
        path = self.write("t.jsonl", [
            {"type": "thread.started", "thread_id": "th1"},
            {"type": "item.completed", "item": {"type": "command_execution"}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60,
                                                 "cache_write_input_tokens": 0, "output_tokens": 7}},
            {"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 3}},
        ])
        record = sensor.codex_record(path)
        self.assertEqual(record["tokens"], {"input_uncached": 80, "cache_read": 70, "cache_write": 0,
                                            "output": 10, "total_incl_cached": 160})
        self.assertEqual(record["session_ids"], ["th1"])
        self.assertEqual(record["tool_calls"], 1)
        self.assertIsNone(record["wall_seconds"])

    def test_codex_without_usage_is_unavailable(self):
        path = self.write("t.jsonl", [{"type": "thread.started", "thread_id": "th1"}])
        self.assertIsNone(sensor.codex_record(path)["tokens"])

    def test_verify_pilot_fails_on_a_wrong_total_and_on_a_missing_trace(self):
        trace_dir = self.root / "trials" / "good" / "evidence/native-session"
        trace_dir.mkdir(parents=True)
        (trace_dir / "trace.jsonl").write_text(json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 7}}) + "\n",
            encoding="utf-8")
        table = self.root / "trials.csv"
        table.write_text("trial_id,native_tokens,input_tokens,cached_input_tokens,output_tokens\n"
                         "good,107,100,60,7\nwrong,1,1,0,0\n", encoding="utf-8")
        result = sensor.verify_pilot(self.root / "trials", table)
        self.assertEqual(result["matched"], 1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["rows"][1]["reason"], "trace missing")

    def test_cli_rejects_an_invalid_timestamp_and_returns_three_on_mismatch(self):
        path = self.write("c.jsonl", [assistant("m1", "2026-10-25T00:00:01Z", [{"type": "text"}], usage())])
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = sensor.main(["claude", str(path), "--since", "not-a-time"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(errors.getvalue())["status"], "rejected")
        table = self.root / "trials.csv"
        table.write_text("trial_id,native_tokens,input_tokens,cached_input_tokens,output_tokens\nx,1,1,0,0\n", encoding="utf-8")
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = sensor.main(["verify-pilot", "--trials-dir", str(self.root), "--csv", str(table)])
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
