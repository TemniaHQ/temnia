# Compact chapter proposal staging qualification

Planned September 10, 2026 (Asia/Kolkata), after PR #29 merged as
`0f6d43cffe37350d2d4f30effcc1072c5da6bc32`. This continues the authorized staging
experiment. It does not select production model winners or settle an unknown attempt.

## Admission and evidence

Verify both deployed images and their merged code before new model work. Use the matching
operator CLI in an isolated finite container on staging, with the exact deployed Python image.
Use authored synthetic evidence, native strict output, per-request ZDR, an exact provider
allowlist, no SDK retries, the existing 300-second request deadline and bounded cost lookups.
Keep credentials in the server-side process environment, never in candidate files or reports.
Save create-only admission, request identities, responses and charges before assessing results.

The existing 151-minute run remains unknown: 20,236 micro-dollars reported and 109,644
reserved. Allocate at most 800,000 micros to qualification and 2,000,000 to one separately
budgeted source run. Including the old exposure, this totals 2,929,880 micros, within the
existing 3,000,000-micro allowance. A new journal or run does not reset that allowance.

The first compact batch plans three stages for each of the existing three routes, with
8,192 output tokens per request. Refuse an existing journal, output collision, over-budget
admission, missing charge, identity mismatch or unknown result. One bounded observer owns
the finite container. An interrupted observer is a reason to inspect the existing execution,
never to dispatch it again. Retain the evidence before removing only the owned terminal
container.

## A failed route changes the plan

The first batch dispatched seven calls. DeepSeek/DeepInfra and Kimi/Alibaba passed all three
stages. Qwen/Alibaba returned the requested schema inside an array instead of a summary;
the strict validator refused it and the remaining two Qwen stages were skipped. Every
dispatched charge was reported. The available response cannot locate the fault within the
upstream model, provider adapter or gateway adapter. Do not substitute the previous Qwen
proof, weaken validation, or repeat the same call until it happens to pass.

Research one replacement provider/family from current primary metadata. Its three exact
serialized requests must fit the remaining 290,843-micro conservative qualification
admission, calculated as 800,000 minus the first batch's 509,157. Give it a fresh bounded
journal and the unchanged compact request contract. A failure or unknown outcome stops
that replacement batch. Only a candidate passing all three stages can enter the new snapshot.
The original two successful candidates do not need repeat inference.

The selected replacement probe is GLM-5.3-Flash/Baseten: its current public endpoint lists
one-million-token context and $0.15/M input plus $0.50/M output. GPT-OSS-120B/Baseten has
cheaper input but a 131,072-token context; the retained long repair already required 170,136
input tokens under the harness's conservative byte accounting. Qwen/Parasail offers more
context than GPT-OSS but less than GLM and more expensive output. GLM/DeepInfra is cheaper,
but the observed gateway throughput listing was lower and it shares the DeepSeek provider.
Those listings guide this probe; they do not establish Temnia latency or model quality.
One replacement batch permits at most ten total dispatches across both batches, while the
combined qualification admission remains at most 800,000 micros.

## Configuration and recovery

Bind each eligible route to a new immutable proof manifest containing all three request,
schema, prompt, response, identity, usage and charge observations. Construct and validate
the snapshot through the production Pydantic models. Keep three distinct families per seat,
an open-weight candidate, disabled caching, the same first family for summary and proposal,
and a verifier outside every generating or repairing family. Record the exact route order;
transport success is not an editorial audition.

Before changing configuration, verify no relevant active Temporal workflows or competing
source runs and no queued/running Dokploy deployment. Preserve complete prior configuration
privately on the server. Add a new content-addressed, root-owned `0444` route file and bind
at a distinct target; preserve the old file and mount. The worker receives the new path and
snapshot ID. The web receives the matching ID, with no key or route-file mount. All unrelated
environment, build settings, mounts and harness caps are preserved.

Apply the worker first through Dokploy's supported configuration reload. Verify its actual
service and container environment, retained image, route bytes and canonical ID, nonroot
write refusal, boot and Temporal readiness. Then reload the matching web configuration and
verify its actual environment, image and health. A successful API response alone proves none
of these. An ambiguous response requires inspection before any repeated mutation. On failure,
retain logs and inspect work in flight before explicitly restoring the backed-up configuration
and reloading. Do not overwrite the old snapshot or change a saved run's envelope.

## Long-source and user checks

Refresh the authenticated staging browser after deployment. Verify the explanation and
disabled controls on the old unknown run, and the separate-charge explanation on New run.
Immediately before submission, recheck scoped source/transcript identity, the old ledger and
absence of competing work. Start one new run with the original brief and a $2 maximum.
Require visible submission acknowledgement plus a durable new run/workflow/config identity.
Model-operation reuse stays within its original run; existing source and transcript artifacts
may be reused.

Observe the source run with a finite, content-free progress feed. At a quiescent outcome retain
its history, accounting, immutable artifacts and dependency hashes. Inspect exact coverage,
diagnostics, rendering and media checks before exercising chapter correction, review and export.
Distinguish mechanical checks, browser behavior, listening and human editorial acceptance.
Never invent gold boundaries, accept unreviewed chapters to complete a test, or call the feature
qualified when it stops before review.

## Lessons and delivery

- Replace the earlier large output and undirected repair with the merged compact wire and
  diagnostic-driven repair; measure the long-source result before claiming the fix is sufficient.
- Preserve failed and unknown paid evidence. A new run or cost-only observation does not recover
  a missing response, and no partial JSON is completed in code.
- Replace catalogue-based eligibility with observed exact request-shape proof. Keep failures
  attached to their precise model/provider pair rather than excluding a whole model family.
- Verify real staging configuration and browser state, including reload and unknown-result
  behavior. Local cassettes do not establish live transport, latency or editorial quality.

Record measured results and the daily log, plus any evidence-driven fixes, in a new verified PR.
Every final commit receives the required exact-SHA local release gate and GitHub checks.
Rajesh merges; social drafts are not published by this work.
