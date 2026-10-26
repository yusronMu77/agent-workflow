# Agent Workflow

Portable workflow controller used by a native coding-agent chat in VS Code.
Python 3.10+ standard library, SQLite state, filesystem artifacts; no provider key required.
This is an implementation of the approved workflow architecture, beginning with the native-chat
path, durable project/task state, context packs, validators, feedback proposals and scoped memory.

## Project layout and ownership

| Area | Purpose |
|---|---|
| `wf.py`, `workflow_core/` | CLI, project/scope guards, transactional state, context, feedback |
| `tests/` | Unit, crash/transaction, scope and end-to-end integration tests |
| `skills/agent-workflow/` | Canonical chat skill; bundled launcher locates this kit |
| `docs/` | User guide, capability limits and architecture |
| `agent-workflow.code-workspace` | VS Code source/docs/tests workspace with explicit tasks |
| `.agent-workflow/` in a target project | Private generated config/state/pack/artifacts; ignored |

This is a standalone project beside research. Respect each target project's applicable instructions.
The kit must work after copying elsewhere: no imports from research, agent-setup, notes helpers or C:/apps.
Target projects retain their own root/parent/nested instructions, native approvals and source files.

## Chat workflow

Native is the default, including for multi-step work. For an explicit workflow request, or a risky write
in a project initialized for this kit, read `skills/agent-workflow/SKILL.md` and use the CLI through the
native terminal tool. Ordinary questions need no task. The current chat executes work; a task record never authorizes
external writes, provider changes, plugins, deployment or publication by itself.

Bind the actual target project, requirements, roots and acceptance before executing a stage.
Keep required instructions intact. Save long outputs as artifacts, then read the returned context
pack and work with the smallest relevant source excerpts. Checkpoint before long handoffs.
Use validators appropriate to the task; required text alone cannot prove content correctness.

State transitions and outbox writes share one SQLite transaction. Do not edit the database,
generated config or checkpoints manually. Unknown write outcomes block replay until reconciled.
One task owns an output path; releases and memory proposals do not mutate instructions or code.
Feedback defaults to observe/propose. Memory requires project scope, source hashes and explicit
verification; candidate records are not facts and must not be retrieved as instructions.

## Development

Run `python -m unittest discover -s tests -v` from this folder. Check portable integration from a
temporary directory outside the source tree. Do not commit/push unless requested.
Do not copy `.mcp.json`, `.env`, auth, secrets, native session databases or model weights into the kit.
Optional LLM and external adapters remain explicit dependencies; no model is resident by default.
