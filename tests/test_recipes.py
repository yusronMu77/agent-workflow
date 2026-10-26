"""A recipe replays with zero model requests, refuses or escalates on any deviation, and an unverified draft never replays."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recipes

PY = "{python}"


def write_step(code, **extra):
    return {"id": "write", "effect": "project_write", "writes": ["out/{name}.txt"],
            "argv": [PY, "-c", code, "{name}"], **extra}


WRITE_CODE = "import sys,pathlib; p=pathlib.Path('out'); p.mkdir(exist_ok=True); (p/(sys.argv[1]+'.txt')).write_text('hello '+sys.argv[1])"


def base(**over):
    recipe = {"id": "make-greeting", "description": "write a greeting file", "keywords": ["greeting", "hello", "write"],
              "params": {"name": {"type": "str", "pattern": "[a-z]+", "from_request": r"for ([a-z]+)"}},
              "steps": [write_step(WRITE_CODE)],
              "postconditions": [{"kind": "file_contains", "path": "out/{name}.txt", "text": "hello {name}"}]}
    recipe.update(over)
    return recipe


class RecipeBase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="recipes-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "recipes"
        self.project = Path(temp.name) / "project"
        self.project.mkdir()

    def record(self, recipe=None, **kwargs):
        return recipes.record(self.root, recipe or base(), **kwargs)

    def run_recipe(self, name="make-greeting", params=None, **kwargs):
        return recipes.run(self.root, name, params or {"name": "alice"}, self.project, **kwargs)


class ValidationTests(unittest.TestCase):
    def test_a_good_recipe_passes(self):
        self.assertEqual(recipes.validate_recipe(base()), [])

    def test_missing_postconditions_are_refused(self):
        self.assertTrue(any("postconditions" in p for p in recipes.validate_recipe(base(postconditions=[]))))

    def test_shell_strings_and_unknown_effects_are_refused(self):
        bad = base(steps=[{"id": "s", "effect": "magic", "argv": "echo hi"}])
        problems = " ".join(recipes.validate_recipe(bad))
        self.assertIn("argv", problems)
        self.assertIn("effect", problems)

    def test_project_write_must_list_writes(self):
        step = write_step(WRITE_CODE)
        step.pop("writes")
        self.assertTrue(any("writes" in p for p in recipes.validate_recipe(base(steps=[step]))))

    def test_external_write_needs_replay_safe(self):
        step = {"id": "send", "effect": "external_write", "argv": ["curl", "x"]}
        self.assertTrue(any("replay_safe" in p for p in recipes.validate_recipe(base(steps=[step]))))

    def test_placeholder_without_parameter_is_refused(self):
        bad = base(steps=[{"id": "s", "effect": "read", "argv": ["echo", "{ghost}"]}])
        self.assertTrue(any("ghost" in p for p in recipes.validate_recipe(bad)))

    def test_literal_secret_is_refused(self):
        bad = base(steps=[{"id": "s", "effect": "read", "argv": ["tool", "--api_key=abcdef123456789"]}])
        self.assertTrue(any("secret" in p for p in recipes.validate_recipe(bad)))

    def test_builtin_parameter_names_are_reserved(self):
        bad = base(params={"python": {"type": "str"}})
        self.assertTrue(any("bad parameter name" in p for p in recipes.validate_recipe(bad)))

    def test_malformed_structures_do_not_crash(self):
        self.assertTrue(recipes.validate_recipe("nope"))
        self.assertTrue(recipes.validate_recipe(base(steps=["x"])))
        self.assertTrue(recipes.validate_recipe(base(postconditions=["x"])))


class ParameterTests(RecipeBase):
    def test_path_parameters_cannot_escape_the_project(self):
        spec = {"type": "path"}
        for value in ("../x", "/abs/x", "a/../../x", ""):
            with self.assertRaises(ValueError, msg=value):
                recipes.check_param("p", spec, value, self.project)
        self.assertEqual(recipes.check_param("p", spec, "a/b.txt", self.project), "a/b.txt")

    def test_enum_pattern_and_length_are_enforced(self):
        with self.assertRaises(ValueError):
            recipes.check_param("e", {"type": "enum", "enum": ["a", "b"]}, "c", self.project)
        with self.assertRaises(ValueError):
            recipes.check_param("s", {"type": "str", "pattern": "[a-z]+"}, "A1", self.project)
        with self.assertRaises(ValueError):
            recipes.check_param("s", {"type": "str"}, "x" * 501, self.project)

    def test_request_regex_default_and_missing(self):
        recipe = base(params={"name": {"type": "str", "from_request": r"for (\w+)"}, "mode": {"type": "enum", "enum": ["a", "b"], "default": "a"},
                              "need": {"type": "str"}})
        values, missing = recipes.resolve_params(recipe, {}, "greet for bob", self.project)
        self.assertEqual(values, {"name": "bob", "mode": "a"})
        self.assertEqual(missing, ["need"])

    def test_invalid_value_refuses_the_run_instead_of_crashing(self):
        self.record()
        result = self.run_recipe(params={"name": "ALICE"})
        self.assertEqual(result["outcome"], "refused")
        self.assertFalse((self.project / "out").exists())


class ReplayTests(RecipeBase):
    def test_accepted_replay_uses_zero_model_requests_and_reports_changes(self):
        self.record(source_requests=12)
        result = self.run_recipe()
        self.assertEqual(result["outcome"], "accepted")
        self.assertEqual(result["model_requests"], 0)
        self.assertEqual([c["path"] for c in result["changed_paths"]], ["out/alice.txt"])
        self.assertEqual((self.project / "out" / "alice.txt").read_text(), "hello alice")

    def test_dry_run_changes_nothing_and_hides_secrets(self):
        recipe = base(params={"name": {"type": "str", "secret": True, "pattern": "[a-z]+"}})
        self.record(recipe)
        result = self.run_recipe(dry_run=True)
        self.assertEqual(result["outcome"], "dry_run")
        self.assertNotIn("alice", json.dumps(result))
        self.assertFalse((self.project / "out").exists())

    def test_a_draft_or_demoted_recipe_never_replays(self):
        recipes.save_recipe(self.root, {**base(), "status": "draft", "stats": {}})
        self.assertEqual(self.run_recipe()["outcome"], "refused")

    def test_failed_step_escalates_with_spec_not_trace(self):
        failing = base(steps=[write_step("import sys; sys.stderr.write('boom'); sys.exit(3)")])
        self.record(failing)
        result = self.run_recipe()
        self.assertEqual(result["outcome"], "escalate")
        self.assertEqual(result["failed"]["phase"], "step")
        self.assertIn("boom", result["failed"]["stderr_tail"])
        self.assertTrue(result["side_effects_started"])
        self.assertEqual(result["steps_remaining"], ["write"])

    def test_postcondition_failure_is_an_escalation_not_a_success(self):
        wrong = base(postconditions=[{"kind": "file_contains", "path": "out/{name}.txt", "text": "something else"}])
        self.record(wrong)
        result = self.run_recipe()
        self.assertEqual(result["outcome"], "escalate")
        self.assertEqual(result["failed"]["phase"], "postcondition")

    def test_guard_drift_stops_before_any_step_runs(self):
        guarded = base(guards=[{"kind": "file_exists", "path": "marker.txt"}])
        self.record(guarded)
        result = self.run_recipe()
        self.assertEqual(result["outcome"], "escalate")
        self.assertEqual(result["failed"]["phase"], "guard")
        self.assertFalse(result["side_effects_started"])
        self.assertFalse((self.project / "out").exists())

    def test_sha_guard_detects_changed_input(self):
        (self.project / "spec.txt").write_text("v1")
        guarded = base(guards=[{"kind": "file_sha256", "path": "spec.txt", "sha256": recipes.store.digest(b"v1")}])
        self.record(guarded)
        self.assertEqual(self.run_recipe()["outcome"], "accepted")
        (self.project / "spec.txt").write_text("v2")
        self.assertEqual(self.run_recipe(params={"name": "bob"})["failed"]["phase"], "guard")

    def test_stdout_expectation_is_enforced(self):
        step = {"id": "say", "effect": "read", "argv": [PY, "-c", "print('ready')"], "expect": {"stdout_regex": "done"}}
        self.record(base(steps=[step], postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}], params={}))
        self.assertEqual(self.run_recipe(params={})["outcome"], "escalate")

    def test_timeout_is_a_failed_step(self):
        step = {"id": "slow", "effect": "read", "argv": [PY, "-c", "import time; time.sleep(5)"], "timeout": 1}
        self.record(base(steps=[step], postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}], params={}))
        result = self.run_recipe(params={})
        self.assertEqual(result["outcome"], "escalate")
        self.assertIn("timeout", result["failed"]["stderr_tail"])

    def test_missing_executable_is_a_failed_step_not_a_crash(self):
        step = {"id": "nope", "effect": "read", "argv": ["definitely-not-a-real-binary-xyz"]}
        self.record(base(steps=[step], postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}], params={}))
        self.assertEqual(self.run_recipe(params={})["outcome"], "escalate")

    def test_external_write_needs_the_owner_flag(self):
        step = {"id": "send", "effect": "external_write", "replay_safe": True, "argv": [PY, "-c", "pass"]}
        self.record(base(steps=[step], postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}], params={}))
        self.assertEqual(self.run_recipe(params={})["outcome"], "refused")
        self.assertEqual(self.run_recipe(params={}, confirm_external=True)["outcome"], "accepted")

    def test_an_unfinished_journal_blocks_the_same_parameters_until_abandoned(self):
        self.record()
        journal = recipes.journal_dir(self.root)
        journal.mkdir(parents=True)
        pset = recipes.store.digest(recipes.store.canonical({"name": "alice"}))[:16]
        recipes.store.append(journal / "crashed.jsonl", {"event": "started", "recipe": "make-greeting", "params_hash": pset})
        self.assertEqual(self.run_recipe()["outcome"], "refused")
        self.assertEqual(self.run_recipe(params={"name": "bob"})["outcome"], "accepted")
        recipes.abandon(self.root, "crashed", "inspected, nothing half-written")
        self.assertEqual(self.run_recipe()["outcome"], "accepted")
        with self.assertRaises(ValueError):
            recipes.abandon(self.root, "crashed", " ")

    def test_cwd_is_rendered_inside_the_project(self):
        (self.project / "sub").mkdir()
        step = {"id": "here", "effect": "read", "cwd": "sub", "argv": [PY, "-c", "import os,sys; sys.exit(0 if os.path.basename(os.getcwd())=='sub' else 1)"]}
        self.record(base(steps=[step], postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}], params={}))
        self.assertEqual(self.run_recipe(params={})["outcome"], "accepted")


class LifecycleTests(RecipeBase):
    def test_recording_is_candidate_and_never_overwrites(self):
        self.assertEqual(self.record()["status"], "candidate")
        with self.assertRaises(ValueError):
            self.record()
        with self.assertRaises(ValueError):
            recipes.record(self.root, base(id="x"))

    def test_promotion_needs_three_distinct_parameter_sets(self):
        self.record()
        for name in ("alice", "alice", "bob"):
            self.run_recipe(params={"name": name})
        self.assertEqual(recipes.promote(self.root, "make-greeting")["status"], "not_promoted")
        self.run_recipe(params={"name": "carol"})
        self.assertEqual(recipes.promote(self.root, "make-greeting")["status"], "promoted")

    def test_a_recipe_without_parameters_can_still_be_promoted_by_repeated_passes(self):
        step = {"id": "ok", "effect": "read", "argv": [PY, "-c", "pass"]}
        self.record(base(steps=[step], params={}, postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}]))
        for _ in range(2):
            self.run_recipe(params={})
        self.assertEqual(recipes.promote(self.root, "make-greeting")["status"], "not_promoted")
        self.run_recipe(params={})
        self.assertEqual(recipes.promote(self.root, "make-greeting")["status"], "promoted")

    def test_two_failures_in_a_row_demote_and_one_does_not(self):
        self.record(base(guards=[{"kind": "file_exists", "path": "marker.txt"}]))
        self.run_recipe()
        self.assertEqual(recipes.load_recipe(self.root, "make-greeting")["status"], "candidate")
        self.run_recipe()
        recipe = recipes.load_recipe(self.root, "make-greeting")
        self.assertEqual(recipe["status"], "demoted")
        self.assertIn("consecutive", recipe["demoted_reason"])
        self.assertEqual(self.run_recipe()["outcome"], "refused")

    def test_a_pass_resets_the_failure_streak(self):
        self.record(base(guards=[{"kind": "file_exists", "path": "marker.txt"}]))
        self.run_recipe()
        (self.project / "marker.txt").write_text("x")
        self.run_recipe(params={"name": "bob"})
        (self.project / "marker.txt").unlink()
        self.run_recipe(params={"name": "carol"})
        self.assertEqual(recipes.load_recipe(self.root, "make-greeting")["status"], "candidate")

    def test_external_write_recipes_are_never_promoted(self):
        step = {"id": "send", "effect": "external_write", "replay_safe": True, "argv": [PY, "-c", "pass"]}
        self.record(base(steps=[step], postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}], params={"n": {"type": "int"}}))
        for n in (1, 2, 3):
            self.run_recipe(params={"n": n}, confirm_external=True)
        self.assertEqual(recipes.promote(self.root, "make-greeting")["status"], "not_promoted")

    def test_canary_demotes_after_two_drift_failures_without_side_effects(self):
        self.record(base(guards=[{"kind": "file_exists", "path": "marker.txt"}]))
        recipes.canary(self.root, "make-greeting", {"name": "alice"}, self.project)
        result = recipes.canary(self.root, "make-greeting", {"name": "alice"}, self.project)
        self.assertEqual(result["recipe_status"], "demoted")
        self.assertFalse((self.project / "out").exists())

    def test_draft_needs_review_before_it_can_replay(self):
        recipes.save_recipe(self.root, {**base(), "status": "draft", "stats": {"runs": 0, "passes": 0, "fail_streak": 0, "distinct_param_sets": []}})
        with self.assertRaises(ValueError):
            recipes.review(self.root, "make-greeting", " ")
        recipes.review(self.root, "make-greeting", "argv and postcondition read and sensible")
        self.assertEqual(self.run_recipe()["outcome"], "accepted")
        with self.assertRaises(ValueError):
            recipes.review(self.root, "make-greeting", "again")

    def test_report_uses_the_recorded_baseline_and_break_even(self):
        self.record(source_requests=9)
        self.run_recipe(params={"name": "alice"})
        self.run_recipe(params={"name": "bob"})
        info = recipes.report(self.root, handoff_requests=1.0)["recipes"]["make-greeting"]
        self.assertEqual((info["accepted"], info["escalated"]), (2, 0))
        self.assertEqual(info["net_requests_saved_estimate"], 18)
        self.assertEqual(info["break_even_accept_rate"], 0.1)


class MatchTests(RecipeBase):
    def put(self, name, keywords, status="candidate"):
        recipes.save_recipe(self.root, {**base(id=name, keywords=keywords), "status": status, "stats": {}})

    def test_clear_winner_matches_and_ties_stay_native(self):
        self.put("make-greeting", ["greeting", "hello"])
        self.put("make-report", ["report", "weekly"])
        self.assertEqual(recipes.match(self.root, "tolong write a hello greeting")["matched"], "make-greeting")
        self.put("make-poem", ["greeting", "hello"])
        self.assertIsNone(recipes.match(self.root, "write a hello greeting")["matched"])

    def test_no_keyword_hit_and_inactive_recipes_do_not_match(self):
        self.put("make-greeting", ["greeting"], status="demoted")
        self.assertIsNone(recipes.match(self.root, "write a greeting")["matched"])
        self.put("make-report", ["report"])
        self.assertIsNone(recipes.match(self.root, "something unrelated")["matched"])

    def test_multiword_keywords_match_as_phrases(self):
        self.put("make-greeting", ["hello world", "greeting"])
        self.assertEqual(recipes.match(self.root, "print hello world greeting")["matched"], "make-greeting")


class LlmTests(RecipeBase):
    def setUp(self):
        super().setUp()
        self.record()
        report_step = {"id": "check", "effect": "read", "argv": [PY, "-c", "pass"]}
        self.record(base(id="make-report", description="weekly report", keywords=["report", "weekly"], params={}, steps=[report_step],
                         postconditions=[{"kind": "cmd_exit_zero", "argv": [PY, "-c", "pass"]}]), source_requests=5)

    def pick(self, reply):
        with mock.patch.object(recipes, "llm_json", return_value=reply):
            return recipes.llm_select(self.root, "http://x/v1", "m", "write a greeting for dave", self.project)

    def test_valid_choice_has_validated_parameters(self):
        result = self.pick({"recipe": "make-greeting", "params": {"name": "dave"}})
        self.assertEqual(result["matched"], "make-greeting")
        self.assertEqual(result["params"], {"name": "dave"})

    def test_hallucinated_or_none_choice_is_ignored(self):
        self.assertIsNone(self.pick({"recipe": "delete-everything", "params": {}})["matched"])
        self.assertIsNone(self.pick({"recipe": "none", "params": {}})["matched"])

    def test_parameters_that_break_the_contract_reject_the_match(self):
        result = self.pick({"recipe": "make-greeting", "params": {"name": "NOT VALID"}})
        self.assertIsNone(result["matched"])
        self.assertIn("rejected", result["reason"])

    def test_endpoint_failure_means_native_not_a_crash(self):
        with mock.patch.object(recipes, "llm_json", side_effect=OSError("refused")):
            result = recipes.llm_select(self.root, "http://x/v1", "m", "write a greeting", self.project)
        self.assertIsNone(result["matched"])

    def test_choice_outside_the_retrieved_candidates_is_ignored(self):
        recipes.save_recipe(self.root, {**base(id="make-poem", keywords=["poem"]), "status": "candidate", "stats": {}})
        with mock.patch.object(recipes, "match", return_value={"candidates": [{"id": "make-greeting"}]}):
            with mock.patch.object(recipes, "llm_json", return_value={"recipe": "make-poem", "params": {}}):
                result = recipes.llm_select(self.root, "http://x/v1", "m", "poem", self.project)
        self.assertIsNone(result["matched"])

    def test_no_recipes_means_no_llm_request(self):
        empty = self.root.parent / "empty"
        with mock.patch.object(recipes, "llm_json") as call:
            result = recipes.llm_select(empty, "http://x/v1", "m", "anything", self.project)
        call.assert_not_called()
        self.assertEqual(result["llm_requests"], 0)

    def test_draft_is_stored_unusable_and_invalid_drafts_are_rejected(self):
        good = {**base(id="make-farewell"), "keywords": ["farewell"]}
        with mock.patch.object(recipes, "llm_json", return_value=good):
            result = recipes.draft(self.root, "http://x/v1", "m", "write farewell", "tools")
        self.assertEqual(result["status"], "draft")
        self.assertEqual(recipes.run(self.root, "make-farewell", {"name": "x"}, self.project)["outcome"], "refused")
        with mock.patch.object(recipes, "llm_json", return_value={**good, "id": "make-other", "postconditions": []}):
            self.assertEqual(recipes.draft(self.root, "http://x/v1", "m", "r", "t")["status"], "rejected")
        with mock.patch.object(recipes, "llm_json", return_value=good):
            self.assertEqual(recipes.draft(self.root, "http://x/v1", "m", "r", "t")["status"], "rejected")

    def test_llm_request_body_asks_for_a_json_schema(self):
        seen = {}

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode()

        def fake(request, timeout):
            seen["url"], seen["body"] = request.full_url, json.loads(request.data)
            return Reply()

        with mock.patch.object(recipes.urllib.request, "urlopen", fake):
            out = recipes.llm_json("http://localhost:8080/v1/", "m", "s", "u", {"type": "object"}, "n")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(seen["url"], "http://localhost:8080/v1/chat/completions")
        self.assertEqual(seen["body"]["response_format"]["type"], "json_schema")
        self.assertEqual(seen["body"]["temperature"], 0)


class CliTests(RecipeBase):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = recipes.main(["--root", str(self.root), "--project", str(self.project), *args])
        return code, out.getvalue(), err.getvalue()

    def test_record_run_and_exit_codes(self):
        file = self.root.parent / "r.json"
        file.write_text(json.dumps(base()), encoding="utf-8")
        self.assertEqual(self.cli("record", str(file), "--source-requests", "7")[0], 0)
        code, out, _ = self.cli("run", "make-greeting", "--param", "name=zed")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["outcome"], "accepted")
        (self.project / "out" / "zed.txt").unlink()
        code, out, _ = self.cli("run", "make-greeting", "--request", "greeting for zed", "--dry-run")
        self.assertEqual((code, json.loads(out)["outcome"]), (0, "dry_run"))
        self.assertEqual(self.cli("run", "no-such-recipe")[0], 2)
        self.assertEqual(self.cli("run", "make-greeting", "--param", "broken")[0], 2)

    def test_escalation_exits_three(self):
        file = self.root.parent / "r.json"
        file.write_text(json.dumps(base(guards=[{"kind": "file_exists", "path": "marker.txt"}])), encoding="utf-8")
        self.cli("record", str(file))
        self.assertEqual(self.cli("run", "make-greeting", "--param", "name=zed")[0], 3)


if __name__ == "__main__":
    unittest.main()
