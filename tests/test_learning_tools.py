"""K6-K9 and the lazy trigger: weak evidence stays weak, decisions are immutable, randomization excludes irreversible writes."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import assignment
import change_proposals as proposals
import cost_per_outcome as cost
import jsonl_store as store
import lazy_adopt
import owner_feedback as feedback
import usage_ledger

HASH = "a" * 64


class TempCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="learning-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)


class StoreTests(TempCase):
    def test_chain_detects_edit_and_removal(self):
        path = self.dir / "chain.jsonl"
        for n in range(3):
            store.chained_append(path, {"n": n})
        self.assertTrue(store.chain_ok(path))
        rows = store.rows(path)
        rows[1]["n"] = 99
        store.save(path, rows)
        self.assertFalse(store.chain_ok(path))
        store.save(path, [r for i, r in enumerate(store.rows(path)) if i != 0])
        self.assertFalse(store.chain_ok(path))

    def test_chain_detects_edit_or_removal_of_the_last_row(self):
        path = self.dir / "chain.jsonl"
        store.chained_append(path, {"n": 0})
        store.chained_append(path, {"n": 1})
        good = store.rows(path)
        store.save(path, [good[0], {**good[1], "n": 99}])
        self.assertFalse(store.chain_ok(path))
        store.save(path, good[:1])
        self.assertFalse(store.chain_ok(path))
        store.save(path, good)
        self.assertTrue(store.chain_ok(path))
        self.assertTrue(store.chain_ok(self.dir / "never-written.jsonl"))

    def test_interrupted_last_line_is_skipped(self):
        path = self.dir / "log.jsonl"
        store.append(path, {"a": 1})
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"broken": ')
        self.assertEqual(store.rows(path), [{"a": 1}])


class FeedbackTests(TempCase):
    def setUp(self):
        super().setUp()
        self.path = self.dir / "feedback.jsonl"

    def add(self, task="t1", text="Judul salah", **kw):
        kw.setdefault("kind", "defect")
        kw.setdefault("part", "title")
        kw.setdefault("artifact_sha256", HASH)
        kw.setdefault("evidence", "diff")
        return feedback.add(self.path, task, text, **kw)

    def test_clean_owner_defect_is_a_full_weight_candidate_never_a_fact(self):
        result = self.add()
        self.assertEqual((result["status"], result["weight"]), ("candidate", 1.0))
        self.assertEqual(result["route"], "regression_case")
        self.assertFalse(feedback.load(self.path)[0]["may_change_validators"])

    def test_data_from_tools_web_or_third_parties_is_not_feedback(self):
        for source in ("tool_output", "web", "file", "third_party", "unknown"):
            self.assertEqual(self.add(task="t-" + source, source=source)["status"], "not_feedback")
        self.assertEqual(self.add(task="trace", text="Traceback (most recent call last):\n  File x")["status"], "not_feedback")
        self.assertEqual(self.add(task="long", text="x" * 2500)["status"], "not_feedback")

    def test_silence_is_unknown_and_never_accepted(self):
        result = feedback.add(self.path, "t1", "", via="silence")
        self.assertEqual((result["status"], result["weight"]), ("unknown", 0.0))
        with self.assertRaises(ValueError):
            feedback.review(self.path, result["id"], "accepted", "owner said")

    def test_weak_signals_are_capped(self):
        self.assertEqual(self.add(task="a", text="sudah bagus", kind="satisfaction")["weight"], 0.25)
        self.assertEqual(self.add(task="b", text="ya", kind="unknown")["weight"], 0.25)
        self.assertEqual(self.add(task="c", artifact_sha256=None, part=None, text="kurang pas")["weight"], 0.25)
        self.assertEqual(self.add(task="d", kind="unknown", via="revert", text="undo")["weight"], 0.5)
        self.assertEqual(self.add(task="e", via="answer", leading=True, text="betul begitu")["weight"], 0.25)

    def test_missing_part_asks_one_sharpening_question(self):
        result = self.add(part=None)
        self.assertEqual(result["followup_question"], "Bagian mana yang perlu diubah?")

    def test_duplicates_are_not_counted_twice(self):
        first = self.add()
        self.assertEqual(self.add()["status"], "duplicate")
        self.assertEqual(len(feedback.feedback_rows(feedback.load(self.path))), 1)
        self.assertEqual(first["status"], "candidate")

    def test_opposite_polarity_on_the_same_artifact_part_is_a_conflict_for_the_owner(self):
        first = self.add()
        second = self.add(task="t2", text="judul bagus sekali", kind="satisfaction")
        self.assertTrue(second["needs_owner_decision"])
        stored = {e["id"]: e for e in feedback.feedback_rows(feedback.load(self.path))}
        self.assertEqual(stored[first["id"]]["conflicts"], [second["id"]])
        self.assertEqual(feedback.report(self.path)["open_conflicts"], sorted([first["id"], second["id"]]))

    def test_other_parts_do_not_conflict(self):
        self.add()
        self.assertFalse(self.add(task="t2", text="isi bagus", kind="satisfaction", part="body")["needs_owner_decision"])

    def test_owner_decision_is_immutable_and_idempotent(self):
        item = self.add()
        self.assertFalse(feedback.review(self.path, item["id"], "accepted", "benar")["idempotent"])
        self.assertTrue(feedback.review(self.path, item["id"], "accepted", "benar")["idempotent"])
        with self.assertRaises(ValueError):
            feedback.review(self.path, item["id"], "rejected", "berubah pikiran")
        with self.assertRaises(ValueError):
            feedback.review(self.path, item["id"], "accepted", " ")
        with self.assertRaises(KeyError):
            feedback.review(self.path, "nope", "accepted", "x")

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(ValueError):
            feedback.add(self.path, "t", "x", kind="bogus")
        with self.assertRaises(ValueError):
            feedback.add(self.path, " ", "x")

    def test_a_pattern_generalizes_only_after_three_distinct_tasks(self):
        for task in ("t1", "t2"):
            self.add(task=task, text="heading " + task, part="heading")
        self.assertEqual(feedback.report(self.path)["generalizable_patterns"], [])
        self.add(task="t3", text="heading t3", part="heading")
        self.assertEqual(feedback.report(self.path)["generalizable_patterns"], [{"kind": "defect", "part": "heading", "tasks": 3}])

    def test_ask_policy(self):
        ask = lambda **kw: feedback.should_ask(self.path, kw.pop("task", "t"), kw.pop("risk", "light"), **kw)
        self.assertTrue(ask(risk="write")["ask"])
        self.assertTrue(ask(tier=2)["ask"])
        self.assertFalse(ask(risk="write", internal_failed=True)["ask"])
        self.assertFalse(ask(owner_corrected=True, tier=2)["ask"])
        self.assertTrue(ask(task="light-1", every=1, daily_budget=100)["ask"])
        self.assertFalse(ask(task="light-2", every=0)["ask"])

    def test_daily_budget_limits_light_tasks_but_not_writes(self):
        for n in range(2):
            feedback.should_ask(self.path, "w%d" % n, "light", every=1, daily_budget=2)
        self.assertFalse(feedback.should_ask(self.path, "late", "light", every=1, daily_budget=2)["ask"])
        self.assertTrue(feedback.should_ask(self.path, "risky", "write", daily_budget=2)["ask"])

    def test_cli_round_trip(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = feedback.main(["add", "t1", "--text", "Judul salah", "--kind", "defect", "--part", "title",
                                  "--artifact-sha256", HASH, "--evidence", "diff", "--store", str(self.path)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "candidate")
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(feedback.main(["review", "nope", "--decision", "accepted", "--note", "x", "--store", str(self.path)]), 2)


def entry(i, label, accepted, out=100, requests=10):
    return {"entry_id": "claude:%s" % i, "client": "claude", "label": label, "requests": requests, "wall_seconds": 60, "tier": None,
            "tokens": {"input_uncached": 10, "cache_read": 1000, "cache_write": 100, "output": out, "total_incl_cached": 1110 + out},
            "outcome": {"accepted": accepted, "source": "owner"}}


class CostTests(TempCase):
    def setUp(self):
        super().setUp()
        self.ledger, self.fb = self.dir / "ledger.jsonl", self.dir / "feedback.jsonl"

    def save(self, *entries):
        usage_ledger.save(self.ledger, {e["entry_id"]: e for e in entries})

    def test_cost_per_accepted_counts_every_attempt_and_never_treats_unknown_as_yes(self):
        entries = [entry(1, "x", "yes"), entry(2, "x", "yes"), entry(3, "x", "no"), entry(4, "x", "unknown")]
        out = cost.summarize(entries, [])
        self.assertEqual((out["accepted_yes"], out["accepted_no"], out["outcome_unknown"]), (2, 1, 1))
        self.assertEqual(out["acceptance_rate"], 0.667)
        self.assertEqual(out["cost"]["uncached_plus_output"]["total"], 440.0)
        self.assertEqual(out["cost"]["uncached_plus_output"]["per_accepted"], 220.0)
        self.assertIsNone(out["coverage_warning"])

    def test_coverage_warning_and_no_division_by_zero(self):
        out = cost.summarize([entry(1, "x", "unknown"), entry(2, "x", "unknown"), entry(3, "x", "no")], [])
        self.assertIsNotNone(out["coverage_warning"])
        self.assertIsNone(out["cost"]["raw_total"]["per_accepted"])
        self.assertIsNone(cost.wilson(0, 0))

    def test_weighty_owner_defect_feedback_marks_no_but_owner_mark_wins(self):
        feedback.add(self.fb, "claude:1", "salah", kind="defect", part="p", artifact_sha256=HASH, evidence="e")
        feedback.add(self.fb, "x", "bagus", kind="satisfaction", part="p", artifact_sha256="b" * 64, evidence="e")
        items = store.rows(self.fb)
        self.assertEqual(cost.accepted_of(entry(1, None, "unknown"), items), ("no", "owner_feedback"))
        self.assertEqual(cost.accepted_of(entry(1, None, "yes"), items), ("yes", "owner_mark"))
        self.assertEqual(cost.accepted_of(entry(2, "x", "unknown"), items), ("unknown", None))

    def test_compare_refuses_thin_data(self):
        self.save(entry(1, "a", "yes"), entry(2, "b", "yes"))
        result = cost.compare(self.ledger, self.fb, "a", "b")
        self.assertEqual(result["status"], "refused")
        self.assertEqual(len(result["reasons"]), 4)

    def test_compare_is_descriptive_unless_both_labels_are_randomized(self):
        a = [entry("a%d" % n, "native", "yes", out=100) for n in range(5)]
        b = [entry("b%d" % n, "workflow", "yes", out=200) for n in range(5)]
        k = [{**e, "entry_id": "k9-" + e["entry_id"], "label": "k9:b1:" + e["label"]} for e in a + b]
        self.save(*(a + b + k))
        loose = cost.compare(self.ledger, self.fb, "native", "workflow")
        self.assertEqual(loose["status"], "descriptive_only")
        self.assertAlmostEqual(loose["cost_per_accepted_ratio_a_over_b"]["uncached_plus_output"], 110 / 210, places=3)
        causal = cost.compare(self.ledger, self.fb, "k9:b1:native", "k9:b1:workflow")
        self.assertEqual(causal["status"], "causal")

    def test_report_groups_and_cli_exit_codes(self):
        self.save(entry(1, "a", "yes"), entry(2, "b", "no"))
        groups = cost.report(self.ledger, self.fb)["groups"]
        self.assertEqual(set(groups), {"a|claude", "b|claude"})
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cost.main(["compare", "a", "b", "--ledger", str(self.ledger), "--feedback", str(self.fb)]), 3)
            self.assertEqual(cost.main(["report", "--by", "nonsense", "--ledger", str(self.ledger), "--feedback", str(self.fb)]), 2)


CASES = [
    {"id": "c1", "features": {"risk_write": True}, "expected_tier": 2},
    {"id": "c2", "features": {"explicit": True}, "expected_tier": 1},
    {"id": "c3", "features": {"requests_so_far": 35}, "expected_tier": 1},
    {"id": "c4", "features": {"requests_so_far": 10}, "expected_tier": 0},
    {"id": "c6", "features": {"requests_so_far": 28}, "expected_tier": 1},
]
FRESH = [{"id": "a1", "features": {"requests_so_far": 26}, "expected_tier": 1}, {"id": "a2", "features": {"requests_so_far": 5}, "expected_tier": 0}]


class ProposalTests(TempCase):
    def setUp(self):
        super().setUp()
        self.root = self.dir / "p"
        self.cases = self.dir / "cases.json"
        self.cases.write_text(json.dumps(CASES), encoding="utf-8")
        self.fresh = self.dir / "fresh.json"
        self.fresh.write_text(json.dumps(FRESH), encoding="utf-8")
        proposals.freeze(self.root, "base", self.cases)

    def propose(self, target, to, kind="threshold"):
        return proposals.propose(self.root, kind, target, to, "because")["id"]

    def test_better_threshold_passes_but_is_not_acceptance_without_unseen_cases(self):
        pid = self.propose("lazy_requests", 25)
        result = proposals.evaluate(self.root, pid, "base")
        self.assertEqual(result["verdict"], "pass")
        self.assertIn("agreement", result["better"])
        self.assertTrue(result["acceptance_status"].startswith("missing"))
        with self.assertRaises(ValueError):
            proposals.decide(self.root, pid, "accept", "ok")
        result = proposals.evaluate(self.root, pid, "base", self.fresh)
        self.assertTrue(result["acceptance_status"].startswith("checked"))
        decided = proposals.decide(self.root, pid, "accept", "ok")
        self.assertFalse(decided["configuration_changed"])
        self.assertTrue(decided["chain_ok"])

    def test_lowering_the_risk_tier_is_an_unsafe_miss_and_fails(self):
        pid = self.propose("risk_write_tier", 1)
        result = proposals.evaluate(self.root, pid, "base")
        self.assertEqual(result["verdict"], "fail")
        with self.assertRaises(ValueError):
            proposals.decide(self.root, pid, "accept", "ok")

    def test_tradeoff_needs_explicit_owner_acceptance(self):
        pid = self.propose("lazy_requests", 40)
        result = proposals.evaluate(self.root, pid, "base", self.fresh)
        self.assertEqual(result["verdict"], "tradeoff_for_owner")
        with self.assertRaises(ValueError):
            proposals.decide(self.root, pid, "accept", "ok")
        self.assertTrue(proposals.decide(self.root, pid, "accept", "ok", owner_accepts_tradeoff=True)["chain_ok"])

    def test_a_proposer_set_overlap_is_flagged_not_counted_as_acceptance(self):
        pid = self.propose("lazy_requests", 25)
        overlap = self.dir / "overlap.json"
        overlap.write_text(json.dumps(CASES[:2]), encoding="utf-8")
        result = proposals.evaluate(self.root, pid, "base", overlap)
        self.assertEqual(result["acceptance"]["overlap_with_regression_set"], ["c1", "c2"])
        self.assertTrue(result["acceptance_status"].startswith("missing"))

    def test_worse_on_unseen_cases_turns_a_pass_into_a_fail(self):
        pid = self.propose("lazy_requests", 25)
        bad = self.dir / "bad.json"
        bad.write_text(json.dumps([{"id": "z1", "features": {"requests_so_far": 27}, "expected_tier": 0}]), encoding="utf-8")
        self.assertEqual(proposals.evaluate(self.root, pid, "base", bad)["verdict"], "fail")

    def test_wording_changes_need_owner_review_and_owner_only_kinds_are_never_evaluated(self):
        wording = proposals.propose(self.root, "wording", "SKILL.md", "new text", "clearer")["id"]
        self.assertEqual(proposals.evaluate(self.root, wording, "base")["verdict"], "needs_owner_review")
        owner = proposals.propose(self.root, "permission", "allow", "x", "faster")
        self.assertEqual(owner["status"], "owner_only")
        with self.assertRaises(ValueError):
            proposals.evaluate(self.root, owner["id"], "base")
        self.assertEqual(proposals.decide(self.root, owner["id"], "accept", "owner decides")["status"], "accepted")

    def test_decisions_are_immutable_idempotent_and_chained(self):
        pid = self.propose("lazy_requests", 25)
        proposals.evaluate(self.root, pid, "base", self.fresh)
        proposals.decide(self.root, pid, "accept", "ok")
        self.assertTrue(proposals.decide(self.root, pid, "accept", "ok")["idempotent"])
        with self.assertRaises(ValueError):
            proposals.decide(self.root, pid, "reject", "no")
        self.assertTrue(proposals.status(self.root)["decision_chain_ok"])
        path = proposals.paths(self.root)["decisions"]
        rows = store.rows(path)
        rows[0]["note"] = "edited"
        store.save(path, rows)
        self.assertFalse(proposals.status(self.root)["decision_chain_ok"])

    def test_frozen_set_cannot_be_overwritten_or_tampered_with(self):
        with self.assertRaises(ValueError):
            proposals.freeze(self.root, "base", self.cases)
        target = proposals.paths(self.root)["sets"] / "base.json"
        record = json.loads(target.read_text(encoding="utf-8"))
        record["cases"][0]["expected_tier"] = 0
        target.write_text(json.dumps(record), encoding="utf-8")
        pid = self.propose("lazy_requests", 25)
        with self.assertRaises(ValueError):
            proposals.evaluate(self.root, pid, "base")

    def test_frozen_set_validation(self):
        bad = self.dir / "bad.json"
        for payload in ([], [{"id": "x"}], [CASES[0], CASES[0]]):
            bad.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                proposals.freeze(self.root, "other", bad)

    def test_a_decision_after_the_set_changed_is_refused(self):
        pid = self.propose("lazy_requests", 25)
        proposals.evaluate(self.root, pid, "base", self.fresh)
        target = proposals.paths(self.root)["sets"] / "base.json"
        record = json.loads(target.read_text(encoding="utf-8"))
        record["cases"][0]["expected_tier"] = 0
        target.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ValueError):
            proposals.decide(self.root, pid, "accept", "ok")

    def test_triage_rules(self):
        config = proposals.BASE_CONFIG
        self.assertEqual(proposals.triage({"features": {"risk_write": True, "explicit": True}}, config), 2)
        self.assertEqual(proposals.triage({"features": {"explicit": True}}, config), 1)
        self.assertEqual(proposals.triage({"features": {"requests_so_far": 30}}, config), 1)
        self.assertEqual(proposals.triage({"features": {}}, config), 0)


class AssignmentTests(TempCase):
    def setUp(self):
        super().setUp()
        self.root = self.dir / "assign"
        assignment.approve(self.root, "b1", 10, "seed", "owner: boleh coba 10 task")

    def test_approval_rules(self):
        with self.assertRaises(ValueError):
            assignment.approve(self.root, "b1", 5, "s", "again")
        with self.assertRaises(ValueError):
            assignment.approve(self.root, "b2", 5, "s", "owner ok")
        assignment.close(self.root, "b1")
        with self.assertRaises(ValueError):
            assignment.approve(self.root, "b2", 5, "s", " ")
        self.assertEqual(assignment.approve(self.root, "b2", 5, "s", "owner ok")["status"], "approved")
        with self.assertRaises(ValueError):
            assignment.approve(self.dir / "fresh", "first", 21, "s", "owner ok")

    def test_assign_without_approval_is_refused(self):
        with self.assertRaises(KeyError):
            assignment.assign(self.root, "never-approved", "t", "refactor module")

    def test_arms_stay_balanced_in_blocks_of_two_and_assignment_is_idempotent(self):
        rows = [assignment.assign(self.root, "b1", "t%d" % n, "refactor module %d" % n) for n in range(10)]
        for block in range(0, 10, 2):
            self.assertEqual({rows[block]["arm"], rows[block + 1]["arm"]}, {"native", "workflow"})
        again = assignment.assign(self.root, "b1", "t3", "refactor module 3")
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["arm"], rows[3]["arm"])
        self.assertTrue(rows[0]["label"].startswith("k9:b1:"))
        with self.assertRaises(ValueError):
            assignment.assign(self.root, "b1", "t10", "one more")

    def test_assignment_is_deterministic_for_a_seed(self):
        other = self.dir / "other"
        assignment.approve(other, "b1", 10, "seed", "owner ok")
        mine = [assignment.assign(self.root, "b1", "t%d" % n, "x")["arm"] for n in range(6)]
        theirs = [assignment.assign(other, "b1", "t%d" % n, "x")["arm"] for n in range(6)]
        self.assertEqual(mine, theirs)

    def test_irreversible_writes_are_never_randomized(self):
        for task, request in (("a", "delete the old branch"), ("b", "git push the release"), ("c", "update prod config"),
                              ("d", "hapus file lama"), ("e", "send email to client")):
            row = assignment.assign(self.root, "b1", task, request)
            self.assertTrue(row["excluded"], request)
            self.assertIsNone(row["arm"])
        self.assertTrue(assignment.assign(self.root, "b1", "f", "plain", risky=True)["excluded"])
        status = assignment.status(self.root, "b1")
        self.assertEqual((status["excluded"], status["remaining"]), (6, 10))

    def test_custom_exclusion_and_bad_pattern(self):
        assignment.add_exclusion(self.root, r"\bsecrets?\b", "touches secrets")
        self.assertTrue(assignment.assign(self.root, "b1", "s1", "rotate the secret")["excluded"])
        with self.assertRaises(Exception):
            assignment.add_exclusion(self.root, "(", "bad")
        with self.assertRaises(ValueError):
            assignment.add_exclusion(self.root, "x", " ")

    def test_closed_batch_accepts_no_more_randomization_and_link_labels_the_ledger(self):
        row = assignment.assign(self.root, "b1", "t1", "refactor")
        ledger = self.dir / "ledger.jsonl"
        usage_ledger.save(ledger, {"claude:1": entry(1, None, "unknown")})
        self.assertEqual(assignment.link(self.root, "b1", "t1", "claude:1", ledger)["label"], row["label"])
        self.assertEqual(usage_ledger.load(ledger)["claude:1"]["label"], row["label"])
        with self.assertRaises(KeyError):
            assignment.link(self.root, "b1", "unassigned", "claude:1", ledger)
        assignment.close(self.root, "b1")
        with self.assertRaises(ValueError):
            assignment.assign(self.root, "b1", "t2", "refactor again")


def transcript_rows(*items):
    return "".join(json.dumps(i) + "\n" for i in items)


def user(text="do it"):
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


def assistant(ident, **extra):
    return {"type": "assistant", "message": {"id": ident, "model": "claude-test", "usage": {"output_tokens": 1}}, **extra}


class LazyTests(TempCase):
    def setUp(self):
        super().setUp()
        self.transcript = self.dir / "t.jsonl"
        self.state = self.dir / "state.json"

    def write(self, text, mode="w"):
        with self.transcript.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def fire(self, threshold=3, session="s1"):
        payload = {"hook_event_name": "PostToolUse", "transcript_path": str(self.transcript), "session_id": session}
        return lazy_adopt.respond(payload, threshold, self.state)

    def test_reminds_once_per_turn_and_again_in_the_next_turn(self):
        self.write(transcript_rows(user(), assistant("m1"), tool_result(), assistant("m2")))
        self.assertEqual(self.fire(), {})
        self.write(transcript_rows(tool_result(), assistant("m3")), "a")
        first = self.fire()
        self.assertIn("3 model requests", first["hookSpecificOutput"]["additionalContext"])
        self.write(transcript_rows(tool_result(), assistant("m4")), "a")
        self.assertEqual(self.fire(), {})
        self.write(transcript_rows(user("next"), assistant("n1"), assistant("n2"), assistant("n3")), "a")
        self.assertIn("hookSpecificOutput", self.fire())

    def test_streamed_duplicates_sidechains_and_synthetic_rows_are_not_counted(self):
        self.write(transcript_rows(user(), assistant("m1"), assistant("m1"), assistant("m1"), assistant("side", isSidechain=True),
                                   {"type": "assistant", "message": {"id": "syn", "model": "<synthetic>", "usage": {"output_tokens": 0}}}))
        self.assertEqual(self.fire(threshold=2), {})

    def test_a_half_written_last_line_waits_for_the_next_call(self):
        self.write(transcript_rows(user(), assistant("m1"), assistant("m2")) + json.dumps(assistant("m3"))[:20])
        self.assertEqual(self.fire(), {})
        self.write(json.dumps(assistant("m3"))[20:] + "\n", "a")
        self.assertIn("hookSpecificOutput", self.fire())

    def test_a_truncated_transcript_resets_instead_of_crashing(self):
        self.write(transcript_rows(user(), assistant("m1"), assistant("m2"), assistant("m3")))
        self.fire()
        self.write(transcript_rows(user(), assistant("x1")))
        self.assertEqual(self.fire(), {})

    def test_sessions_are_independent_and_bad_payloads_are_ignored(self):
        self.write(transcript_rows(user(), assistant("m1"), assistant("m2"), assistant("m3")))
        self.assertIn("hookSpecificOutput", self.fire(session="a"))
        self.assertIn("hookSpecificOutput", self.fire(session="b"))
        for payload in ({}, {"hook_event_name": "Stop"}, {"hook_event_name": "PostToolUse"}, [], "x",
                        {"hook_event_name": "PostToolUse", "transcript_path": str(self.dir / "missing"), "session_id": "s"}):
            self.assertEqual(lazy_adopt.respond(payload, 1, self.state), {})

    def test_settings_and_stdin_main(self):
        command = lazy_adopt.settings(12)["hooks"]["PostToolUse"][0]["hooks"][0]
        self.assertIn("--threshold 12", command["command"])
        self.assertEqual(command["shell"], "powershell")
        sys.stdin = type("S", (), {"buffer": io.BytesIO(b"not json")})()
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                self.assertEqual(lazy_adopt.main([]), 0)
        finally:
            sys.stdin = sys.__stdin__
        self.assertEqual(json.loads(out.getvalue()), {})


if __name__ == "__main__":
    unittest.main()
