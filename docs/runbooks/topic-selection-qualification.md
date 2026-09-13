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
full-source run on it. It dispatches the five exact production request shapes
(inventory, author, cold review, source review, patch) against a five-sentence synthetic
source and records settled cost per call. It proves request shape, strict native-schema
admission and cost observation. It does not prove context capacity, long-source
behaviour, deadline fit or editorial quality; Astra passed every pre-flight call and
failed two full-Karma author calls on the deadline.

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

The candidate catalogue is the metadata-only format (`gatewayModel`, `provider`,
`family`, capacity, ZDR claim, prices, optional `reasoningEffort`, `transport` and
`providerAccountingName` for OpenRouter). Journal, receipts and report paths are
create-only; a new directory is a new session. Five calls per candidate. Do not restart
a session to escape an unknown outcome; reconcile it read-only instead:

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
