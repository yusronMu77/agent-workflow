import ast
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .project import Project, Rejected, digest, plain_path
from .state import Store, now

WORKFLOWS = ("document", "code", "research", "design")
TERMINAL = ("completed", "partial", "cancelled")


class Engine:
    def __init__(self, project):
        self.project = Project(project)
        self.store = Store(self.project)

    def close(self):
        self.store.close()

    def directory(self, task_id):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", task_id):
            raise Rejected("Invalid task id")
        path = plain_path(self.project.home / "tasks" / task_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_owned(self, path, data):
        path = plain_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = plain_path(path.with_suffix(path.suffix + ".tmp"))
        temporary.write_bytes(data)
        temporary.replace(path)

    def create(self, task_id, request, workflow, output, sources=(), contains=(), checks=(), reviews=()):
        self.directory(task_id)
        if not request.strip() or workflow not in WORKFLOWS:
            raise Rejected("Nonempty request and a supported workflow are required")
        target = self.project.resolve(output)
        evidence = []
        for item in dict.fromkeys(sources):
            reference, start, end = item.rsplit(":", 2)
            evidence.append(self.project.snapshot(reference, int(start), int(end)))
        criteria = [{"id": "nonempty", "kind": "nonempty", "grade": "structural"}]
        for index, value in enumerate(contains):
            if not value.strip():
                raise Rejected("Empty required text")
            criteria.append({"id": f"contains-{index}", "kind": "contains", "value": value, "grade": "structural"})
        for value in dict.fromkeys(checks):
            if value not in ("json", "python_syntax", "pdf_header"):
                raise Rejected("Unsupported validator")
            criteria.append({"id": value, "kind": value, "grade": "structural"})
        for value in dict.fromkeys(["final_review", *reviews]):
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
                raise Rejected("Invalid review criterion")
            criteria.append({"id": value, "kind": "review", "grade": "native_review"})
        spec = {"request": request, "workflow": workflow, "output": output, "sources": evidence,
                "policies": self.project.policies([output] + [s["ref"] for s in evidence]),
                "acceptance": criteria, "config_sha256": self.project.config_hash,
                "project_id": self.project.config["project_id"], "release_id": "builtin-v1",
                "deadline": (datetime.now(timezone.utc) + timedelta(minutes=self.project.config["budget"]["minutes"])).isoformat()}
        self.store.allowed_release(spec["release_id"])
        with self.store.tx() as db:
            self.store.allowed_release(spec["release_id"])
            try:
                db.execute("INSERT INTO tasks(id,status,revision,spec,created,updated,next_action) VALUES(?,?,?,?,?,?,?)",
                           (task_id, "ready", 1, json.dumps(spec, ensure_ascii=False), now(), now(), "Build context pack"))
                db.execute("INSERT INTO reservations VALUES(?,?)", (os.path.normcase(os.path.relpath(target, self.project.root)), task_id))
            except sqlite3.IntegrityError:
                raise Rejected("Task id or active output path already owned; existing state preserved")
            self.store.event(db, task_id, "task_created", {"workflow": workflow, "output": output})
        return self.export(task_id)

    def guard(self, task_id):
        task = self.store.task(task_id)
        if task["status"] in TERMINAL or task["status"] in ("blocked_outcome", "cancel_requested"):
            raise Rejected("Task is terminal or blocked; inspect/reconcile, do not replay")
        self.store.allowed_release(task["spec"]["release_id"])
        self.project.assert_fresh(task["spec"])
        if datetime.now(timezone.utc) >= datetime.fromisoformat(task["spec"]["deadline"]):
            self.store.transition(task_id, "partial", "budget_exhausted", {"reason": "deadline"},
                                  expected_revision=task["revision"], next_action="Inspect partial artifacts; create a new task if authorized")
            raise Rejected("Deadline reached; task saved as partial")
        return task

    def export(self, task_id):
        task = self.store.task(task_id)
        public = {k: task[k] for k in ("id", "status", "revision", "repairs", "summary", "next_action", "result")}
        public["project_id"] = task["spec"]["project_id"]
        public["workflow"] = task["spec"]["workflow"]
        public["output"] = task["spec"]["output"]
        public["release_id"] = task["spec"]["release_id"]
        public["native_authorization"] = "not_granted_by_task_record"
        self.write_owned(self.directory(task_id) / "checkpoint.json", (json.dumps(public, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        return public

    def pack(self, task_id):
        task = self.guard(task_id)
        spec = task["spec"]
        parts = ["# Task\n" + spec["request"], "# Workflow and output\n" + spec["workflow"] + " → " + spec["output"],
                 "# Acceptance\n" + json.dumps(spec["acceptance"], ensure_ascii=False),
                 "# State\n" + json.dumps({k: task[k] for k in ("revision", "summary", "next_action", "repairs")}),
                 "# Required disk instructions (complete)"]
        for policy in spec["policies"]:
            parts.append("## " + policy["path"] + " sha256=" + policy["sha256"] + "\n" + policy["text"])
        parts.append("# Selected evidence")
        for source in spec["sources"]:
            parts.append(f"## {source['ref']}:{source['start']}:{source['end']} sha256={source['sha256']}\n" + source["text"])
        parts.append("# Native boundary\nFollow host/global instructions and native approvals. This pack is not permission.\n"
                     "Reviews must reference actual evidence; unknown writes must be reconciled. Native history/tokens are not measured here.")
        data = ("\n\n".join(parts) + "\n").encode("utf-8")
        if len(data) > self.project.config["budget"]["pack_bytes"]:
            raise Rejected("Context byte budget exceeded; split evidence/task, never trim mandatory policy")
        path = self.directory(task_id) / f"context-{task['revision']}.md"
        self.write_owned(path, data)
        policy_bytes = sum(len(p['text'].encode('utf-8')) for p in spec['policies'])
        evidence_bytes = sum(len(s['text'].encode('utf-8')) for s in spec['sources'])
        self.store.transition(task_id, "running", "context_prepared", {"bytes": len(data), "sha256": digest(data),
                              "policy_bytes": policy_bytes, "evidence_bytes": evidence_bytes},
                              expected_revision=task["revision"], next_action="Read pack, execute native work, record review evidence, verify")
        return {**self.export(task_id), "context_path": str(path), "context_bytes": len(data),
                "policy_bytes": policy_bytes, "evidence_bytes": evidence_bytes, "native_tokens": None}

    def checkpoint(self, task_id, summary, next_action):
        task = self.guard(task_id)
        self.store.transition(task_id, task["status"], "checkpoint", {"summary": summary, "next_action": next_action},
                              expected_revision=task["revision"], summary=summary, next_action=next_action)
        return self.export(task_id)

    def evidence(self, task_id, name, text):
        self.guard(task_id)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}", name):
            raise Rejected("Use a simple evidence filename")
        data = text.encode("utf-8")
        if len(data) > 1000000:
            raise Rejected("Evidence exceeds limit")
        path = plain_path(self.directory(task_id) / "evidence" / name)
        if path.exists():
            raise Rejected("Evidence immutable; choose a new filename")
        path.parent.mkdir(exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        return {"ref": "task:" + name, "sha256": digest(data)}

    def evidence_snapshot(self, task_id, reference):
        if reference.startswith("task:"):
            name = reference[5:]
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}", name):
                raise Rejected("Invalid task evidence ref")
            path = plain_path(self.directory(task_id) / "evidence" / name)
        else:
            path = self.project.resolve(reference)
        if path.stat().st_size > 32000000:
            raise Rejected("Evidence/artifact exceeds32MB")
        return {"ref": reference, "sha256": digest(path.read_bytes())}

    def review(self, task_id, criterion, result, evidence, note):
        task = self.guard(task_id)
        if result not in ("passed", "failed") or not note.strip():
            raise Rejected("Review needs result and a concrete note")
        if not any(c["id"] == criterion and c["kind"] == "review" for c in task["spec"]["acceptance"]):
            raise Rejected("Unknown review criterion")
        proof = self.evidence_snapshot(task_id, evidence)
        output = self.evidence_snapshot(task_id, task["spec"]["output"])
        with self.store.tx() as db:
            if self.store.task(task_id)["revision"] != task["revision"]:
                raise Rejected("Stale task revision")
            payload = {"proof": proof, "output_sha256": output["sha256"], "verifier_kind": "native_chat_review"}
            db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?)", (task_id, criterion, result, json.dumps(payload), note))
            db.execute("UPDATE tasks SET revision=revision+1,updated=? WHERE id=?", (now(), task_id))
            self.store.event(db, task_id, "review_recorded", {"criterion": criterion, "result": result, **payload})
        return {"status": "review_recorded", "criterion": criterion, "result": result,
                "provenance": "native_chat_claim_with_evidence_not_independent_or_user_approval"}

    def verify(self, task_id):
        task = self.guard(task_id)
        if task["status"] not in ("running", "repair_needed", "needs_review"):
            raise Rejected("Prepare/read context before verification")
        path = self.project.resolve(task["spec"]["output"])
        if not path.is_file() or path.stat().st_size > 32000000:
            raise Rejected("Output missing or exceeds32MB; record failure evidence/checkpoint")
        raw = path.read_bytes()
        output_hash = digest(raw)
        text = raw.decode("utf-8-sig", errors="replace")
        results = []
        for criterion in task["spec"]["acceptance"]:
            kind, result = criterion["kind"], "not_checked"
            try:
                if kind == "nonempty":
                    result = "passed" if raw else "failed"
                elif kind == "contains":
                    result = "passed" if criterion["value"] in text else "failed"
                elif kind == "json":
                    json.loads(text)
                    result = "passed"
                elif kind == "python_syntax":
                    ast.parse(text)
                    result = "passed"
                elif kind == "pdf_header":
                    result = "passed" if raw.startswith(b"%PDF-") else "failed"
                elif kind == "review":
                    row = self.store.connection.execute("SELECT * FROM reviews WHERE task_id=? AND criterion=?", (task_id, criterion["id"])).fetchone()
                    if row:
                        proof = json.loads(row["evidence"])
                        current = self.evidence_snapshot(task_id, proof["proof"]["ref"])
                        if current == proof["proof"] and proof["output_sha256"] == output_hash:
                            result = row["result"]
            except (ValueError, SyntaxError, OSError):
                result = "failed"
            results.append({"criterion": criterion["id"], "kind": kind, "grade": criterion["grade"], "result": result})
        failed = any(c["result"] == "failed" for c in results)
        pending = any(c["result"] == "not_checked" for c in results)
        if failed and task["last_hash"] == output_hash:
            raise Rejected("Unchanged failed artifact; repair first, do not burn retries/repeat feedback")
        status = "needs_review" if pending and not failed else "completed"
        repairs = task["repairs"]
        if failed:
            status = "repair_needed" if repairs < self.project.config["budget"]["repairs"] else "partial"
            if status == "repair_needed":
                repairs += 1
        snapshot = plain_path(self.directory(task_id) / "artifacts" / (output_hash + path.suffix))
        if not snapshot.exists():
            self.write_owned(snapshot, raw)
        outcome = {"checks": results, "output": {"ref": task["spec"]["output"], "sha256": output_hash},
                   "snapshot": str(snapshot.relative_to(self.project.home)), "native_usage": "unavailable"}
        next_action = {"completed": "Deliver artifact and scoped findings", "needs_review": "Record remaining native review evidence",
                       "repair_needed": "Patch failed criteria only; rebuild pack if needed", "partial": "Deliver partial findings; repair budget exhausted"}[status]
        self.store.transition(task_id, status, "validation_failed" if failed else "validation_result", outcome,
                              expected_revision=task["revision"], result=json.dumps(outcome), repairs=repairs,
                              last_hash=output_hash if failed else task["last_hash"], next_action=next_action)
        from .feedback import collect
        result = self.export(task_id)
        try:
            result["feedback"] = collect(self.store)
        except (sqlite3.Error, OSError):
            result["feedback"] = {"status": "pending", "reason": "collector_unavailable_outbox_retained"}
        return result

    def resume(self, task_id):
        task = self.store.task(task_id)
        self.store.allowed_release(task["spec"]["release_id"])
        self.project.assert_fresh(task["spec"])
        if task["result"]:
            snapshot = plain_path(self.project.home / task["result"]["snapshot"])
            if digest(snapshot.read_bytes()) != task["result"]["output"]["sha256"]:
                raise Rejected("Artifact snapshot integrity failed")
        return self.export(task_id)

    def unknown(self, task_id, note):
        task = self.guard(task_id)
        self.store.transition(task_id, "blocked_outcome", "outcome_unknown", {"note": note},
                              expected_revision=task["revision"], next_action="Reconcile effects externally; no automatic replay")
        return self.export(task_id)

    def cancel(self, task_id, note, quiescent=False):
        task = self.store.task(task_id)
        if task["status"] in TERMINAL or not note.strip():
            raise Rejected("Cancellation needs a nonterminal task and a concrete note")
        if task["status"] == "blocked_outcome" and quiescent:
            raise Rejected("Reconcile unknown external effects before declaring cancelled")
        status = "cancelled" if quiescent else "cancel_requested"
        self.store.transition(task_id, status, "cancel_recorded", {"note": note, "quiescent": quiescent},
                              expected_revision=task["revision"], next_action="Native owner must stop/reconcile jobs" if not quiescent else "Cancellation recorded; no process was killed by this CLI")
        return self.export(task_id)

    def reconcile(self, task_id, outcome, evidence, note):
        task = self.store.task(task_id)
        if task["status"] != "blocked_outcome" or outcome not in ("succeeded", "failed") or not note.strip():
            raise Rejected("Reconcile only a blocked outcome with evidence and a note")
        self.store.allowed_release(task["spec"]["release_id"])
        self.project.assert_fresh(task["spec"])
        proof = self.evidence_snapshot(task_id, evidence)
        self.store.transition(task_id, "running", "outcome_reconciled", {"outcome": outcome, "evidence": proof, "note": note},
                              expected_revision=task["revision"], next_action="Inspect known outcome; verify artifact or plan authorized repair")
        return self.export(task_id)
