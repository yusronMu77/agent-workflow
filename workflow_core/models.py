"""Additional model workers (roadmap M6): OpenAI-compatible endpoints such as llama.cpp, Ollama or LM Studio.

A model drafts into task evidence and can back the route selector. It never writes the task output,
records reviews or transitions a task: the chat still verifies and owns acceptance.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from .project import Rejected, plain_path
from .records import checked_name, emit


def path(engine):
    return plain_path(engine.project.home / "models.json")


def registry(engine):
    return json.loads(path(engine).read_text(encoding="utf-8")) if path(engine).is_file() else {"models": {}}


def register(engine, name, base_url, model, api_key_env=None, timeout=120):
    checked_name(name, "model name")
    if not base_url.startswith(("http://", "https://")):
        raise Rejected("Base URL must be http(s)")
    if api_key_env and not api_key_env.replace("_", "").isalnum():
        raise Rejected("Give the name of an environment variable, never the key itself")
    data = registry(engine)
    data["models"][name] = {"base_url": base_url.rstrip("/"), "model": model, "api_key_env": api_key_env, "timeout": timeout}
    engine.write_owned(path(engine), (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    return {"status": "registered", "name": name, "base_url": base_url, "model": model,
            "api_key": "env:" + api_key_env if api_key_env else "none"}


def chat(engine, name, messages, max_tokens=1024):
    config = registry(engine)["models"].get(name)
    if config is None:
        raise Rejected("Model not registered")
    headers = {"Content-Type": "application/json"}
    if config.get("api_key_env"):
        key = os.environ.get(config["api_key_env"])
        if not key:
            raise Rejected(f"Environment variable {config['api_key_env']} is not set")
        headers["Authorization"] = "Bearer " + key
    body = json.dumps({"model": config["model"], "messages": messages, "max_tokens": max_tokens, "temperature": 0}).encode("utf-8")
    request = urllib.request.Request(config["base_url"] + "/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=config.get("timeout", 120)) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as error:
        raise Rejected(f"Model endpoint unavailable: {type(error).__name__}")
    try:
        text = answer["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise Rejected("Model response is not an OpenAI-compatible chat completion")
    return {"text": text, "usage": answer.get("usage")}


def draft(engine, task_id, name, max_tokens=2048):
    """Send the full-policy pack to the model and keep its reply as immutable task evidence."""
    pack = engine.pack(task_id, host="none")
    context = Path(pack["context_path"]).read_text(encoding="utf-8")
    reply = chat(engine, name, [
        {"role": "system", "content": "You draft for a supervising agent. Follow the pack; say what you could not verify."},
        {"role": "user", "content": context}], max_tokens)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved = engine.evidence(task_id, f"draft-{name}-{stamp}.md", reply["text"])
    emit(engine.store, task_id, "model_drafted", {"model": name, "evidence": saved, "usage": reply["usage"]})
    return {"status": "drafted", "model": name, "evidence": saved["ref"], "usage": reply["usage"],
            "authority": "draft_only_chat_verifies_and_writes_output"}
