# OpenRouter transport and topic-model evaluation

September 11, 2026. Rajesh configured an OpenRouter key in staging and merged PR #37
as `a9cf6886da41e0b9977628df8fa6705ef242d538`. Continue the chapter-selection
audition through an explicitly qualified OpenRouter transport. Keep the source,
editorial programme, evidence, ledger and rendering foundations. Transport completion
is necessary to measure selection quality; it is not the quality measurement itself.

## Evidence and alternatives

The prior full-Karma Astra request reached the existing 300-second client read
timeout, with no response or generation handle. Its $4.548260 estimated reservation
and the earlier DeepSeek qualification unknown remain intact. Neither unknown is
reset, replayed or reclassified by this work. The unstarted Vercel Kimi/Opus cases
remain separate preparations.

The current adapter uses a Vercel-only URL and policy object, disables streaming,
and attaches a generation handle after the complete response. A URL change would
not qualify OpenRouter's privacy/routing/accounting or expose early stream identity.
Compared approaches:

1. Keep Vercel and only extend its timeout: fails to test Rajesh's gateway hypothesis
   and gives no early generation identity. Retain as historical control.
2. Adopt the installed PydanticAI OpenRouter model wholesale: provides a dedicated
   integration but changes model/profile transformations as well as transport.
3. Keep the installed OpenAI-compatible SDK/profile and add a named OpenRouter
   adapter, consuming streaming internally within the existing paid request activity.
   Choose this smaller boundary; qualify its actual native request and receipt.

