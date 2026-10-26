# Replay and learning tools (K6-K10)

Status: code written 2026-10-26 and covered by offline unit tests (`tests/test_recipes.py`, `tests/test_learning_tools.py`); the recipe CLI was smoke-tested once on a real read-only task. **Nothing has run on real feedback, real cost data or a real internal model yet.** All tools are stdlib, live in `scripts/`, keep their data under `~/.agent-workflow/` (override with `AGENT_WORKFLOW_HOME`) and never change prompts, skills or configuration by themselves.

| Tool | Piece | Does |
|---|---|---|
| `owner_feedback.py` | K6 | Screens owner feedback (source, kind, specificity, verifiability, conflict, scope, response, recency). One correction is a `candidate`, never a fact. `ask` decides whether asking the owner is worth the interruption. |
| `cost_per_outcome.py` | K7 | Cost per accepted outcome per class from the ledger, with n and spread, under three token weightings. `compare` refuses without enough sessions and known outcomes, and reads a difference as an effect only for K9-randomized labels. |
| `change_proposals.py` | K8 | Proposals for triage thresholds and wording. Thresholds are evaluated on a frozen regression set (no metric may get worse; unsafe misses may not increase) plus unseen acceptance cases. Decisions are hash-chained and immutable. Nothing is applied. |
| `assignment.py` | K9 | Owner-approved batch (first batch capped at 20) that assigns tasks to native or workflow in seeded blocks of two. Irreversible writes are excluded by built-in rules that cannot be removed. |
| `lazy_adopt.py` | K4 trigger | PostToolUse hook that counts main-chain requests in the current turn and reminds once at 30. Reminder only. |
| `recipes.py` | K10 | Replay of mature requests (below). |

## Replay of mature requests

Goal: fewer requests to the premium model. A task that was done natively and verified is recorded as a **recipe**. When the same kind of request comes again, a deterministic runner replays it with zero model requests. Anything unexpected is handed off to the chat, which remains the executor.

```text
native task done and verified
  -> recipes.py record (candidate)          # argv lists, typed parameters, declared effects, independent postconditions
  -> 3 accepted replays, distinct parameters -> promote
new request
  -> recipes.py match                       # keyword retrieval; no clear lead means stay native
  -> optional internal LLM (--llm-endpoint) # picks among retrieved candidates and fills parameters; runner validates
  -> recipes.py run                         # guards (drift) -> steps -> postconditions
        accepted  -> done, zero model requests
        escalate  -> handoff JSON (spec, failed step, bounded stderr, changed-path hashes) to the chat
```

Rules the runner enforces:

- Steps are argv lists, never shell strings. Parameters are typed; paths must stay inside the project; secrets are parameters marked `secret` and are redacted in output.
- Every step declares an effect: `read`, `project_write` (with the paths it writes) or `external_write`. `external_write` replays only with the owner's `--confirm-external` and the step's `replay_safe: true`, and such a recipe never reaches `promoted`.
- Guards run before the first step, so drift is found before any side effect. A failed step is never retried; the handoff says which steps are done.
- A replay that dies midway leaves an unfinished journal. The same recipe and parameters refuse to run until someone inspects the project and runs `abandon`.
- Two consecutive failures demote a recipe (drift or a poisoned recipe). `canary` checks guards without side effects.
- A recipe without postconditions is rejected: replay needs an independent verifier, not the agent's own claim.
- The handoff carries the spec, the failure and file hashes, not the full trace (research: handing a full trajectory to the stronger model recovers less than half the gap at a large cost premium).

Internal LLM: any OpenAI-compatible endpoint (`/v1/chat/completions` with `response_format` json_schema; llama.cpp server works). It is called only by the runner, in the terminal. Claude Code's own documentation says routing the chat itself to non-Claude models through a gateway is unsupported, so the internal model is a tool of the runner, not the chat model. `draft` asks it to write a new recipe; the result is stored as `draft` and cannot replay until `review` and verification replays.

Break-even (inference, `report` prints it): with a deterministic verifier the replay costs about nothing, so a recipe pays when the accept rate `a` exceeds `h / (c_L + h)`, where `c_L` is the requests the native run took and `h` is the handoff cost in requests.

Research and the evidence behind these choices: `notes/2026-10-26-agent-workflow-replay-request-matang-llm-internal.md` in the research workspace.
