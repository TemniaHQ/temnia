# Standalone-topic quality evaluation

The topic evaluator consumes frozen source-scoped artifacts and independent human
labels. Reporting does not call a model, render media, connect to the database or
reconcile a provider charge. Export uses the configured scope resolver and read-only
database/object-store access. The existing chapter evaluator remains unchanged.

## Commands

Run these from `apps/pipeline` in the already synchronized Python environment:

```sh
.venv/bin/temnia-eval topics --help
.venv/bin/temnia-harness export-topic-bundle --help

.venv/bin/temnia-harness export-topic-bundle \
  --run-id RUN_UUID --split held_out --recording-group EPISODE_GROUP \
  --output /private/tmp/topic-evaluation/bundle.json

.venv/bin/temnia-eval topics labels-template \
  --bundle /private/tmp/topic-evaluation/bundle.json \
  --output /private/tmp/topic-evaluation/human-labels.json

.venv/bin/temnia-eval topics validate \
  --bundle /private/tmp/topic-evaluation/bundle.json \
  --labels /private/tmp/topic-evaluation/human-labels.json

.venv/bin/temnia-eval topics report \
  --bundle /private/tmp/topic-evaluation/bundle.json \
  --labels /private/tmp/topic-evaluation/human-labels.json \
  --output /private/tmp/topic-evaluation/report.json

.venv/bin/temnia-eval topics compare \
  --manifest /private/tmp/topic-evaluation/comparison.json \
  --output /private/tmp/topic-evaluation/comparison-report.json
```

`RUN_UUID` and `EPISODE_GROUP` are operator-supplied identities, not literal values.
Export requires `PIPELINE_DATABASE_URL` and the existing storage configuration;
credentials remain environment-only. Reports are written atomically with private
permissions. Report output cannot overwrite an input file. Bundles can contain
private source sentences and belong in authorized private artifact storage, not Git.

`--labels` is optional for validation/reporting. Without it, the report explicitly
leaves human quality and opportunity recall unmeasured. Do not populate missing
measurements with zeros to make a report appear complete.

## Records and labels

Wire keys are camelCase; schemas are produced by the strict Python models in
`evals/topics.py`. `TopicEvaluationBundle` includes all relevant v1/v2 topic records,
immutable revisions, review-event payloads, and every physical attempt, including
failures and unknown outcomes. JSON bodies are hash-checked against their canonical
artifact identity. Binary media and provider responses remain immutable references;
the exporter neither downloads videos into JSON nor emits provider reasoning.

For a v2 workflow, `rubricSha256` identifies the actual retained rubric. V1 had no
rubric artifact: its exporter instead hashes the explicit legacy audience context
`{policy, authorBrief: run.brief, reviewerAudience: "interested general viewer"}`.
The author brief comes from the persisted run, and the audience wording comes from
the v1 cold-review prompt. This derived identity does not claim that v1 used the
new shared audience-rubric contract. The external Karma receipt lacks a separately
retained brief/rubric and therefore does not receive this derived workflow identity.

Controlled new runs also freeze the complete `evaluationProgram` and its canonical
SHA-256 in the run wrapper before dispatch. This names the implementation build and
every intended prompt/native-schema stage, including conditional repair. The exporter
uses this roster for programme, prompt and schema comparison factors and retains
actual observed call identities separately. Historical runs without it keep those
intended factors unknown; successful-response subsets are not a substitute. Export
refuses available runtime IDs/hashes that conflict with the declared roster.
The full route snapshot and original run configuration remain in the bundle; the
route snapshot ID is excluded only from non-route execution settings, so author
substitution is not counted again as an execution-policy change. Source master SHA
comes from source-bound evidence metadata or an agreeing pinned source identity;
conflicting source identities refuse export.

`TopicHumanLabels` names the exact source, evidence, rubric, recording group and
split. An opportunity annotator identifies useful discussions from the whole source
independently of the proposed portfolio. Each opportunity records a viewer purpose,
core and mandatory source spans, worthwhile/unsuitable/unknown decision and reason.
Model-created development annotations are explicitly separate from human labels.
`opportunitiesComplete` and `independentSourceReview` must describe actual work,
including when the editor concludes that the source has no worthwhile opportunities.

