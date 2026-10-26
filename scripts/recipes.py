"""K10: replay of mature requests. A verified task becomes a recipe; a deterministic runner replays it with zero model requests and hands off to the chat when anything deviates.

An optional internal LLM (any OpenAI-compatible endpoint, for example llama.cpp) may pick among retrieved recipes, fill parameters and draft new recipes.
Its output is always validated by this runner and never executed unreviewed."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import jsonl_store as store

EFFECTS = ("read", "project_write", "external_write")
STATUSES = ("draft", "candidate", "promoted", "demoted")
PARAM_TYPES = ("str", "int", "path", "enum")
GUARDS = ("file_exists", "file_sha256", "cmd_output")
POST = ("file_exists", "file_contains", "cmd_exit_zero")
PROMOTE_MIN_PASSES = 3
DEMOTE_STREAK = 2
MAX_TAIL = 2000
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,60}$")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
SECRET = re.compile(r"(api[_-]?key|token|secret|password|passwd|bearer)\s*[=:]\s*\S{8,}", re.I)
STOPWORDS = {"the", "and", "for", "with", "this", "that", "please", "tolong", "yang", "dan", "untuk", "dengan", "ini", "itu", "buat", "lagi"}
BUILTIN_PARAMS = ("python", "project")


def default_root():
    return store.home() / "recipes"


def recipe_path(root, name):
    return Path(root) / (name + ".json")


def load_recipe(root, name):
    path = recipe_path(root, name)
    if not path.is_file():
        raise KeyError("no such recipe: %s" % name)
    return json.loads(path.read_text(encoding="utf-8"))


def save_recipe(root, recipe):
    path = recipe_path(root, recipe["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe, ensure_ascii=False, indent=1), encoding="utf-8")


def all_recipes(root):
    out = []
    for path in sorted(Path(root).glob("*.json")) if Path(root).is_dir() else []:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def placeholders(value):
    return set(PLACEHOLDER.findall(value)) if isinstance(value, str) else set()


def strings_of(recipe):
    for step in recipe["steps"]:
        yield from step["argv"]
        yield from step.get("writes", [])
        if step.get("cwd"):
            yield step["cwd"]
    for item in recipe.get("guards", []) + recipe["postconditions"]:
        for key in ("path", "text", "sha256", "regex"):
            if isinstance(item.get(key), str):
                yield item[key]
        yield from item.get("argv", [])


def validate_recipe(recipe):
    """Structural checks; returns a list of problems. Whatever a model drafted must pass this before it is stored."""
    problems = []
    if not isinstance(recipe, dict):
        return ["recipe must be an object"]
    if not SLUG.match(str(recipe.get("id", ""))):
        problems.append("id must be a lowercase slug of 3-61 characters")
    if not str(recipe.get("description", "")).strip():
        problems.append("description is required")
    if not recipe.get("keywords") or not all(isinstance(k, str) and k.strip() for k in recipe["keywords"]):
        problems.append("keywords must be a nonempty list of strings")
    params = recipe.get("params")
    if not isinstance(params, dict):
        problems.append("params must be an object (may be empty)")
        params = {}
    for name, spec in params.items():
        if not re.match(r"^[a-z_][a-z0-9_]*$", name) or name in BUILTIN_PARAMS:
            problems.append("bad parameter name: %s" % name)
        if not isinstance(spec, dict) or spec.get("type") not in PARAM_TYPES:
            problems.append("parameter %s needs a type in %s" % (name, PARAM_TYPES))
        elif spec["type"] == "enum" and not spec.get("enum"):
            problems.append("enum parameter %s needs values" % name)
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        return problems + ["steps must be a nonempty list"]
    seen = set()
    for step in steps:
        label = step.get("id") if isinstance(step, dict) else "?"
        if not isinstance(step, dict) or not step.get("id") or step["id"] in seen:
            problems.append("every step needs a unique id")
            continue
        seen.add(step["id"])
        if not isinstance(step.get("argv"), list) or not step["argv"] or not all(isinstance(a, str) for a in step["argv"]):
            problems.append("step %s: argv must be a nonempty list of strings (no shell)" % label)
        if step.get("effect") not in EFFECTS:
            problems.append("step %s: effect must be one of %s" % (label, EFFECTS))
        if step.get("effect") == "external_write" and step.get("replay_safe") is not True:
            problems.append("step %s: external_write must declare replay_safe true to be replayable at all" % label)
        if int(step.get("timeout", 60) or 0) < 1 or int(step.get("timeout", 60)) > 600:
            problems.append("step %s: timeout must be 1-600 seconds" % label)
        if step.get("effect") == "project_write" and not step.get("writes"):
            problems.append("step %s: project_write must list the paths it writes (the handoff diff needs them)" % label)
    if not recipe.get("postconditions"):
        problems.append("postconditions are required: a replay without an independent verifier is never accepted")
    for field in ("postconditions", "guards"):
        items = recipe.get(field, [])
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            return problems + ["%s must be a list of objects" % field]
    for item in recipe.get("postconditions", []):
        if item.get("kind") not in POST:
            problems.append("postcondition kind must be one of %s" % (POST,))
        if item.get("kind") == "cmd_exit_zero" and not isinstance(item.get("argv"), list):
            problems.append("cmd_exit_zero needs argv")
    for item in recipe.get("guards", []):
        if item.get("kind") not in GUARDS:
            problems.append("guard kind must be one of %s" % (GUARDS,))
    used = set()
    try:
        for text in strings_of(recipe):
            used |= placeholders(text)
            if SECRET.search(text):
                problems.append("a literal secret-looking value is stored in the recipe; make it a parameter marked secret")
    except (KeyError, TypeError, AttributeError):
        problems.append("recipe has malformed steps, guards or postconditions")
    unknown = used - set(params) - set(BUILTIN_PARAMS)
    if unknown:
        problems.append("placeholders without a parameter: %s" % ", ".join(sorted(unknown)))
    return problems


def render(text, params):
    return PLACEHOLDER.sub(lambda m: str(params[m.group(1)]), text)


def check_param(name, spec, value, project):
    if spec["type"] == "int":
        return int(value)
    value = str(value)
    if len(value) > 500:
        raise ValueError("parameter %s is longer than 500 characters" % name)
    if spec["type"] == "enum" and value not in spec["enum"]:
        raise ValueError("parameter %s must be one of %s" % (name, spec["enum"]))
    if spec["type"] == "path":
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts or not value:
            raise ValueError("parameter %s must be a relative path inside the project" % name)
        if not (Path(project) / candidate).resolve().is_relative_to(Path(project).resolve()):
            raise ValueError("parameter %s escapes the project" % name)
    if spec.get("pattern") and not re.fullmatch(spec["pattern"], value):
        raise ValueError("parameter %s does not match its pattern" % name)
    return value


def resolve_params(recipe, supplied, request, project):
    """Explicit values win, then a from_request regex, then the default. Missing required values refuse the run."""
    values, missing = {}, []
    for name, spec in recipe["params"].items():
        raw = supplied.get(name)
        if raw is None and request and spec.get("from_request"):
            found = re.search(spec["from_request"], request)
            raw = (found.group(1) if found and found.groups() else found.group(0)) if found else None
        if raw is None:
            raw = spec.get("default")
        if raw is None:
            if spec.get("required", True):
                missing.append(name)
            continue
        values[name] = check_param(name, spec, raw, project)
    return values, missing


def redacted(recipe, params):
    return {k: ("<secret>" if recipe["params"][k].get("secret") else v) for k, v in params.items()}


def tail(text):
    return (text or "")[-MAX_TAIL:]


def file_sha(path):
    try:
        return store.digest(Path(path).read_bytes())
    except OSError:
        return None


def rendered_argv(argv, params, project):
    context = {**params, "python": sys.executable, "project": str(project)}
    return [render(a, context) for a in argv]


def run_command(argv, cwd, timeout):
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=False)
        return done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %d s" % timeout
    except OSError as exc:
        return 127, "", str(exc)


def check_guards(recipe, params, project):
    """Drift check before the first step: nothing has happened yet when a guard fails."""
    context = {**params, "python": sys.executable, "project": str(project)}
    for guard in recipe.get("guards", []):
        if guard["kind"] in ("file_exists", "file_sha256"):
            path = Path(project) / render(guard["path"], context)
            if not path.is_file():
                return "guard %s: %s is missing" % (guard["kind"], guard["path"])
            if guard["kind"] == "file_sha256" and file_sha(path) != guard["sha256"]:
                return "guard file_sha256: %s changed since the recipe was recorded" % guard["path"]
        else:
            code, out, _ = run_command(rendered_argv(guard["argv"], params, project), project, 30)
            if code != 0 or not re.search(guard["regex"], out):
                return "guard cmd_output: %s did not match %s" % (" ".join(guard["argv"][:2]), guard["regex"])
    return None


def check_post(recipe, params, project):
    context = {**params, "python": sys.executable, "project": str(project)}
    for item in recipe["postconditions"]:
        if item["kind"] == "cmd_exit_zero":
            code, _, err = run_command(rendered_argv(item["argv"], params, project), project, int(item.get("timeout", 60)))
            if code != 0:
                return "postcondition cmd_exit_zero failed (exit %d): %s" % (code, tail(err)[-300:])
            continue
        path = Path(project) / render(item["path"], context)
        if not path.is_file():
            return "postcondition %s: %s is missing" % (item["kind"], item["path"])
        if item["kind"] == "file_contains" and render(item["text"], context) not in path.read_text(encoding="utf-8", errors="replace"):
            return "postcondition file_contains: %s lacks the required text" % item["path"]
    return None


def journal_dir(root):
    return Path(root) / "journal"


def open_journals(root, recipe_id, params_hash):
    found = []
    for path in sorted(journal_dir(root).glob("*.jsonl")) if journal_dir(root).is_dir() else []:
        rows = store.rows(path)
        if rows and rows[0].get("recipe") == recipe_id and rows[0].get("params_hash") == params_hash \
                and not any(r["event"] == "finished" for r in rows):
            found.append({"run": path.stem, "last": rows[-1]})
    return found


def escalate(recipe, params, phase, ident, reason, done, remaining, changed, stderr=""):
    return {"outcome": "escalate", "recipe": recipe["id"], "params": redacted(recipe, params),
            "failed": {"phase": phase, "id": ident, "reason": reason, "stderr_tail": tail(stderr)},
            "steps_done": done, "steps_remaining": remaining, "changed_paths": changed,
            "instruction": "Continue natively from this state with the spec above, not a full trace. Do not rerun completed "
                           "non-idempotent steps; inspect the changed paths first."}


def update_stats(root, recipe, outcome, params_hash):
    stats = recipe.setdefault("stats", {"runs": 0, "passes": 0, "fail_streak": 0, "distinct_param_sets": []})
    stats["runs"] += 1
    if outcome == "accepted":
        stats["passes"] += 1
        stats["fail_streak"] = 0
        if params_hash not in stats["distinct_param_sets"]:
            stats["distinct_param_sets"].append(params_hash)
    elif outcome == "escalate":
        stats["fail_streak"] += 1
        if stats["fail_streak"] >= DEMOTE_STREAK and recipe["status"] in ("candidate", "promoted"):
            recipe["status"] = "demoted"
            recipe["demoted_reason"] = "%d consecutive failures: drift or poisoned recipe" % stats["fail_streak"]
    save_recipe(root, recipe)


def run(root, name, supplied, project, request=None, dry_run=False, confirm_external=False):
    recipe = load_recipe(root, name)
    project = Path(project).resolve()
    if recipe["status"] not in ("candidate", "promoted"):
        return {"outcome": "refused", "reason": "recipe is %s; only candidate (verification runs) and promoted recipes replay" % recipe["status"]}
    try:
        params, missing = resolve_params(recipe, supplied, request, project)
    except (ValueError, TypeError) as exc:
        return {"outcome": "refused", "reason": "parameter rejected: %s" % exc}
    if missing:
        return {"outcome": "refused", "reason": "missing parameters", "missing": missing}
    pset = store.digest(store.canonical(params))[:16]
    external = [s["id"] for s in recipe["steps"] if s["effect"] == "external_write"]
    if external and not confirm_external:
        return {"outcome": "refused", "reason": "external_write steps (%s) replay only with --confirm-external from the owner" % ", ".join(external)}
    pending = open_journals(root, recipe["id"], pset)
    if pending and not dry_run:
        return {"outcome": "refused", "reason": "an earlier replay with the same parameters never finished: inspect the project, then "
                "`abandon` it (needs_reconcile)", "unfinished": pending}
    plan = [{"step": s["id"], "effect": s["effect"],
             "argv": [("<secret>" if any(recipe["params"].get(p, {}).get("secret") for p in placeholders(template)) else shown)
                      for template, shown in zip(s["argv"], rendered_argv(s["argv"], params, project))]} for s in recipe["steps"]]
    if dry_run:
        return {"outcome": "dry_run", "recipe": recipe["id"], "params": redacted(recipe, params), "plan": plan, "model_requests": 0}
    started = time.time()
    run_id = "%s-%s-%s" % (recipe["id"], time.strftime("%Y%m%dT%H%M%S"), pset[:6])
    jpath = journal_dir(root) / (run_id + ".jsonl")
    store.append(jpath, {"event": "started", "recipe": recipe["id"], "params_hash": pset, "at": store.now_iso()})
    drift = check_guards(recipe, params, project)
    done, changed = [], []
    if drift:
        result = escalate(recipe, params, "guard", None, drift, done, [s["id"] for s in recipe["steps"]], changed)
        result["side_effects_started"] = False
    else:
        result = None
        before = {}
        for index, step in enumerate(recipe["steps"]):
            store.append(jpath, {"event": "step_started", "step": step["id"], "effect": step["effect"]})
            writes = [render(w, {**params, "project": str(project)}) for w in step.get("writes", [])]
            for w in writes:
                before.setdefault(w, file_sha(project / w))
            cwd = project / render(step["cwd"], {**params, "python": sys.executable, "project": str(project)}) if step.get("cwd") else project
            code, out, err = run_command(rendered_argv(step["argv"], params, project), cwd, int(step.get("timeout", 60)))
            want = int(step.get("expect", {}).get("exit_code", 0))
            ok = code == want and (not step.get("expect", {}).get("stdout_regex") or re.search(step["expect"]["stdout_regex"], out))
            store.append(jpath, {"event": "step_done", "step": step["id"], "exit": code, "ok": bool(ok)})
            changed = [{"path": w, "before": before[w], "after": file_sha(project / w)} for w in before if before[w] != file_sha(project / w)]
            if not ok:
                result = escalate(recipe, params, "step", step["id"], "exit %d (wanted %d) or stdout did not match" % (code, want), done,
                                  [s["id"] for s in recipe["steps"][index:]], changed, err or out)
                result["side_effects_started"] = True
                break
            done.append(step["id"])
        if result is None:
            failure = check_post(recipe, params, project)
            result = {"outcome": "accepted", "recipe": recipe["id"], "steps_done": done, "changed_paths": changed} if failure is None else \
                escalate(recipe, params, "postcondition", None, failure, done, [], changed)
            if failure:
                result["side_effects_started"] = True
    result.update(run_id=run_id, model_requests=0, seconds=round(time.time() - started, 2), params_hash=pset)
    store.append(jpath, {"event": "finished", "outcome": result["outcome"]})
    store.append(Path(root) / "runs.jsonl", {"run": run_id, "recipe": recipe["id"], "status_at_run": recipe["status"], "outcome": result["outcome"],
                                             "phase": (result.get("failed") or {}).get("phase"), "seconds": result["seconds"], "at": store.now_iso()})
    update_stats(root, load_recipe(root, name), result["outcome"], pset)
    return result


def canary(root, name, supplied, project):
    """Drift detector without side effects: guards plus the read-only postconditions. Two failures in a row demote the recipe."""
    recipe = load_recipe(root, name)
    try:
        params, missing = resolve_params(recipe, supplied, None, project)
    except (ValueError, TypeError) as exc:
        return {"outcome": "refused", "reason": "parameter rejected: %s" % exc}
    if missing:
        return {"outcome": "refused", "reason": "missing parameters", "missing": missing}
    reason = check_guards(recipe, params, Path(project).resolve())
    outcome = "escalate" if reason else "accepted"
    store.append(Path(root) / "runs.jsonl", {"run": "canary", "recipe": recipe["id"], "outcome": outcome, "phase": "canary", "at": store.now_iso()})
    update_stats(root, load_recipe(root, name), outcome, store.digest("canary")[:16])
    return {"outcome": outcome, "reason": reason, "recipe_status": load_recipe(root, name)["status"]}


def abandon(root, run_id, note):
    path = journal_dir(root) / (run_id + ".jsonl")
    if not path.is_file() or not note.strip():
        raise ValueError("unknown run or empty note")
    store.append(path, {"event": "finished", "outcome": "abandoned", "note": note})
    return {"status": "abandoned", "run": run_id}


def record(root, recipe, source_requests=None):
    problems = validate_recipe(recipe)
    if problems:
        raise ValueError("; ".join(problems))
    if recipe_path(root, recipe["id"]).exists():
        raise ValueError("recipe %s exists; record a new id or demote the old one" % recipe["id"])
    recipe = {**recipe, "status": "candidate", "created": store.now_iso(), "source_requests": source_requests,
              "stats": {"runs": 0, "passes": 0, "fail_streak": 0, "distinct_param_sets": []}}
    save_recipe(root, recipe)
    return {"status": "candidate", "id": recipe["id"], "next": "replay it %d times with different parameters; each accepted run counts toward promotion" % PROMOTE_MIN_PASSES}


def promote(root, name):
    recipe = load_recipe(root, name)
    reasons = []
    if recipe["status"] != "candidate":
        reasons.append("status is %s, not candidate" % recipe["status"])
    stats = recipe["stats"]
    if recipe["params"]:
        if len(stats["distinct_param_sets"]) < PROMOTE_MIN_PASSES:
            reasons.append("needs %d accepted replays with distinct parameter sets, has %d" % (PROMOTE_MIN_PASSES, len(stats["distinct_param_sets"])))
    elif stats["passes"] < PROMOTE_MIN_PASSES:  # no parameters means one parameter set: count accepted replays instead
        reasons.append("needs %d accepted replays, has %d" % (PROMOTE_MIN_PASSES, stats["passes"]))
    if stats["fail_streak"]:
        reasons.append("the latest replay failed")
    if any(s["effect"] == "external_write" for s in recipe["steps"]):
        reasons.append("recipes with external_write steps stay candidate: every replay needs the owner's --confirm-external")
    if validate_recipe(recipe):
        reasons.append("recipe no longer validates")
    if reasons:
        return {"status": "not_promoted", "reasons": reasons}
    recipe["status"] = "promoted"
    recipe["promoted_at"] = store.now_iso()
    save_recipe(root, recipe)
    return {"status": "promoted", "id": name}


def demote(root, name, note):
    recipe = load_recipe(root, name)
    recipe["status"] = "demoted"
    recipe["demoted_reason"] = note
    save_recipe(root, recipe)
    return {"status": "demoted", "id": name}


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9_\-]{3,}", text.lower()) if w not in STOPWORDS}


def match(root, request, top=3, min_score=0.5, min_margin=0.15):
    """Keyword retrieval first (cheap, no embedding): a recipe matches only when it is clearly ahead of the runner-up."""
    words, lowered, scored = tokens(request), request.lower(), []
    for recipe in all_recipes(root):
        if recipe["status"] not in ("candidate", "promoted"):
            continue
        hits = sum(1 for k in recipe["keywords"] if (k.lower() in lowered if " " in k else k.lower() in words))
        scored.append({"id": recipe["id"], "status": recipe["status"], "score": round(hits / len(recipe["keywords"]), 3), "hits": hits})
    scored.sort(key=lambda r: (-r["score"], r["id"]))
    scored = scored[:top]
    clear = bool(scored) and scored[0]["score"] >= min_score and (len(scored) < 2 or scored[0]["score"] - scored[1]["score"] >= min_margin)
    return {"matched": scored[0]["id"] if clear else None, "candidates": scored,
            "reason": "clear keyword match" if clear else "no recipe is clearly ahead: stay native"}


def llm_json(endpoint, model, system, user, schema, name, timeout=60):
    body = {"model": model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
    request = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        reply = json.loads(response.read().decode("utf-8"))
    return json.loads(reply["choices"][0]["message"]["content"])


def llm_select(root, endpoint, model, request, project):
    """The internal LLM chooses only among retrieved candidates and fills parameters. The runner validates everything it returns."""
    found = match(root, request, top=5, min_score=0.0, min_margin=0.0)["candidates"]
    if not found:
        return {"matched": None, "reason": "no recipe to choose from", "llm_requests": 0}
    recipes = {r["id"]: r for r in all_recipes(root)}
    ids = [c["id"] for c in found] + ["none"]
    schema = {"type": "object", "additionalProperties": False, "required": ["recipe", "params"],
              "properties": {"recipe": {"type": "string", "enum": ids}, "params": {"type": "object", "additionalProperties": {"type": "string"}}}}
    menu = "\n".join("- %s: %s | params: %s" % (i, recipes[i]["description"], ", ".join(recipes[i]["params"]) or "none") for i in ids[:-1])
    try:
        reply = llm_json(endpoint, model, "Choose the recipe that exactly fits the request, or none. Fill only the listed parameters from the request text. "
                         "Answer JSON only.", "Request: %s\nRecipes:\n%s" % (request, menu), schema, "pick")
    except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        return {"matched": None, "reason": "llm unavailable or invalid reply: %s" % exc, "llm_requests": 1}
    chosen = reply.get("recipe")
    if chosen not in recipes or chosen == "none" or chosen not in [c["id"] for c in found]:
        return {"matched": None, "reason": "llm chose none or an unretrieved recipe", "llm_requests": 1}
    try:
        params, missing = resolve_params(recipes[chosen], reply.get("params") or {}, request, project)
    except (ValueError, TypeError) as exc:
        return {"matched": None, "reason": "llm parameters rejected: %s" % exc, "llm_requests": 1}
    return {"matched": chosen, "params": {k: v for k, v in params.items()}, "missing": missing, "llm_requests": 1,
            "reason": "llm chose among retrieved candidates; parameters validated by the runner"}


def draft(root, endpoint, model, request, tools):
    """An internal LLM drafts a recipe. It is stored as draft and cannot replay until the owner reviews it and it passes verification runs."""
    step = {"type": "object", "additionalProperties": False, "required": ["id", "argv", "effect"],
            "properties": {"id": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}},
                           "effect": {"type": "string", "enum": list(EFFECTS)}, "writes": {"type": "array", "items": {"type": "string"}}}}
    post = {"type": "object", "additionalProperties": False, "required": ["kind"],
            "properties": {"kind": {"type": "string", "enum": list(POST)}, "path": {"type": "string"}, "text": {"type": "string"},
                           "argv": {"type": "array", "items": {"type": "string"}}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["id", "description", "keywords", "params", "steps", "postconditions"],
              "properties": {"id": {"type": "string"}, "description": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
                             "params": {"type": "object", "additionalProperties": {"type": "object"}},
                             "steps": {"type": "array", "items": step}, "postconditions": {"type": "array", "items": post}}}
    system = ("Write a replayable recipe as JSON. Use only the listed tools as argv lists, never a shell string. Use {name} placeholders for every value that varies "
              "and declare them in params with a type (str, int, path, enum). Classify each step honestly: read, project_write (list the writes), or external_write. "
              "Include at least one postcondition that checks the result independently.")
    reply = llm_json(endpoint, model, system, "Request: %s\nTools:\n%s" % (request, tools), schema, "recipe")
    problems = validate_recipe(reply)
    if problems:
        return {"status": "rejected", "problems": problems}
    reply.update(status="draft", created=store.now_iso(), stats={"runs": 0, "passes": 0, "fail_streak": 0, "distinct_param_sets": []})
    if recipe_path(root, reply["id"]).exists():
        return {"status": "rejected", "problems": ["recipe %s exists" % reply["id"]]}
    save_recipe(root, reply)
    return {"status": "draft", "id": reply["id"], "next": "owner or agent reviews it (`review`), then dry-run and verification replays"}


def review(root, name, note):
    recipe = load_recipe(root, name)
    if recipe["status"] != "draft" or not note.strip():
        raise ValueError("only a draft with a nonempty review note can move to candidate")
    recipe["status"] = "candidate"
    recipe["reviewed"] = {"note": note, "at": store.now_iso()}
    save_recipe(root, recipe)
    return {"status": "candidate", "id": name}


def report(root, handoff_requests=1.0):
    runs = store.rows(Path(root) / "runs.jsonl")
    out = {"schema": "recipe-report/1", "recipes": {}, "estimate_note": "estimates in model requests; baseline comes from the recorded source_requests"}
    saved = 0.0
    for recipe in all_recipes(root):
        mine = [r for r in runs if r["recipe"] == recipe["id"] and r["run"] != "canary"]
        ok, esc = sum(r["outcome"] == "accepted" for r in mine), sum(r["outcome"] == "escalate" for r in mine)
        baseline = recipe.get("source_requests")
        a = ok / (ok + esc) if ok + esc else None
        need = (handoff_requests / (baseline + handoff_requests)) if baseline else None  # break-even with c_s=c_v=0: a > h / (c_L + h)
        net = ok * baseline - esc * handoff_requests if baseline else None
        if net:
            saved += net
        out["recipes"][recipe["id"]] = {"status": recipe["status"], "accepted": ok, "escalated": esc, "accept_rate": round(a, 3) if a is not None else None,
                                        "break_even_accept_rate": round(need, 3) if need is not None else None,
                                        "net_requests_saved_estimate": net, "fail_streak": recipe["stats"]["fail_streak"]}
    out["net_requests_saved_estimate_total"] = saved
    return out


def parse_params(pairs):
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError("--param needs name=value")
        out[key] = value
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--project", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record")
    rec.add_argument("recipe_file", type=Path)
    rec.add_argument("--source-requests", type=int, help="Model requests the native run took (from the ledger): the saving baseline")
    mat = commands.add_parser("match")
    mat.add_argument("request")
    mat.add_argument("--llm-endpoint")
    mat.add_argument("--llm-model", default="local")
    runner = commands.add_parser("run")
    runner.add_argument("name")
    runner.add_argument("--param", action="append")
    runner.add_argument("--request")
    runner.add_argument("--dry-run", action="store_true")
    runner.add_argument("--confirm-external", action="store_true")
    can = commands.add_parser("canary")
    can.add_argument("name")
    can.add_argument("--param", action="append")
    for name in ("promote", "show"):
        commands.add_parser(name).add_argument("name")
    for name in ("demote", "review"):
        item = commands.add_parser(name)
        item.add_argument("name")
        item.add_argument("--note", required=True)
    ab = commands.add_parser("abandon")
    ab.add_argument("run_id")
    ab.add_argument("--note", required=True)
    dr = commands.add_parser("draft")
    dr.add_argument("request")
    dr.add_argument("--llm-endpoint", required=True)
    dr.add_argument("--llm-model", default="local")
    dr.add_argument("--tools", required=True, help="Text file listing the tools and commands the draft may use")
    commands.add_parser("list")
    rp = commands.add_parser("report")
    rp.add_argument("--handoff-requests", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            result = record(args.root, json.loads(args.recipe_file.read_text(encoding="utf-8")), args.source_requests)
        elif args.command == "match":
            result = match(args.root, args.request)
            if args.llm_endpoint:
                result["llm"] = llm_select(args.root, args.llm_endpoint, args.llm_model, args.request, args.project)
        elif args.command == "run":
            result = run(args.root, args.name, parse_params(args.param), args.project, args.request, args.dry_run, args.confirm_external)
        elif args.command == "canary":
            result = canary(args.root, args.name, parse_params(args.param), args.project)
        elif args.command == "promote":
            result = promote(args.root, args.name)
        elif args.command == "demote":
            result = demote(args.root, args.name, args.note)
        elif args.command == "review":
            result = review(args.root, args.name, args.note)
        elif args.command == "abandon":
            result = abandon(args.root, args.run_id, args.note)
        elif args.command == "draft":
            result = draft(args.root, args.llm_endpoint, args.llm_model, args.request, args.tools and Path(args.tools).read_text(encoding="utf-8"))
        elif args.command == "show":
            result = load_recipe(args.root, args.name)
        elif args.command == "report":
            result = report(args.root, args.handoff_requests)
        else:
            result = {"recipes": [{"id": r["id"], "status": r["status"], "keywords": r["keywords"], "stats": r["stats"]} for r in all_recipes(args.root)]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if result.get("outcome") == "escalate" else 0


if __name__ == "__main__":
    sys.exit(main())
