# Qualifying the standalone selection programme

`standalone-topics/2` adds source opportunity discovery, viewer-value assessment,
portfolio review and grounded compound repair. The complete design is in
[the implementation plan](../plans/topic-selection-quality.md). It preserves the
existing runtime, independent reviewer family, immutable evidence, expense ledger,
media renderer and revision-based human review.

V1 remains available for comparison and existing work remains readable. New v2
intents require `HARNESS_TOPIC_SELECTION_ENABLED=1` on the web server and worker.
The switch defaults off. Turning it off refuses new v2 runs while preserving
existing runs and their review surfaces. The production-image local gate enables
both policies with explicitly synthetic model fixtures; those fixtures are not
an editorial benchmark.

`standalone-topics/3` adds an independent pre-author opportunity inventory,
rationale-hidden source review, a scoped repair projection and both-edge extent
replacement. It uses the separate `HARNESS_TOPIC_SELECTION_V3_ENABLED` switch and
`HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH`. Its implementation record is
[standalone topic production quality v3](../design/standalone-topic-production-quality-v3-2026-09-12.md).

## Exact request qualification

For the gateway backend, the worker also requires
`HARNESS_TOPIC_SELECTION_QUALIFICATION_PATH`. This is a manifest binding a frozen
production route snapshot to successful, settled requests using all four exact
production prompt functions and native output schemas. Qualification covers each
route that can occupy the author/repair or cold/source-review seats. It does not
select a vendor or establish a quality winner.

Use the existing metadata-only candidate catalogue format, with exact
`gatewayModel`, `provider`, `family`, capacity, ZDR claim and prices. Optional
`reasoningEffort` and `serviceTier` fields must match the intended frozen route;
caching remains unsupported by the gateway transport. Obtain current catalogue
and pricing evidence before choosing these values. An old model name, reputation
or successful chapter-schema request does not qualify the new schemas.

From `apps/pipeline`, in the synchronized environment:

```sh
.venv/bin/python scripts/qualify_harness_gateway.py run \
  --suite topic-selection \
  --candidates /private/tmp/topic-qualification/candidates.json \
  --journal /private/tmp/topic-qualification/journal.json \
  --receipts /private/tmp/topic-qualification/receipts \
  --report /private/tmp/topic-qualification/report.json \
  --max-exposure-micros "$REVIEWED_EXPOSURE_MICROS" \
  --max-dispatches "$REVIEWED_DISPATCHES" \
  --max-output-tokens "$QUALIFIED_OUTPUT_TOKENS"
```

This command dispatches inference and needs the existing authorized gateway
credential in `AI_GATEWAY_API_KEY` for legacy Vercel routes, or `OPENROUTER_API_KEY`
for explicit OpenRouter routes. Candidate transport determines the gateway;
optional `--gateway openrouter` verifies that identity rather than overriding it.
The operator supplies the experiment's execution
settings explicitly; the new suite introduces no fixed experiment-spend, candidate
count or dispatch ceiling. The historical suites retain their historical ceilings.
A candidate requires four calls for a complete qualification. Do not restart a
session to evade an unknown outcome: the existing journal and read-only
reconciliation command retain unresolved exposure. Journal, report and receipt
paths are create-only; use a distinct directory for a genuinely new experiment.

For v3, use `--suite topic-selection-v3`. A candidate requires five calls because
the independent inventory is a separate reviewer request. Bind it with
`--program-version standalone-topics/3 --bind-transport`; this produces
`topic-selection-qualification/5` and cannot be substituted for a v2 proof.

The synthetic source is deliberately small. A passing suite proves this request
shape, structured output admission and settled cost observation. It does not prove
maximum context capacity, long-source quality, cold comprehension by real viewers,
actual media quality or the correctness of a provider's broader policy claims.

Bind qualifying reports without making a model call:

```sh
.venv/bin/python scripts/qualify_harness_gateway.py bind-topics \
  --snapshot /private/tmp/topic-qualification/routes.json \
  --reports /private/tmp/topic-qualification/report.json \
  --max-output-tokens "$QUALIFIED_OUTPUT_TOKENS" \
  --output /private/tmp/topic-qualification/admission.json
```

The manifest includes exact report byte hashes, snapshot identity and effective
maximum output setting. Validation checks response bytes and generation identity,
replays source admission, and compares production prompt/contract/native-schema
hashes, model/provider/family, routing/privacy options, reasoning and service tier.
Every needed route/seat combination must have evidence. A stale schema, changed
receipt, missing route or unresolved cost refuses admission. The effective output
setting is checked at worker boot and before each v2 gateway call against the
run's frozen configuration. A route's advertised maximum is not the effective
request setting. Keep the manifest and referenced reports/receipts accessible to
the worker at their recorded paths; no customer source is used by this suite.