Candidate judgments bind the full candidate hash, exact edit hash and topic render
descriptor hash. They identify cold and source-aware annotators separately and record
prior source/variant exposure, full playback, actual observed modalities, audience
value, comprehension, completion, fidelity, title and media quality. A reader who
already knows the episode cannot be silently treated as a cold viewer. Unassisted
acceptance requires full media inspection, independent cold/source observations,
critical passes and no editorial correction. Partial or contaminated observations
remain in the labels but do not count as qualified acceptance.

The label-template command copies actual source/rubric/candidate/output identities,
with unassigned annotators, empty opportunity labels and every judgment unknown.
It does not copy source speech or claim that an empty opportunity inventory is complete.
Assign separate cold and source-aware annotators before observation. The opportunity
annotator works from the full source before seeing any model inventory. Then enter
human opportunities and semantic matches; never copy the model inventory as gold.
For a transcript-only pass set `observedModalities: ["text"]`,
`fullSelectedTextReviewed: true` only after the complete selected text was read,
`fullPlayback: false`, `mediaQuality: "unknown"` and `decision: "unknown"`.
An incomplete reading leaves `fullSelectedTextReviewed` false. Text-only observations
can measure the editorial choice but cannot qualify the actual video for publication.

Opportunity matches are explicit human semantic decisions. The evaluator checks
that claimed mandatory spans actually lie in the matched candidate; matching by
purpose remains an editorial judgment. Containment alone never grants recall credit.
Stage losses require retained artifact evidence; an unfinished author run cannot
establish a discovery miss. Reviewer calibration names the actual retained criterion
(for example `cold.intelligibleBeginning` or `cold.value.deliveredValue`) and rejects
an asserted model decision that differs from the saved assessment.

Repair labels preserve before/after candidate hashes and patch artifacts. Human edits
preserve base/result edit hashes and operation; actively measured seconds require
their measurement method. Evaluation overhead is recorded separately. Review-event
timestamps are not a substitute for active editing time.

## Interpreting reports and comparisons

Every rate has a numerator, denominator, nullable value and missing-data reason.
`editorialPrecision` is editorially useful candidates divided by candidates with
complete independent editorial observations. Usefulness requires adequate/strong
audience value, comprehension/completion/fidelity/title passes and no editorial
correction; publication decision and media quality do not enter this numerator.
Complete text-only or actual-media observations can support it, with text-only
limitations called out in the report. `usefulOpportunityRecall` credits independent,
mandatory-content-preserving semantic treatments judged editorially useful, divided
by all independently labeled worthwhile opportunities. It requires complete source
labels and complete editorial evaluation of the final portfolio.

`deliveryYield` counts actual outputs with retained passing technical checks divided
by final recommendations. `publicationAcceptance` counts unassisted accepted outputs
divided by outputs with complete media judgments. It additionally requires passing
technical delivery and observation of every known source modality (including video
for video sources), complete playback, a human acceptance decision and media-quality
pass. `endToEndAcceptedYield` uses final recommendations as its denominator;
`endToEndAcceptedOpportunityRecall` uses independently worthwhile opportunities.
These end-to-end rates remain null until all final media is evaluated. Missing or
failed delivery remains visible separately even when human evaluation cannot finish.
`humanEvaluationCompletion` describes editorial observations;
`fullMediaEvaluationCompletion` describes actual media observations.

Media failures do not become editorial failures. Text-only assessment cannot make
`qualificationComplete` true. No outputs cannot produce perfect precision; a source
with no worthwhile opportunity has no recall denominator.

The reviewer report distinguishes invalid approvals divided by all human-invalid
cases from invalid approvals divided by all approvals. Good controls and rejected
outputs are necessary to interpret either measure. Costs include all known physical
attempts; unresolved exposure remains separate. A missing accepted-video denominator
does not yield zero cost per accepted video.

`TopicComparisonManifest` freezes every bundle/label canonical hash, baseline ID,
comparison mode and intended changed factors. A `model_swap` permits only the author
identity to change. `fixed_candidate_reviewer` permits only the reviewer identity
and requires identical candidate content. Other program changes are explicit
`configuration` comparisons. Missing identities, unpaired sources and changed fixed
factors are reported, not silently tolerated. Recording groups cannot cross data
splits. Repeated trials must first be explicitly grouped rather than supplied as
duplicate source/configuration rows.

