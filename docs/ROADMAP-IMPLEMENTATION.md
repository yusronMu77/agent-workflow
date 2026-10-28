# Roadmap implementation status

Status of every open item from the research checklist
(`research/notes/2026-10-25-agent-workflow-checklist-belum-diimplementasi.md`, sections B, C and D)
after the 2026-10-28 implementation pass. "Implemented" means the code exists in this kit and unit
tests in `tests/test_roadmap.py` exercise it on temporary fixtures. No feature here was qualified in a
native Claude Code or Codex session, run against real feedback/benchmark data, or installed into a
client configuration. Schema stays 1: new records are events in the existing outbox.

## Feedback loop and self-improvement (checklist B)

| Item | Status | Where | Command |
| --- | --- | --- | --- |
| Producer for `route_corrected` | Implemented | `workflow_core/routing.py` | `route record`, `route correct EVENT --to R --reason` |
| Proposal that changes prompt/rules/MCP/hook, review and activation in the CLI | Implemented | `workflow_core/changes.py` | `change propose/evaluate/review/activate/rollback/list` |
| Feedback that researches MCP/skill/web catalogs for a gap | Implemented (local catalogs; web queries handed to the chat) | `workflow_core/catalog.py` | `catalog --need TEXT --skills DIR --mcp-config FILE` |
| Isolated candidate evaluation, install, trial, automatic rollback | Implemented (temporary copy plus redirected profile, not an OS sandbox) | `workflow_core/candidates.py` | `candidate trial/install/rollback` |
| Benchmark importer | Implemented | `workflow_core/benchmarks.py` | `benchmark --input alias:file.json` |
| Worker/scheduler for background self-improvement | Implemented (observe/propose only) | `scripts/worker.py` | `python scripts/worker.py --project P [--interval S]` |
| Health check per MCP/tool | Implemented | `workflow_core/health.py` | `health --mcp-config FILE [--probe]` |
| Provenance of tool/MCP routing decisions | Implemented | `workflow_core/routing.py` | `route record`, `route history`, `route select` |
| Verification by the harness instead of the agent's report | Implemented; Stop hook is opt-in and not installed | `workflow_core/harness.py`, `workflow_core/runner.py` | `harness configure/run/hook`, `finish --run`, check `harness_exit_zero` |

## Items that were tests, not features (checklist C)

| Item | Status |
| --- | --- |
| Codex host detection | Implemented: `CODEX_THREAD_ID` selects `codex`; `AGENT_WORKFLOW_HOST` overrides detection, for example `none` when an inherited variable does not prove the instructions were loaded. |
| macOS | CI matrix now includes `macos-latest`; it has not run until the commit is pushed. |
| K7 on real data | Run 2026-10-28: `usage_ledger.py scan` read 324 native sessions (47 Claude, 277 Codex, counts only). Cost per accepted outcome stays unknown because no session has an outcome label yet (K6 owner feedback). |
| Runtime implicit-invocation precision/recall, token overhead, Claude Code K5 stage, K6/K8/K9 on real data | Still open. These need native sessions or owner input (feedback, frozen regression set, batch approval). |

## Reference architecture roadmap (checklist D)

| Item | Status | Where |
| --- | --- | --- |
| M1 remainder: process/job ownership and host resource admission | Implemented | `workflow_core/jobs.py`, `workflow_core/job_wrapper.py`; `job start/list/stop/reap/admission`; limits in `.agent-workflow/host.json` (`max_jobs`, `min_free_mb`) |
| M2: dispatch to another agent with a qualified profile; manual handoff fallback | Implemented; no profile is qualified by default | `workflow_core/dispatch.py`; `dispatch profiles/qualify/send` with templates for `claude`, `codex`, `acpx` |
| M3: MCP lifecycle broker, lease per target, idle eviction | Implemented for local HTTP servers only; not installed | `workflow_core/broker.py`; `mcp register/lease/release/evict` |
| M4: Rulesync dry-run on a copy | Implemented | `workflow_core/rulesync.py`; `rules dry-run [-- argv]` |
| M4: Basic Memory shadow copy | Implemented (export to a new folder) | `workflow_core/knowledge.py`; `memory shadow-export --output DIR` |
| M4: semantic memory | Implemented as offline TF-IDF cosine, no embedding model | `memory search --semantic --query TEXT` |
| M4: automatic restore of a DB backup | Implemented | `workflow_core/recovery.py`; `restore --from FILE`, `restore --auto --backups DIR` |
| M5: feedback promotion (staged patch, fresh acceptance, shadow, rollback) | Implemented through staged changes | `workflow_core/changes.py` |
| M6: provider UI | Implemented as a read-only local status page | `scripts/status_ui.py` |
| M6: selector integration in core | Implemented as a pluggable selector; a Qwen model runs only if configured in `.agent-workflow/selector.json` | `workflow_core/routing.py`; `route select` |
| M6: gateway | Implemented as a token-protected local JSON API exposing read and propose operations only (the MetaMCP gateway decision stands) | `scripts/gateway.py`; needs `AGENT_WORKFLOW_GATEWAY_TOKEN` |
| M6: additional worker models | Implemented for OpenAI-compatible endpoints (llama.cpp, Ollama, LM Studio); drafts go to task evidence only, and a registered model can back the selector | `workflow_core/models.py`; `model register/draft/list`, `selector.json` `{"model": NAME}` |
| Task acceptance on real user work | Implemented and run on recorded tasks: kit 22 tasks, acceptance 0.905; research 3 tasks, 1.0 (2026-10-28) | `workflow_core/acceptance.py`; `acceptance [--workflow W]` |
| Default end-to-end budget | Implemented: `budget.e2e_minutes` default 240 for new projects (240 assumed when absent); `budget [TASK]` reports consumption |

## Safety boundaries kept

- Nothing writes a target until `change activate`. Activation requires a passing shadow
  evaluation and a passed review, both bound to the staged hash. It also requires an accepted
  linked proposal (when one is given) and an unchanged target. A failing post-activation check
  rolls back automatically. A rollback refuses to overwrite edits made after activation.
- `job stop` and broker eviction touch only processes this project recorded. Unrecorded PIDs are
  refused.
- `health` reports variable names and booleans, never secret values. `--probe` starts stdio servers
  or calls HTTP servers, so run it only when that is acceptable.
- `candidate trial` executes the candidate's commands. The isolation is a temporary copy with
  redirected HOME/APPDATA/TEMP, not a sandbox.
- `dispatch send --execute` runs only with a profile qualified by evidence. Otherwise the
  result is a manual handoff.
- The worker never activates, installs or dispatches.

## Opt-in Stop hook (not installed)

```json
{"hooks": {"Stop": [{"hooks": [{"type": "command",
  "command": "python <kit>/wf.py --project <target> harness hook"}]}]}}
```

Configure the check first: `python wf.py --project P harness configure default -- python -m unittest discover -s tests`.

## Scheduling the worker (not installed)

```powershell
schtasks /Create /SC MINUTE /MO 30 /TN agent-workflow-worker /TR "python <kit>\scripts\worker.py --project <target>"
```

## Verification

`python -m unittest discover -s tests` includes 29 roadmap tests. These use temporary projects, fake
MCP servers and short Python child processes only.
