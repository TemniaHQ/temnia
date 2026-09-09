# Chapter harness rollout and recovery

2026-09-08. PR #24 adds the chapter workflow and an opt-in checkpointed speech provider.
This is an operating procedure, not deployment evidence. The
[implementation status](../design/harness-implementation-status-2026-09-08.md) records what has
actually passed. Shared staging remains `main`; merge and deployment are separate from running
the isolated qualification. Use the [staging runbook](staging.md) for services and access.

## Deploy the schema and worker together

Use a clean, verified commit. Migration `0003_harness_foundation.sql` adds the scoped harness
tables and source deletion fence; the web release phase applies it through the owner role.
The web and worker continue to use their restricted application/pipeline roles. Do not start
harness work before the migration has completed and both images correspond to the intended commit.

The worker serves both the pipeline queue and its derived `-control` queue in the same process
and namespace. Start and stop them together. Keep the existing namespace and pipeline queue in
agreement between web and worker; do not configure a separate control queue. Verify the running
container's environment and startup logs, not only the deployment API response. A rollback may
restore both the previous image and its previous environment.

Keep `HARNESS_ENABLED=0` until the enabled worker has a valid route snapshot and the web has the
same settings. The migration and disabled feature can deploy without a model credential.

## Enable checkpointed speech

The additive application is `temnia-speech`, protocol `temnia-speech/1`. It is separate from the
existing `temnia-media` protocol-4 ladder/transcription application. Deploy from the exact clean
checkout being qualified, from `apps/pipeline`:

```bash
uv run --frozen python -c 'from temnia_pipeline.modal_build import source_build_id; print(source_build_id())'
uv run --frozen modal deploy --env staging -m temnia_pipeline.modal_speech_app
```

Record that input fingerprint with the deployed identity. It covers packaged Python, dependency
declarations, Dockerfiles and notices; it does not pretend mutable model weights or upstream
packages are independently content-pinned. GPU receipts record the versions and model provenance
actually observed. Existing `temnia-r2` and `temnia-hf` secrets and the model volume are required.
Never copy secret values into a route snapshot, command argument, report or repository file.

Qualify an isolated short source through the actual Temporal checkpoint/ledger path before a long
recording. `scripts/qualify_checkpointed_speech.py --help` describes the bounded driver. It requires
a dedicated migrated database, isolated queue/source prefix and explicit runtime configuration;
it is not a replacement provider-dispatch path. Save source/checkpoint/build hashes, physical call
and container IDs, stage telemetry, known expense and unresolved exposure. The lost-result case
must recover under the original attempt and make no second inference dispatch. An unknown outcome
halts further qualification until reconciled.

Before enabling the new provider on shared staging, drain existing transcription work under its
current provider and retain the old application while any old call remains unresolved. Set
`TRANSCRIPTION_PROVIDER=modal-checkpointed` on the worker; the web does not select the speech
provider. Worker settings also include
`MODAL_ENVIRONMENT=staging`, `MODAL_SPEECH_APP=temnia-speech` and **the recorded
`MODAL_SPEECH_BUILD` fingerprint**. Do not rely on recomputing checkout build inputs inside the
runtime image. Verify the worker's startup identity check and a completed scoped source result.

The reviewed defaults are 6,500,000 micro-dollars per speech run, five admitted calls, a 3,600-second
stage limit and a 120-second startup allowance. The reservation rate is 1,250,000 micro-dollars per
hour for the declared L4/four-CPU/16-GiB container. These bound configured exposure, not an invoice:
unobserved startup/restart costs remain unknown. Do not raise limits merely to hide an unresolved
attempt. See [the pipeline environment example](../../apps/pipeline/.env.example).

### Opt into the versioned parallel speech path

Protocol `temnia-speech/2` adds independent recognition/alignment and raw speaker-turn
branches. A CPU operation joins the two immutable inputs; it makes no fourth GPU call.
The v1 activities, checkpoints and workflow history remain supported. Select v2 only after
the exact deployment has passed qualification; the worker does not silently upgrade a saved run.

Prepare a dedicated model volume and record every file's size and SHA-256. The benchmark
preparation script copies the existing cache through a read-only mount, strips cache locks,
tokens and transfer logs, dereferences links, and writes a content-addressed frozen directory.
The runtime mounts that volume read-only and verifies its complete manifest before loading a
model. The image also bakes the Punkt tokenizer and identifies WhisperX's bundled VAD weights
and library versions. A configured image digest that differs from the deployed identity refuses
startup; no runtime download repairs a missing model. Preserve both inventories with the run.

Deploy with `MODAL_SPEECH_V2_APP`, `MODAL_SPEECH_RESOURCE_PROFILE`,
`MODAL_SPEECH_MODEL_MANIFEST` and `MODAL_SPEECH_MODEL_VOLUME` set from those records:

```bash
uv run --frozen modal deploy --env staging -m temnia_pipeline.modal_speech_v2_app
```

The module form is required. Deploying the file path changes the remote import layout and is
rejected. Use a separate application name and frozen volume; do not replace `temnia-media`,
`temnia-speech` or the shared mutable `temnia-models` volume during qualification.

