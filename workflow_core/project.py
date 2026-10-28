import hashlib
import json
import os
from pathlib import Path
import re
import uuid


class Rejected(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def jsonc(path):
    text = path.read_text(encoding="utf-8-sig")
    text = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else '', text)
    return json.loads(re.sub(r'"(?:\\.|[^"\\])*"|,\s*([}\]])',
                             lambda m: m[0] if m[0].startswith('"') else m[1], text))


def plain_path(path):
    path = Path(os.path.abspath(path))
    if path.resolve() != path:
        raise Rejected("Managed state path is redirected")
    return path


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.home = plain_path(self.root / ".agent-workflow")
        path = plain_path(self.home / "project.json")
        self.config = json.loads(path.read_text(encoding="utf-8"))
        if self.config.get("schema") != 1 or not self.config.get("project_id"):
            raise Rejected("Unsupported project manifest")
        for key in ("pack_bytes", "source_bytes", "minutes"):
            if type(self.config.get("budget", {}).get(key)) is not int or self.config["budget"][key] <= 0:
                raise Rejected("Invalid project budget")
        if type(self.config["budget"].get("repairs")) is not int or self.config["budget"]["repairs"] < 0:
            raise Rejected("Invalid repair limit")
        self.config_hash = digest(path.read_bytes())
        self.roots = {k: (self.root / v).resolve() for k, v in self.config["roots"].items()}
        if self.roots.get("project") != self.root or any(not p.is_dir() for p in self.roots.values()):
            raise Rejected("Invalid or missing registered root")
        self.db = plain_path(self.home / "state.sqlite")

    @classmethod
    def init(cls, root, roots=(), workspace=None):
        root = Path(root).resolve()
        if not root.is_dir():
            raise Rejected("Project directory must exist")
        home = plain_path(root / ".agent-workflow")
        home.mkdir(exist_ok=True)
        path = home / "project.json"
        if path.exists():
            return cls(root), False
        if any(home.iterdir()):
            raise Rejected("Unowned or legacy state exists; preserve it and use explicit migration")
        mappings = {"project": "."}
        if workspace:
            workspace = Path(workspace).resolve()
            for index, item in enumerate(jsonc(workspace).get("folders", [])):
                target = (workspace.parent / item["path"]).resolve()
                if target == root:
                    continue
                key = re.sub(r"[^a-z0-9_-]", "-", item.get("name", f"folder-{index}").lower())
                key = key if key and key[0].isalpha() else "root-" + key
                if key in mappings:
                    raise Rejected("Duplicate workspace root alias")
                mappings[key] = os.path.relpath(target, root)
        for value in roots:
            key, sep, relative = value.partition("=")
            if not sep or not re.fullmatch(r"[a-z][a-z0-9_-]*", key) or key in mappings:
                raise Rejected("Use a unique --root alias=relative/path")
            if Path(relative).is_absolute():
                raise Rejected("Portable root bindings must be relative")
            mappings[key] = relative
        if any(not (root / value).resolve().is_dir() for value in mappings.values()):
            raise Rejected("Workspace/root directory is missing")
        config = {"schema": 1, "project_id": str(uuid.uuid4()), "roots": mappings,
                  "budget": {"pack_bytes": 64000, "source_bytes": 1000000, "repairs": 2, "minutes": 60, "e2e_minutes": 240},
                  "automation": "observe_propose", "native_context_tokens": None}
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(config, indent=2) + "\n")
        return cls(root), True

    def resolve(self, reference):
        alias, sep, relative = reference.partition(":")
        if not sep or alias not in self.roots or not relative:
            raise Rejected("Use registered-alias:relative/path")
        path = Path(relative)
        if path.is_absolute() or path.drive or ".." in path.parts:
            raise Rejected("Path traversal/absolute path rejected")
        for component in path.parts:
            name = component.casefold()
            if (name == ".agent-workflow" or name.startswith(".env") or name.startswith(".mcp.json")
                    or name.endswith((".key", ".pem", ".pfx"))):
                raise Rejected("Private state/credential files are excluded")
        target = (self.roots[alias] / path).resolve()
        if not target.is_relative_to(self.roots[alias]):
            raise Rejected("Path/junction escapes registered root")
        return target

    def snapshot(self, reference, start=None, end=None):
        path = self.resolve(reference)
        if path.stat().st_size > self.config["budget"]["source_bytes"]:
            raise Rejected("Source too large; prepare a smaller explicit source")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Rejected(f"Source {reference} is not UTF-8 text; export a UTF-8 copy and cite that file")
        if start is not None:
            lines = text.splitlines(keepends=True)
            if not 1 <= start <= end <= len(lines):
                raise Rejected("Invalid inclusive line range")
            text = "".join(lines[start - 1:end])
        return {"ref": reference, "sha256": digest(raw), "start": start, "end": end, "text": text}

    def policies(self, references):
        locations = set(self.root.parents) | {self.root}
        for reference in references:
            target = self.resolve(reference)
            locations.update(target.parent.parents)
            locations.add(target.parent)
        paths = {folder / name for folder in locations for name in ("AGENTS.md", "CLAUDE.md")
                 if (folder / name).is_file()}
        return [{"path": os.path.relpath(p, self.root), "sha256": digest(p.read_bytes()), "text": p.read_text(encoding="utf-8-sig")}
                for p in sorted(paths)]

    def assert_fresh(self, spec):
        current = Project(self.root)
        if current.config_hash != spec["config_sha256"]:
            raise Rejected("Configuration changed; create a new task after review")
        references = [spec["output"]] + [s["ref"] for s in spec["sources"]]
        policies = self.policies(references)
        if [(x["path"], x["sha256"]) for x in policies] != [(x["path"], x["sha256"]) for x in spec["policies"]]:
            raise Rejected("Applicable policy set/content changed; review the change, then run rebind with a note")
        for source in spec["sources"]:
            if self.resolve(source["ref"]) == self.resolve(spec["output"]):
                continue  # The declared output may intentionally edit this source in place.
            if self.snapshot(source["ref"], source["start"], source["end"])["sha256"] != source["sha256"]:
                raise Rejected("Source changed; review the change, then run rebind with a note")