Effective author/reviewer output allowances are fixed execution factors derived from
the retained run configuration and immutable route snapshot. Equal run ceilings alone
do not establish equal actual requests. V2 may use an explicit smaller route ceiling;
a comparison that changes this allowance requires `configuration` mode and declares
`execution_identity` among the changed factors. Historical missing effective settings
remain unknown and cannot establish a controlled model swap.

Paired differences use episodes as the sampling unit. A deterministic bootstrap
interval is provided for two or more measured source pairs; a one-source result has
no interval. These descriptive reports select no automatic winner. A production
qualification-ready comparison requires complete held-out evaluation for every
configuration, paired conditions, and no observed severe fidelity defects. Product
promotion still requires the predeclared quality gates and human decision.

## Freeze a paired experiment

Before running, record the episode groups/split, audience rubric, baseline and
challenger configurations, fixed factors and primary metrics in the experiment plan.
For an author substitution freeze the same evidence, program, prompts/schema,
reviewer, media detector and execution policy, and allow only `author_identity`.
Discovery/selection program changes require `configuration` and an explicit list of
changed factors. Every baseline and challenger needs the same full episode groups;
one external retained author response is not a full workflow comparison arm.

After exporting each arm and completing independent labels, the following local
script makes a runnable two-arm comparison manifest from actual records. Extend
`pairs` with each additional frozen episode pair; paths are relative to the manifest.
The comparison rejects stale hashes and fixed-factor changes. Replace the example
paths and experiment rationale before running; no inference occurs in this script.

```python
from pathlib import Path
from temnia_pipeline.evals.topic_cli import private_json
from temnia_pipeline.evals.topic_comparison import ComparisonInput, TopicComparisonManifest
from temnia_pipeline.evals.topics import TopicEvaluationBundle, TopicHumanLabels, digest

directory = Path("/private/tmp/topic-evaluation")
pairs = [
    ("baseline-bundle.json", "baseline-labels.json"),
    ("challenger-bundle.json", "challenger-labels.json"),
]
inputs, bundles = [], []
for bundle_path, labels_path in pairs:
    bundle = TopicEvaluationBundle.model_validate_json((directory / bundle_path).read_bytes())
    labels = TopicHumanLabels.model_validate_json((directory / labels_path).read_bytes())
    bundles.append(bundle)
    inputs.append(ComparisonInput(
        bundle=bundle_path, labels=labels_path,
        expected_bundle_sha256=digest(bundle.model_dump(mode="json", by_alias=True)),
        expected_labels_sha256=digest(labels.model_dump(mode="json", by_alias=True)),
    ))
manifest = TopicComparisonManifest(
    experiment_id="predeclared-author-substitution",
    mode="model_swap",
    baseline_configuration_id=bundles[0].configuration.configuration_id,
    allowed_changed_factors=("author_identity",),
    primary_metrics=("editorialPrecision", "usefulOpportunityRecall"),
    inputs=tuple(inputs),
    rationale="Same full episodes and fixed reviewer/program; test the author substitution.",
)
private_json(directory / "comparison.json", manifest.model_dump(mode="json", by_alias=True))
```

Use the `topics compare` command above to generate the report. A calibration
comparison instead uses `fixed_candidate_reviewer`, changes only
`reviewer_identity`, and binds the same exact candidate contents in every arm.
Reviewer-case labels should include human-valid controls and human-invalid examples,
with the actual retained criterion path, so both conditional error rates are measurable.
Set `primary_metrics=("reviewerFalseAcceptance", "reviewerFalseRejection")` for that
calibration comparison rather than pretending it is a complete author workflow trial.

## Retained external Karma author response

The September 11 OpenRouter response was an external author probe, not a settled
Temnia workflow operation. The following command verifies its original request,
response, evidence, transport and reconciled charge identities without changing the
retained originals or dispatching inference:

```sh
.venv/bin/temnia-eval topics retained-author \
  --directory /private/tmp/temnia-openrouter-karma-20260911/parameter-alias \
  --preparation-sha256 db42dd452b5f3862b204316b2defda343fbbc97964a0e79671606a7a2947d13c \
  --response-sha256 0d352e6e233f09916b57a5a3d33b2a5abb9fd449422f0f29d0e2e66e38a2b393 \
  --output /private/tmp/topic-evaluation/karma-retained-diagnostic.json
```

