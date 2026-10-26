# Workflow efficiency

Local v1.2 work combines controller calls while retaining output validation,
current-hash check evidence, explicit native review and additional required reviews.
It does not execute tests, approve a review or authorize additional operations.
The current chat performs and inspects the work.

## Use the short path with real check evidence

Example for an existing single-root target; select acceptance from the actual task:

```sh
python /kit/wf.py --project ROOT start example --request "Produce checked JSON" --workflow code --output project:answer.json --check json
# Read the inline context; create and inspect answer.json.
# Run the task's real checks and save their UTF-8 output to check.log.
# Inspect that output before recording its actual exit code and your review.
python /kit/wf.py --project ROOT finish example --input project:check.log --exit-code 0 --review-result passed --review-note "Describe the actual checks and artifact inspection"
```

Add `start --init` only when initializing a new single-root project is within scope.
Existing state is preserved. Multi-root projects use explicit manual `init` first.
`start` adds the `capture_exit_zero` gate and returns complete compact context inline;
do not read `context_path` again unless the inline response was incomplete.
`--host codex|claude` references rules already in the host context; use `--host none`
when that is not established. Mandatory rules are never summarized or trimmed.

`finish` archives the supplied log, binds an explicit final review to the current
output hash, and calls the normal verifier. Exit code is caller-reported, not an
observed subprocess result. A nonzero check or failed review cannot complete.
The latest reported check for the current output hash governs the exit-code gate;
an older passing capture cannot hide a later failing check.

Semantic acceptance still requires meaningful criteria. Declare `--review-required ID`
and record each extra review with the manual `review` command and actual evidence.
`finish` does not approve those criteria. Pending extra reviews return `needs_review`.
The main skill links to `skills/agent-workflow/references/advanced.md` for these cases,
source selection, recovery, handoff and unknown-write reconciliation.
The reference is included in source packages and independent skill installations.

Each constituent operation remains separately durable. These commands are not one
atomic transaction: inspect state after interruption rather than blindly replaying.
Older tasks without `capture_exit_zero` retain the manual review/verify path.

## Check equivalent outcomes before interpreting token measurements

The first six-session fixture had an ordering error in its checker. All six agents
produced the correct rows, recorded the nonzero check, and left `repair_needed`.
That negative evidence is retained and excluded from performance acceptance.

The corrected comparison uses three fresh old-workflow and three fresh new-workflow
sessions in balanced order. Both arms receive the same request, immutable 120,000-line
log, corrected checker, output cap and required semantic `exact_rows` review.
The old kit was frozen before implementation changes. Model, effort and configured
tool catalog are recorded per attempt; original JSONL traces and protected-file hashes
remain available. All six corrected sessions passed exact-answer, immutable-input,
workflow-completion and required-review checks; their native session IDs are unique.
Model was `gpt-6.1-sol`, reasoning effort `low`, with the same recorded tool-catalog
fingerprint. Largest command output was 9,616 bytes, below the 16,000-byte cap.

| Median across three fresh sessions per arm | Previous workflow | Short path |
|---|---:|---:|
| Input + output tokens, cached input included once | 223,995 | 157,423 |
| Input tokens | 222,332 | 156,302 |
| Cached input tokens | 202,240 | 138,496 |
| Uncached input tokens | 19,633 | 17,806 |
| Output tokens | 1,699 | 1,126 |
| Native terminal calls | 9 | 7 |
| Elapsed seconds | 57.219 | 45.473 |

Median total tokens fell **29.7%** relative to the previous workflow in this sample.
Each field's median is calculated separately; median components need not sum to
median totals. Cached input is already part of input; reasoning tokens are already
part of output. No pricing or invoice is inferred from these figures.

After snapshotting the benchmark, the final skill clarified that implicit substantial
work must belong to an initialized project. All trials explicitly invoked the skill;
they do not qualify implicit discovery or that final trigger wording.

Evidence and generator: research workspace
`resource/2026-10-25-agent-workflow-v1-1-implementasi/2026-10-25-workflow-efficiency/`,
with corrected results in `sessions-corrected/report.json`. The initial defective
fixture remains in `sessions/`; its sessions never count as successful comparisons.

The target is less overhead than the previous workflow, not a claim of lower cost
than direct native tools. One extraction task cannot establish broad task quality,
3D design quality or billed-provider savings. Cache state is uncontrolled and latency
is descriptive. Shorter instructions alone do not establish equivalent task quality.

Local regression covers complete mandatory rules and source excerpts, oversized-policy
rejection, drift, unknown outcomes, nonzero checks, invalid JSON, explicit failed review,
pending extra reviews and preservation of legacy state. Distribution tests require
the advanced reference in both the archive and independent skill installations.
All **114 tests** passed on Windows Python 3.10 and 3.13. Copied multi-folder
document/code portability, the 75-file reproducible distribution, Claude plugin
validation and Codex skill discovery also passed. These are local results, not macOS
qualification or implicit-trigger precision/recall.