Primary references: OpenRouter [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[strict output](https://openrouter.ai/docs/guides/features/structured-outputs),
[streaming](https://openrouter.ai/docs/api/reference/streaming),
[router metadata](https://openrouter.ai/docs/guides/features/router-metadata), and
[generation accounting](https://openrouter.ai/docs/api/api-reference/generations/get-generation).
Installed SDK methods and behavior are inspected before implementation. No dependency
or durable runtime is added. The explicit current OpenAI profile remains the schema
owner at the transport seam; actual native-schema hashes are still qualified.

## Frozen route and process contract

Add a strict optional `transport` to Python's internal `RouteEntry` and qualification
candidate, plus an optional `provider_accounting_name` (candidate alias
`providerAccountingName`). Legacy absent fields stay omitted from serialization,
so historical snapshot IDs, operation hashes and receipts remain valid. Do not fill
historical evaluation records from current process settings.

`GatewayTransportPolicy` carries `version="gateway-transport/1"`, `gateway`
(`vercel` or `openrouter`), `mode` (`non_streaming` or `streaming`),
`request_timeout_seconds` (positive idle timeout), and `total_timeout_seconds`
(positive aggregate request deadline). All behavioral fields are explicit on new
routes. OpenRouter requires explicit transport and the exact accounting provider
display name observed in its catalogue; its routing slug remains `route.provider`.
Unknown gateway names and arbitrary base URLs refuse. Existing no-transport routes
retain their legacy Vercel execution behavior and unchanged serialized content.

One experiment worker uses one gateway. `HARNESS_GATEWAY` selects `vercel` or
`openrouter`; absence preserves existing Vercel configuration. Credentials remain
process-only: `AI_GATEWAY_API_KEY` for Vercel, `OPENROUTER_API_KEY` for OpenRouter.
Boot refuses a snapshot whose routes name a different gateway. The launcher derives
the required gateway from immutable route snapshots and emits only non-secret worker
settings. It does not repurpose a credential or switch the ordinary staging service.

The first OpenRouter profiles use a 300-second read timeout, the existing value,
and an explicit 540-second request deadline within the existing ten-minute model
activity. The remaining minute permits bounded charge lookup and persistence.
This is a declared request-lifecycle condition, not a video duration/count or
experiment-spend limit. Every new timeout value is part of the route/proof identity.
The activity itself keeps its existing timeout; evidence and render activities are
unchanged. Live results determine whether another explicitly declared condition is
needed; a timed-out request is never silently repeated under new settings.

## Request, completion and cost lifecycle

Reuse `GatewayChatModel` and the existing paid `request` operation. OpenRouter sends
one Chat Completions request with native strict JSON Schema, the exact model alias,
provider allowlist and order, `allow_fallbacks=false`, `require_parameters=true`,
`zdr=true`, and `data_collection="deny"`. Disable response-cache reuse explicitly.
Request metadata is enabled for observed routing. No tools, images, server plugins,
model fallbacks, SDK retries or unqualified cache/transform settings are introduced.
Output and reasoning settings remain frozen and are checked on the actual wire.

The streamed mode drains the existing SDK stream inside `GatewayChatModel.request`
and returns one `ModelResponse`. Public agent streaming remains disabled. An awaited
HTTP response hook records `X-Generation-Id` before consuming content; chunk IDs
must remain consistent. A task-local awaited generation observer connects this to
the existing owner-fenced `ledger.attach_remote_handle`, and separately to the
qualification journal. Repeated identical IDs are harmless; conflicting IDs refuse.
Missing headers do not invent identity; the first actual chunk ID may provide it.

Consume the entire stream, require a terminal finish reason, retain usage/metadata,
and run the same strict schema and grounding validation afterward. Empty streams,
partial EOF, a mid-stream error, cancellation, deadline expiry and lost persistence
retain an unknown outcome with any observed handle. A numeric 4xx inside a 200 SSE
stream is not an initial HTTP rejection. Do not infer cancellation stopped upstream
computation or released the charge. A complete response is persisted before optional
recording and settlement, as today. SDK parsing is reused; no new general SSE parser.

Dispatch charge lookup through the same frozen gateway as the request. OpenRouter
generation identity must match the ID, model and catalogue-bound provider display
name. Parse actual reported monetary fields as finite nonnegative decimals. Preserve
reasoning/cache usage and receipt provenance. A missing, pending, mismatched or BYOK
receipt stays unknown; estimates never substitute for reported cost. The generation
lookup cannot reconstruct a lost response or authorize another inference.

## Qualification and comparison

Extend the existing finite qualifier/journal and four-stage binder. New candidate
identity includes transport and accounting provider name. Preserve old report bytes
and `/2` and `/3` manifest interpretation; add `/4` for explicit transport-bound
qualification. Original halted-report exclusion and all receipt/grounding/authority
checks remain. Selected routes with unknown calls in referenced reports stay excluded.
The actual request capture verifies URL, mode, privacy/provider controls, timeouts,
schema, messages, reasoning and output. It must not pre-buffer the HTTP response and
defeat early identity or streaming. The complete SDK response and accounting receipts
remain immutable files; partial outcomes retain the early handle in the journal.

Carry the route transport into operation and cassette identities. Legacy cassette
metadata serializes exactly as before; new metadata includes the canonical transport
and accounting identity, preventing cross-gateway or cross-mode reuse. The existing
route copy in attempts and artifacts remains authoritative. Export role-keyed transport
settings into execution comparison factors; upstream provider/model changes remain
route factors. Missing historical settings remain unknown for controlled comparison.

Refresh the live model and ZDR endpoint catalogue before preparing exact candidates.
Start with the requested contemporary Astra/Opus/Gemini/Kimi comparison, keeping
DeepSeek/GLM candidates available where current routes and exact qualification support
the three-family, open-weight and independent-reviewer policy. Freeze route/provider,
quantization where known, catalogue hashes, conservative price tiers and full native
request settings. No model is selected by catalogue reputation. Qualification costs
are reported separately from full-source editorial costs.

The first captured cohort has four routes and four native stages per route (16
physical requests if all settle). Every route uses high effort, 32,768 effective
output tokens, streaming, 300-second idle and 540-second aggregate deadlines:

| Model | Routing slug | Accounting provider |
| --- | --- | --- |
| `openai/gpt-6-astra` | `azure` | Azure |
| `anthropic/claude-opus-5` | `amazon-bedrock` | Amazon Bedrock |
| `google/gemini-3.8-flash` | `google-vertex/global` | Google |
| `moonshotai/kimi-k3` | `fireworks` | Fireworks |

The desired author pool is Astra/Kimi/Opus and reviewer pool Gemini/Astra/Kimi;
required roles must pass before binding. Extra stage results remain diagnostics.
Captured OpenRouter ZDR metadata does not list the previous direct Anthropic and
Kimi Alibaba routes. Bedrock advertises the required structured-output parameter;
Opus Vertex does not in this capture. These catalogue facts define candidates, and
live requests determine capability. Do not inherit Vercel's Opus 8,192 condition.
Base provider slugs can match ordinary regional variants; retain every matching
endpoint row and conservative tier prices. They are provider-level pins, not proof
of one physical endpoint. Gemini's explicit global slug excludes flex/priority.

Private preflight material is under
`/private/tmp/temnia-openrouter-evaluation-20260911/`. The candidate file SHA-256 is
`767f3591ceee68dba125415524766db565efaa73296293214067181f6ec37bd3`.
The 16-request conservative exposure is 19,151,153 micros ($19.151153), not a charge
or a new spending cap. Offline request captures pass all 16 shapes; final source and
image parity are required before live dispatch. A halted unknown is retained with
its unstarted cohort slots, never retried implicitly.

Use full Karma as exposed development material. New OpenRouter preparations get new
stable IDs and qualified snapshots; they do not resume the old unknown. Astra/Azure
may support the closest transport comparison. Opus and Kimi can change upstream
provider, so disclose that additional factor. Streaming is an explicit configuration
change. Keep source/transcript/rubric/semantic prompts/PySceneDetect AdaptiveDetector
fixed. Execute qualified author arms with an available different-family reviewer
through rendering, retaining initial selections, omissions, repairs, failures,
expenses and delivered outputs. Do not retry failures to obtain favorable samples.

## Verification and operations

### First live admission findings and second declared condition

The first cohort stopped after five requests on September 11 at 17:45 UTC. All
four Astra/Azure requests received conclusive HTTP 404 parameter-admission errors.
The captured Azure endpoints advertise `max_completion_tokens`, whereas the adapter
sent `max_tokens`. Bedrock, Vertex global and Fireworks advertise `max_tokens`.
The error body does not identify the rejected parameter, but the full request versus
endpoint comparison establishes that one gateway-wide encoding does not fit this
roster. Preserve these four failures; do not relabel them as timeouts or model errors.

Opus/Bedrock returned a complete response with an early generation ID. Accounting
returned the catalogue's dated canonical model slug, whereas its request and SDK
response used the request alias. Generation ID and provider agree. The raw receipt
reports $0.040225; the original identity-failed journal retains its 992,173-micro
reservation and zero settled total. A separate offline check rejects the returned
prose as invalid JSON. Correct accounting alone cannot make that output qualify.
All 11 unstarted slots and original files remain intact. Official OpenRouter
[structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs)
warns that endpoint enforcement can vary; catalogue support does not establish
native enforcement. The captured metadata contains no transformed upstream request,
so the specific cause of the prose remains unknown.

Fix the complete observed contract before further admission. Add
`gateway-transport/2`, requiring an explicit `output_token_parameter` of
`max_tokens` or `max_completion_tokens`. Add catalogue-bound `accounting_model`
to routes (`accountingModel` in candidates), required for OpenRouter transport v2.
Keep request aliases: all four public alias lookups returned 200 with their frozen
canonical slug; canonical-slug model lookups returned 404. Request and SDK identity
stay the alias; generation accounting must match the explicit canonical identity.
Version 1 preserves its original token encoding and alias-accounting interpretation,
with new absent fields omitted so its saved identities do not change. Bind both new
fields through qualification, costs, operation/cassette identity and comparison.
The full role route identity retains the provider-required output-parameter spelling;
shared execution factors retain gateway/mode/deadlines and effective output counts.
Changing API spelling alone does not change the shared token allowance.
Do not add fuzzy model matching, aliases learned from a paid response, or a general
reconciliation framework. Original failed journals are not rewritten or reused as
passing proofs.

The second condition keeps Astra/Azure, Gemini/Vertex global and Kimi/Fireworks,
whose remaining first-cohort requests did not run, and tests **Opus/Google Vertex**
as a different provider condition. Vertex's captured ZDR endpoints omit the
`structured_outputs` advertisement; probe actual native requests before declaring
support or excluding the provider. Do not repeat the failed Bedrock author request
under unchanged wire settings. All four routes retain high effort, 32,768 output,
streaming and 300/540-second deadlines. Derive the output-token key and canonical
accounting identity from the captured endpoint/model records and retain exact
variant pricing. Qualified three-family role pools remain mandatory. A new native
failure remains evidence, not a reason to silently weaken privacy or source/schema
validation. Re-run image/request parity for the changed adapter before dispatch.

### Declared full-source comparison after Opus admission failure

At 18:15 UTC the second cohort has settled passes for all four Astra and Gemini
stages. Opus/Vertex returned four conclusive HTTP 404 parameter-admission failures;
Kimi qualification is still running. The original Astra/Kimi/Opus experiment remains
unavailable. Declare a separate **Astra versus Kimi** development comparison,
conditional on all four Kimi stages also passing and the production `/4` binder
validating all twelve Astra/Kimi/Gemini proofs against both complete original reports
and their raw receipts. Preserve the original three-arm preparation scaffold.

Use qualified Astra/Kimi/Gemini author pools and Gemini/Astra/Kimi reviewer pools,
with the desired author first and Gemini reserved as the fixed independent reviewer
in both arms. Three families, including an open-weight family, remain eligible in
each pool; policy does not require three evaluated author arms. Do not add a Gemini
author arm with a different reviewer to the matched model comparison. No failed or
unknown request is relabeled or replayed to produce this experiment. Gemini and Kimi
are admitted by actual native-stage results, not by their role in this declaration.

Both arms retain the full source, generic brief, rubric, semantic prompts, effective
32,768 output allowance, high effort, explicit streaming 300/540-second deadlines,
AdaptiveDetector, and existing programme's 32-dispatch/one-repair configuration.
Set the same run admission allowance to **44,503,040 micros** in both the experiment
and worker settings. This is derived from that programme's maximum request envelope,
not an arbitrary experiment cap or expected charge: the existing payload reservation
bound gives Astra 14,417,920, Kimi 2,297,856 and Gemini 522,240 micros per request;
at most two author calls plus the remaining thirty reviewer calls yield Astra's
44,503,040 and Kimi's 20,262,912. The inherited 41,881,600 allowance would be below
the declared Astra envelope. Two arms therefore admit at most $89.00608 of estimated
model exposure under these assumptions; actual expenses and unknown reservations
remain authoritative, and media/storage costs are separate. No runtime limit changes.

Execute one arm at a time on the eight-vCPU staging host. Existing immutable shot
and speech evidence keys match the current detector/runtime and may be reused by
the normal workflow; source/transcript verification and assembled evidence are
still performed normally. Recheck current scoped source/transcript object bytes,
actual image, prepared identities, qualification and sole queue pollers before
each start. Preserve initial author selections separately from review and repairs,
then render eligible outputs and export all failures and expenses. Compare missed
discussions against the separately frozen assistant source inventory only as a
diagnostic; it is neither human gold nor material for model prompts. Human labels
and playback acceptance remain required for an editorial winner.

### Additional configuration declared before inspecting candidate output

At 18:49 UTC, Astra's full-source arm has failed on a reported mid-stream network
error, with no completed response. Its separate accounting receipt reports zero
charge; the original unknown-response fence remains. Kimi's author has completed
and settled $0.247932, and Gemini review has begun. No candidate content from that
run has been inspected when declaring this additional condition.

Run one **Gemini-author/Kimi-reviewer** full-Karma configuration after the existing
Kimi arm finishes. This provides another potentially complete configuration for
editorial diagnosis without replaying Astra, tuning from candidate output, or
changing the original two-arm model-swap comparison. Preserve that original
comparison and its failed Astra sample. This additional comparison explicitly
changes both model roles; it cannot isolate an author-model effect.

Reuse the already qualified routes, unchanged native schemas/prompts, high effort,
32,768 effective output, streaming 300/540-second deadlines, full source/transcript,
generic brief, rubric, detector and deterministic media path. There is no new paid
qualification request. A new snapshot has Gemini/Astra/Kimi propose and summary
pools, and Kimi/Astra/Gemini verify pool. The existing resolver reserves Kimi and
selects Gemini; both pools retain three families and an open-weight member. Rebind
all twelve stage proofs through the unchanged production `/4` binder against both
complete original reports. No failed/unknown route result is reinterpreted.

Create a separate packet and stable experiment/run IDs through the existing
operator. Set its run and worker allowance to **71,755,776 micros**, derived as the
maximum over the existing 32-dispatch/one-repair programme: one Gemini request at
522,240 plus thirty-one Kimi requests at 2,297,856. Two Gemini requests plus thirty
Kimi reviews is lower, at 69,980,160. This is conservative model admission exposure,
not a forecast charge, a new discretionary cap, or a change in dispatch/repair
limits. Actual charges and unknown reservations remain authoritative.

Use the native evaluator's `configuration` mode, explicitly allowing
`author_identity`, `reviewer_identity` and `execution_identity` changes, including
that different price-derived allowance. Validate every other observed factor from
the actual exported bundles. Keep normal editorial precision/opportunity-recall
metrics; missing human labels produce null measurements and explicit reasons, not
a score or promotion. The original two-arm `model_swap` report is unchanged.

Run sequentially after the prior owned worker is terminal and stopped. Recheck the
same image, new prepared identity, current transcript bytes and sole queue pollers.
Preserve original proposals, review/repair lineage, actual media, failures and costs.
Provider metadata establishes no distinct Astra provider with the required ZDR
evidence in the captured catalogue, so this condition uses existing qualified roles
rather than assuming an unqualified substitute provider is available.

- Test exact serialized OpenRouter requests and realistic SSE responses through the
  installed SDK: keep-alives, role/usage-only frames, early ID before content, duplicate
  terminal usage, partial EOF, conflicting IDs, mid-stream errors and cancellation.
- Test generation lookup identity and reported charge, missing/zero/nonfinite cost,
  wrong provider/model, and unknown preservation. Test with the real PostgreSQL ledger
  that early handles survive a timeout, one dispatch is made and settled reuse costs
  zero new calls. Reuse existing ownership and unknown-outcome tests.
- Test old snapshot/cassette byte identity, new transport mismatch at boot, cross-mode
  and cross-gateway proof/reuse refusal, original halted reports, and evaluation factors.
- Run strict type/lint checks, exact image/programme parity, and the complete clean-commit
  repository gate before verified PR delivery. Rajesh owns merging. Original checkout
  log edits remain untouched. The session log records actual results and drafts only.
- Verify the saved staging key and running image independently; a dashboard save or
  merge is not proof of rollout. Read only presence/identity publicly; credentials stay
  in server memory. Use isolated queues, exact worker image, source-scoped preparation
  and existing progress/terminal checks. Stop owned idle/terminal workers gracefully
  and retain containers, manifests, receipts and unknown reservations.

Independent human opportunity labels, cold/source judgments and playback acceptance
remain pending. Model-created inventories and critics remain hypotheses. No transport
success, schema test or single recording establishes a winning model or improved
publication quality. The outcome record will distinguish delivered development
outputs from completed human editorial acceptance.