The result preserves the original model/provider, request/settings/native-schema
identity, source/frozen-runtime manifest, original proposal and historical charge.
It has no fabricated database run, operation or cache entry. The historical expense
is reported separately from new evaluation expense. Its status is
`unqualified_downstream_not_run`; reviewer, repair, compilation, rendering and human
acceptance are not claimed. The record identifies absent later stages; it is an
accounting/identity diagnostic, not a runnable or scored continuation, and does not
authorize a new provider route. Original provider
reasoning is not copied into the diagnostic record.

The command was actually run on September 11, with no inference or original-file
changes. The verified outputs are:

- Diagnostic: `/private/tmp/temnia-topic-quality-evaluator-20260911/karma-retained-diagnostic.json`.
- Aggregate report: `/private/tmp/temnia-topic-quality-evaluator-20260911/karma-retained-report.json`.
- Original request SHA-256: `db660eb594534f39439fd7c9b9458b6e6f61e54fbb1b8a1ed1ddb2c77d4b0709`.
- Original evidence SHA-256: `f8f65027b66d6fd13c1de150edcbd0509efaf171a8e7049034a3dcbd21aad6c1`.
- Reconciled historical charge: **$0.3996663**, recorded as **399,667 micros**
  after rounding upward to whole micros. New evaluation dispatches: **0**.
- Retained result: **9 source-grounded author candidates**; no source/cold reviewers,
  repair, compiled output, actual video inspection or measured editorial acceptance.

These are local private files, not committed portable artifacts. The expense above
covers this exact external receipt only; it does not settle earlier gateway attempts
or erase their unknown-outcome fences. The retained probe did not have a separate
audience-rubric artifact: its rubric identity remains null, and `labels-template`
correctly refuses to invent one. A later text evaluation must first declare its
evaluation rubric separately from the original request, preserve all original
request/response/evidence/candidate hashes in the derived record, and identify that
audience choice as evaluation context rather than an instruction originally sent
to the model. A complete production-quality comparison still needs the declared
downstream stages and full-media evaluation.

## Launch a controlled model comparison

The Python-only operator reuses the complete production topic workflow. Its
`prepare` command freezes qualified arms and ready source/transcript pins in a
create-only private manifest; it makes no model calls. Use the pipeline image or
the same verified checkout for preparation and execution.

```sh
python scripts/run_topic_model_experiment.py prepare --spec /private/spec.json --output /private/prepared.json
python scripts/run_topic_model_experiment.py run --prepared /private/prepared.json --arm control --case karma
python scripts/run_topic_model_experiment.py status --prepared /private/prepared.json
```

The spec contains `name`, `brief`, explicit `budgetMicros` and
`workerMaxRunBudgetMicros`, Temporal address/namespace, and `topicShotDetector`.
Each source declares `id`, the environment's `sourceId`, `sourceGroup`, `split`,
`priorExposure`, and known expected source/evidence hashes. Each arm declares
`id`, the exact `ChapterRunConfig`, `routeSnapshotPath`, `qualificationPath`,
and the expected `authorRouteId` and `reviewerRouteId`. Source scope comes from
the existing resolver. Paths are absolute within the execution environment.
Use the [experiment plan](../plans/topic-model-evaluation.md) to declare the
comparison and derive request exposure before preparing paid arms.

Preparation prints each arm's non-secret worker environment. Start the existing
worker entry point with these overrides and its existing server-side database,
storage and gateway credentials. Every arm has its own pipeline/control queues.
Verify the actual image, input fingerprint, dependency versions, configuration
and successful worker boot before `run`; a queue poller alone is insufficient.
Keep the ordinary staging worker configuration separate from the experiment.

Run and request IDs remain fixed in `prepared.json`. After an uncertain start,
inspect this same intent with `status` and reuse it with `run`; neither duplicate
nor closed workflows are restarted. A claimed database run whose Temporal
execution is absent is fenced. Status never creates a run or spends tokens.
Known later source/evidence mismatches remain visible as incomparable results.
Missing pre-run source hashes remain unknown. Export the retained run through
`topics export`, then use the labeling and comparison commands above.

## Validation evidence

```sh
.venv/bin/pytest tests/test_topic_evaluation.py tests/test_chapter_evaluation.py -q
```

These are deterministic tests of source/identity binding, missing-data semantics,
metric denominators, comparison controls, export projection and retained receipts.
They do not measure real editorial quality. Fresh full-source runs, actual media
and independent human decisions are separate required evidence.
