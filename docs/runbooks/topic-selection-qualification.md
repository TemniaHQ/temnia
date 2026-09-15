# Topic route pre-flight (optional)

Since 13 September 2026 the topic-selection qualification manifest is not a gate. The
worker does not read one, there is no `bind-topics`, and there are no
`HARNESS_TOPIC_SELECTION_*` flags or paths. Admission happens in the run: the first
settled call on a snapshot, route, stage and program identity is the proof, a provider
refusal ends the run with a message naming route, stage and HTTP status, and the
charge is retained. The ledger's reservation, receipt and unknown-outcome fences are
unchanged. Decision record: the 2026-09-13 entry in `AGENTS.md`; plan:
[topic-generation-staging-360-view.md](../plans/topic-generation-staging-360-view.md).
The historical v2 admission design is kept in the
[decisions document](../design/standalone-topic-decisions-2026-09-12.md) §2.

What remains is an optional pre-flight for auditioning a **new route** before spending a
full-source run on it. V3 starts the five production stage shapes (inventory, author,
cold review, source review, patch) against a five-sentence synthetic source and records
settled cost per model round. Inventory and author declare exactly `browse_source`,
`search_source` and `read_source`. Source review declares those tools plus exactly
`inspect_candidate` and `read_media_evidence`. If a model calls them, the pre-flight executes
the local synthetic tools with production result shapes and durably accounts for each continuation
request under that logical stage. The final native schema remains present on every round.
Each indexed continuation also uses the production `topic-agent-checkpoint/1` history processor:
the next wire request contains the unchanged prompt plus one bounded checkpoint, with earlier
assistant and tool messages removed. This checks the compact request shape in addition to tool
declaration.

The synthetic source uses `topic-source-index/2`: browse starts at the episode root and then visits
every returned section in order before search and exact read. Source review also has one accepted
candidate and measured evidence fixture. A direct final answer can prove only schema/tool declaration
admission; a qualified indexed route has retained successful root, section, search and read rounds
followed by the typed final round. Qualification of source review additionally requires observed
candidate/media tool rounds when those capabilities are being relied on for a production route.

The report's `dispatchCount` is therefore model requests, not logical stages. Each logical
call retains a `rounds` list with request, response, generation and cost identities. Budget
at least five dispatches per candidate and enough additional dispatches for the expected
tool rounds. A model that answers directly can establish declaration admission but does not
prove that it can call the tools; inspect the indexed stages' retained rounds before treating
function calling as qualified. The pre-flight does not prove context capacity, long-source
behaviour, deadline fit or editorial quality; Astra passed every earlier pre-flight call and
failed two full-Karma author calls on the deadline.

`--suite topic-selection-v4` replaces the source-wide inventory shape with the exact
`topic_inventory_shard` request from `standalone-topics/4`. Its prompt carries one deterministic
target section, requires that section's complete leaf browse and uses the same search/read tools and
checkpoint compactor. The other four stages remain in the suite because program identity includes
their program version even where their prompt and schema versions are unchanged. A V3 report does
not qualify the V4 shard request. Contract:
[bounded-opportunity-inventory-2026-09-14.md](../design/bounded-opportunity-inventory-2026-09-14.md).

`--suite topic-selection-v5` retains the bounded inventory request and replaces the author shape
with `topic-selection-author-shard/1`. The synthetic assignment contains one exact section-owned
opportunity batch. The author must browse that section, use hybrid retrieval, read its evidence and
namespace every candidate to the work item. Production admission checks the answer against the
exact assignment. V3 and V4 reports do not qualify this request. Contract:
[bounded-author-packaging-2026-09-14.md](../design/bounded-author-packaging-2026-09-14.md).

A compact checkpoint is not a provider receipt or permission to replay an unknown request. Each
continuation still has its own request hash, dispatch, response and cost row. Inspect the logical
call's `rounds` and require the expected root/section/search/read/final sequence when testing tool
behavior. The checkpoint processor is documented in
[indexed-agent-checkpoints-2026-09-14.md](../design/indexed-agent-checkpoints-2026-09-14.md).
The reviewer-only tool contract is
[candidate-media-evidence-tools-2026-09-14.md](../design/candidate-media-evidence-tools-2026-09-14.md).

## Running the pre-flight

From `apps/pipeline`, with the gateway credential in the process environment only
(`OPENROUTER_API_KEY` for OpenRouter routes, `AI_GATEWAY_API_KEY` for legacy Vercel
routes):

```sh
uv run --frozen python scripts/qualify_harness_gateway.py run \
  --suite topic-selection-v3 \
  --candidates /private/tmp/topic-preflight/candidates.json \
  --journal /private/tmp/topic-preflight/journal.json \
  --receipts /private/tmp/topic-preflight/receipts \
  --report /private/tmp/topic-preflight/report.json \
  --max-exposure-micros "$REVIEWED_EXPOSURE_MICROS" \
  --max-dispatches "$REVIEWED_DISPATCHES" \
  --max-output-tokens 32768
```