The default binding remains `topic-selection-qualification/2`, with one exact
effective output allowance. To use explicitly configured route ceilings, add
`--per-route-output` to `bind-topics`. This opts into `/3`: the requested run ceiling
is retained alongside `routeMaxOutputTokens` for every usable author/reviewer route.
Each value must equal the smaller of that run ceiling and the immutable route's
`max_output_tokens`, and must match the original settled request. Lowering a route
ceiling creates a new snapshot; preserve its original advertised capacity evidence.
Missing/extra keys, stale ceilings and other-setting receipts refuse admission.
The flag does not dispatch inference or infer capacity from observed token usage.

Explicit gateway transports require `--bind-transport`, which produces
`topic-selection-qualification/4`. Each candidate carries a complete `transport`
(`version`, `gateway`, `mode`, `request_timeout_seconds`, `total_timeout_seconds`)
and OpenRouter's catalogue-bound `providerAccountingName`. The snapshot uses
`provider_accounting_name`. `/4` binds these fields and effective route outputs,
then validates the actual URL, stream mode, HTTP deadlines, provider/privacy/cache
controls, complete response and retained generation-accounting receipt. `/2` and
`/3` cannot qualify an explicit transport. See the
[declared initial OpenRouter condition](../plans/openrouter-model-evaluation.md).

New OpenRouter candidates use `gateway-transport/2` with an explicit
`output_token_parameter` chosen from the endpoint's advertised parameters. Freeze
`accountingModel` from the model catalogue's canonical slug; the snapshot field is
`accounting_model`. The API request still uses `gatewayModel`, which may be an alias.
Accounting must match its declared canonical identity exactly. Transport version 1
keeps its original encoding and request-model accounting; do not retrofit its saved
records or treat earlier failures as successful qualification.

An experiment worker uses one gateway. Set `HARNESS_GATEWAY=openrouter` and supply
the matching key in the pipeline process; keep secrets out of route files and CLI
arguments. The experiment launcher emits the gateway from its frozen snapshot.
Boot rejects a gateway/snapshot mismatch or a missing matching credential. Absence
of `HARNESS_GATEWAY` preserves existing Vercel workers. Keep the manifest, original
reports and all referenced response/accounting files together at their recorded
paths. Never fill historical transport settings from the current environment.

Streaming is consumed inside the existing paid activity. An observed header or
chunk generation ID is persisted before further content is read when available.
Incomplete EOF, stream error or deadline expiry keeps the unknown fence and any
handle; a complete truncated output retains its receipt and expense before being
rejected. Read-only generation reconciliation can recover reported cost, but cannot
recover a missing model response or authorize duplicate inference.

V2 uses the same effective allowance in preparation, all four native requests,
expense reservations and response-reuse identity. Older workflows still require
the full global allowance to fit their routes, even when sharing a v2-enabled worker;
incompatible older-policy starts refuse before database creation or ownership claim.

Terminal `completed` and `halted` reports may provide original settled proofs for
unrelated routes. Any selected model/provider with an unsettled call in any referenced
report remains excluded. A later pass cannot override that uncertainty. Preserve the
whole report and journal; do not relabel, subset or replay an unknown request. When
original absolute receipt paths collide, retain the complete original directories
separately and provide verified read-only aliases only for nonconflicting response
files required for validation. Alias hashes must match their original receipts.

## Full-source editorial comparison

Once exact requests are qualified, freeze complete source recordings, rubric,
programme/prompt/schema identities, detector, model/provider/settings and execution
configuration. Compare the existing v1 programme and v2 under declared conditions;
a separate fixed-programme model swap can distinguish programme gains from model
gains. PySceneDetect AdaptiveDetector remains the requested topic trial; FFmpeg is
an explicit comparison. Neither detector agreement nor these package tests selects
a winner.

Run every baseline and finalist through the full production path, then use the
[topic evaluation runbook](topic-quality-evaluation.md) for scoped export,
independent opportunity labels, cold/source judgments, actual-media inspection,
correction effort and cost comparison. Retain rejected and uncompleted treatments.
Do not promote a model or programme solely because it produces valid schemas or
more candidates. Useful discussion recall and selected-video value are the primary
editorial measures; delivery and unattended acceptance are separate outcomes.

## Physical evidence and recovery

V2 persists a `topic-feasible-grid/2` derivation in the source evidence before its
hash is published. At each lexical transition, derivation intersects complete
selected-word membership with measured speech exclusions and the source-relative
frame/sample grid. Earliest, middle and latest feasible instants represent each
connected interval. Exact rational instants are encoded in reserved candidate IDs;
`timeMs` is the display approximation. Validation reproduces the entire derived
inventory, and the compiler chooses from an eligible subset without mutating it.
A 1.818 ms safe audio sample must not become an unsafe integer-ms cut.

V2 edits carry `topic-compiler/2`. Historical source evidence and v1 edits keep
their original identities. Unknown acoustic coverage remains a review concern;
absence of a feasible cut remains a physical failure with grounded context. Neither
condition authorizes silently removing selected speech or rewriting the discussion.