For the worker, set `MODAL_SPEECH_PROTOCOL=temnia-speech/2`, `MODAL_SPEECH_APP` to that
application, the recorded `MODAL_SPEECH_BUILD`, and the same resource/model JSON. Set
`SPEECH_EXECUTION_TOPOLOGY` explicitly to the qualified `serial` or `parallel` arrangement.
Production requires coalesced progress and 3,600-second stages plus 120-second startup; the
rate must cover the declared four- or eight-core L4/16-GiB profile. The 900-second deadline
and synchronous progress control are accepted only by the finite benchmark driver. Progress
uses `temnia-speech-v2-progress` on both sides and is diagnostic, never proof of completion.

For a controlled comparison, run `scripts/benchmark_checkpointed_speech.py --help` from
`apps/pipeline`. The driver requires an exact deployment manifest, the two frozen source
files, a dedicated migrated database and Temporal namespace, an experiment ID, queue prefix
and private output directory. `--dry-run` validates a separate plan without remote mutations.
Live admission is one-time per experiment ID, with a process-held local lock and database
advisory lock. The journal reserves each case before source creation; failed and unknown
cases halt the sequence and never return spending capacity. Do not delete its lock/journal
to restart an experiment.

Save every case report, normalized transcript, stage checkpoint, CPU assignment, coverage
artifact and physical-attempt record before deleting anything. Cancel through Temporal and
keep the worker alive until owned remote cleanup completes. If completion cannot be confirmed,
retain the calls, object prefixes and reservations for reconciliation. Only after all owned calls
are terminal may the exact qualification prefixes, applications, frozen volumes, database and
namespace be removed. A duration-based resource estimate is not an actual provider invoice.

## Enable chapter planning

Supply a qualified, immutable route JSON file to the worker and validate its canonical snapshot ID:

```bash
uv run --frozen temnia-harness routes validate /etc/temnia/harness-routes.json
```

The snapshot needs `summary`, `propose` and `verify` seats, each with at least three qualified
families including an open-weight candidate. The verifier excludes generating/repair families.
Schema acceptance is not a live privacy/transport probe or a human quality audition. Never copy
the synthetic fixture into a production snapshot or assert a probe succeeded without its evidence.

Set `HARNESS_BACKEND=gateway`, `HARNESS_ROUTE_SNAPSHOT_ID` and matching caps on both web and worker;
set `HARNESS_ROUTE_SNAPSHOT_PATH` and `AI_GATEWAY_API_KEY` only on the worker. The web never needs
that key. Enable `HARNESS_ENABLED=1` only after the worker boots successfully. Config mismatches
refuse run creation; changing a route requires a new immutable snapshot and deliberate rollout.
Already persisted runs retain their original configuration rather than silently adopting a new model.

The recorded gate backend requires `HARNESS_BACKEND=recorded`, `HARNESS_ALLOW_RECORDED=1`, an
explicit synthetic route snapshot and `HARNESS_RECORDED_FIXTURE_PATH`. The UI labels it as a test
backend. It proves mechanics and never establishes live model quality or price.

## Respond to a stopped run

| State | Response |
| --- | --- |
| `budget_paused` | Inspect spent and unresolved exposure, then raise the budget to resume the same run. Accepted operations are reused. |
| `failed` | Inspect the recorded error and fix its cause, then Retry. A terminal error must not leave a permanently active row. |
| `outcome_unknown` | Retain the attempt, handle and reservation. Reconcile provider execution before allowing another dispatch. A charge alone does not prove an execution result. |
| `cancelled` | Keep historical revisions and accepted exports. In-flight provider expense may still need reconciliation. Start a new run for new editing work. |
| `needs_review` | Listen around cuts, correct boundaries, and accept every keep/drop with required reasons. Human review resolves editorial concerns; technical failures still block export. |
| `ready` | Export the accepted revision. Later content corrections create a new revision while the prior export remains available. |

Gateway cost lookup is read-only unless `--apply` is supplied:

```bash
uv run --frozen temnia-harness reconcile-cost --run-id RUN_UUID
uv run --frozen temnia-harness reconcile-cost --run-id RUN_UUID --apply
```

The CLI uses the same generated seeded scope seam as the editing core; it accepts no arbitrary
organization flag. It requires worker credentials and records missing/nonterminal lookups rather
than redispatching. Scope, provider/model/route/generation identity and exact idempotent settlement
must match. Provider-reported, reconciled and unknown expense remain distinct.

Retained harness history prevents source deletion through the ordinary delete action. Never delete
its object prefix to work around that refusal. Cancellation, retention and provider reconciliation
must finish before a future explicit history-deletion operation can remove those dependencies.
An ordinary unreadable upload can be deleted when bounded, complete history proves that its exact
failed ingest run never scheduled an external media writer and no earlier successful probe is
recorded. A late retry that explicitly failed to claim the fenced source is also eligible when
its complete history proves no dispatch. Other failed or uncertain outcomes
remain fenced; the delete action explains that distinction and does not remove their objects.

## Evaluate the result

Export a scoped bundle containing the evidence, current edit, render descriptor, checks and editorial
verification JSON, SHA-256 identities, review events and every attempt. Media/caption bytes and full
proposal/model-response bodies are not embedded. Keep transcript-bearing bundles private.
The offline CLI accepts those facts and optional independently authored human labels:

```bash
uv run --frozen temnia-harness export-bundle --run-id RUN_UUID --output run-bundle.json
uv run --frozen temnia-harness report --bundle run-bundle.json --output run-report.json
```

Synthetic and cassette-replayed attempts are separate from live spend. Missing labels, correction
timing, unknown charges and absent results remain unmeasured. A single run cannot choose a model
winner. Preserve source-level tuning/test separation when adding human boundary labels.
