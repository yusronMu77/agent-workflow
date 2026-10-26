---
name: agent-workflow
description: Durable state, bound evidence and gates for explicit workflow/checkpoint/resume requests or risky writes in initialized projects; not for questions, lookups or one-step edits. Native is the default, also for ordinary multi-step work.
---

# Agent Workflow

The current chat does the work. State/pack/review never grant permissions or start agents.
Use native tools directly for questions, lookups, one-step edits and scoped log inspection.
Native is the default, including for multi-step work: in the K5 tests (Codex, five stages) a plain handoff note kept the same quality as workflow state at lower cost.
Use this skill when explicitly requested, or for risky writes where bound evidence, unknown-write reconciliation or a gate is worth the extra requests.

Normal work needs two controller calls. W below is this skill's scripts/wf.py; P is the target.
Select workflow, output and acceptance from the user's goal; the commands below are JSON examples.
Declare semantic acceptance with --review-required ID; read references/advanced.md for extra reviews,
source ranges, literal requirements and workflows other than this example before establishing the task.

1. Read applicable project rules, then:
   python W --project P start T --init --request "TASK" --workflow code --output project:out.json --check json
   Use --init only for a new single-root project; existing state is preserved. Multi-root setup uses manual init.
   Read the inline context returned by start completely. Do not reread its context_path, run doctor routinely,
   or repeat pack without a change. --host claude/codex references rules that host already loaded;
   default is conservative. Use --host none if rules are absent from chat.
2. Work and inspect the artifact. Check file size first; use scoped searches for large logs.
   Run the real checks and inspect their output; save a UTF-8 check.log. Native commands can do this
   in one call. Keep short output direct; archive/retrieve long output only when needed.
3. After reviewing artifact and actual check output:
   python W --project P finish T --input project:check.log --exit-code N --review-result passed --review-note "WHAT WAS CHECKED"
   Report the actual exit N and passed/failed review. finish captures the immutable log, binds review
   to the output hash and runs all gates. It does not run checks or prove the review claim.
   Never claim success for missing evidence, nonzero checks or pending extra reviews.

Checkpoint at a real handoff, risky stage or budget boundary, not after every tool.
For resume, drift/rebind, failed checks, extra reviews, unknown writes, read-output pagination,
memory or authorized reviewer handoff, read references/advanced.md. Unknown writes require
reconciliation before retry. Do not replace that guard with finish or bypass native approvals.