For the bounded inventory program, use a new create-only directory and change the suite:

```sh
uv run --frozen python scripts/qualify_harness_gateway.py run \
  --suite topic-selection-v4 \
  --candidates /private/tmp/topic-v4-preflight/candidates.json \
  --journal /private/tmp/topic-v4-preflight/journal.json \
  --receipts /private/tmp/topic-v4-preflight/receipts \
  --report /private/tmp/topic-v4-preflight/report.json \
  --max-exposure-micros "$REVIEWED_EXPOSURE_MICROS" \
  --max-dispatches "$REVIEWED_DISPATCHES" \
  --max-output-tokens 32768
```

For bounded inventory and author packaging, use another create-only directory with
`--suite topic-selection-v5`. Keep the same reviewed exposure, dispatch and output limits; the
report records the different prompt and program identities.

```sh
uv run --frozen python scripts/qualify_harness_gateway.py run \
  --suite topic-selection-v5 \
  --candidates /private/tmp/topic-v5-preflight/candidates.json \
  --journal /private/tmp/topic-v5-preflight/journal.json \
  --receipts /private/tmp/topic-v5-preflight/receipts \
  --report /private/tmp/topic-v5-preflight/report.json \
  --max-exposure-micros "$REVIEWED_EXPOSURE_MICROS" \
  --max-dispatches "$REVIEWED_DISPATCHES" \
  --max-output-tokens 32768
```

For bounded inventory, author packaging and independent source review, use another create-only
directory with `--suite topic-selection-v6`. Its source stage is one exact local review shard; it
must browse the assigned section, inspect only every assigned candidate, search, read its deciding
speech and pass production shard admission. V3 through V5 reports do not qualify this request.
Contract:
[bounded-source-review-2026-09-14.md](../design/bounded-source-review-2026-09-14.md).

```sh
uv run --frozen python scripts/qualify_harness_gateway.py run \
  --suite topic-selection-v6 \
  --candidates /private/tmp/topic-v6-preflight/candidates.json \
  --journal /private/tmp/topic-v6-preflight/journal.json \
  --receipts /private/tmp/topic-v6-preflight/receipts \
  --report /private/tmp/topic-v6-preflight/report.json \
  --max-exposure-micros "$REVIEWED_EXPOSURE_MICROS" \
  --max-dispatches "$REVIEWED_DISPATCHES" \
  --max-output-tokens 32768
```

For bounded repair as well, use a new create-only directory with `--suite topic-selection-v7`.
Its patch stage is one exact connected finding component. It declares only `browse_source`,
`search_source` and `read_source`, uses the compact `repair` checkpoint role, and must pass the
production component validator. V3 through V6 reports do not qualify this request. Contract:
[bounded-atomic-repair-2026-09-14.md](../design/bounded-atomic-repair-2026-09-14.md).

```sh
uv run --frozen python scripts/qualify_harness_gateway.py run \
  --suite topic-selection-v7 \
  --candidates /private/tmp/topic-v7-preflight/candidates.json \
  --journal /private/tmp/topic-v7-preflight/journal.json \
  --receipts /private/tmp/topic-v7-preflight/receipts \
  --report /private/tmp/topic-v7-preflight/report.json \
  --max-exposure-micros "$REVIEWED_EXPOSURE_MICROS" \
  --max-dispatches "$REVIEWED_DISPATCHES" \
  --max-output-tokens 32768
```

The candidate catalogue is the metadata-only format (`gatewayModel`, `provider`,
`family`, capacity, ZDR claim, prices, optional `reasoningEffort`, `transport` and
`providerAccountingName` for OpenRouter). Journal, receipts and report paths are
create-only; a new directory is a new session. Do not restart a session to escape an
unknown outcome; reconcile it read-only instead:

```sh
uv run --frozen python scripts/qualify_harness_gateway.py reconcile \
  --journal /private/tmp/topic-preflight/journal.json \
  --journal-sha256 EXACT_RETAINED_JOURNAL_SHA256 \
  --report /private/tmp/topic-preflight/reconciliation.json
```

Record the report hash and settled cost in the day log when a route is added to a
snapshot. Nothing installs the report anywhere; the snapshot file and the run's own
ledger are the durable records.

## Physical evidence and recovery

V2 and V3 persist a `topic-feasible-grid/2` derivation in the source evidence before
its hash is published. At each lexical transition, derivation intersects complete
selected-word membership with measured speech exclusions and the source-relative
frame/sample grid; earliest, middle and latest feasible instants represent each
connected interval, exact rational instants are encoded in reserved candidate IDs, and
validation reproduces the entire derived inventory. V3 edits carry `topic-compiler/3`,
which assigns each speech-free transition to the preceding utterance by choosing the
latest safe source-grid instant before the next speech; at 25 fps any residue at a new
video's opening is under one frame, and selected speech is never removed to make a
pause exact. Historical evidence and earlier edits keep their original identities.
Absence of a feasible cut remains a physical failure with grounded context; it never
authorizes removing selected speech or rewriting the discussion.
