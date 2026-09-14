"""Strict offline records for independent-topic evaluation, never inference.

These Python-only evaluation files consume the public generated topic contracts.
They do not replace the TypeScript/Python runtime schema owner.
"""

# Validation errors deliberately identify the failed invariant without source text.
# ruff: noqa: C901, EM101, PLR0912, PLR0915, TC003, TRY003, TRY004

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRenders,
    HarnessEvidence,
    TopicCandidate,
    TopicEditorialRubric,
    TopicEditSpec,
    TopicExport,
    TopicProposal,
    TopicRenders,
    TopicSelectionAssessment,
    TopicSelectionRecord,
    TopicSentenceSpan,
)
from temnia_pipeline.evals.common import SHA256, AttemptFact, EvaluationModel, JSONValue
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.routes import RouteSnapshot
from temnia_pipeline.harness.topic_compiler import validate_topic_edit, validate_topic_proposal
from temnia_pipeline.harness.topic_editorial import editorial_routes
from temnia_pipeline.harness.topic_selection_runtime import effective_topic_output_tokens
from temnia_pipeline.harness.validators import validate_evidence

type Split = Literal["development", "held_out", "qualification"]
type Stage = Literal["discovery", "selection", "repair", "compilation", "rendering", "human"]
Nonnegative = Annotated[int, Field(ge=0)]
Identifier = Annotated[str, Field(min_length=1)]


def digest(value: object) -> str:
    """Hash canonical JSON, not a pretty-printed export file."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def editorial_identity(program: Mapping[str, object] | None) -> dict[str, object] | None:
    """The programme without its implementation build: what a resumed run must still speak.

    Prompts, schemas and the policy decide whether retained work and new work belong to
    one run. The build that produced them is provenance; every deploy changes it, and a
    run stopped on a defect is resumed precisely after the deploy that fixes it.
    """
    if program is None:
        return None
    return {key: value for key, value in program.items() if key != "implementationSha256"}


class TopicArtifact(EvaluationModel):
    """One immutable artifact and its source-scoped dependency closure."""

    id: UUID
    source_id: UUID
    sha256: SHA256
    fingerprint: SHA256
    kind: Identifier
    size_bytes: Nonnegative
    storage_key: str | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    dependencies: tuple[UUID, ...] = ()
    body: dict[str, JSONValue] | None = None
    body_status: Literal["included", "binary_reference", "retained_reference"] = "included"


class TopicProgramStage(EvaluationModel):
    """One intended native call shape, including stages that a run may never need."""

    seat: Literal["author", "reviewer"]
    prompt_version: Identifier
    prompt_template_sha256: SHA256
    schema_version: Identifier
    native_schema_sha256: SHA256


class TopicProgramManifest(EvaluationModel):
    """Frozen runtime identity captured before dispatch, never inferred from current code."""

    format: Literal["temnia-topic-evaluation-program/1"] = "temnia-topic-evaluation-program/1"
    policy: Literal[
        "standalone-topics/3",
        "standalone-topics/4",
        "standalone-topics/5",
        "standalone-topics/6",
        "standalone-topics/7",
    ]
    implementation_sha256: SHA256
    program_version: Identifier
    stages: Annotated[dict[Identifier, TopicProgramStage], Field(min_length=1)]

    @model_validator(mode="after")
    def _complete_roster(self) -> Self:
        expected = {
            "topic_inventory_shard"
            if self.policy
            in {
                "standalone-topics/4",
                "standalone-topics/5",
                "standalone-topics/6",
                "standalone-topics/7",
            }
            else "topic_inventory": "reviewer",
            "topic_author": "author",
            "topic_cold": "reviewer",
            "topic_source": "reviewer",
            "topic_patch": "author",
        }
        if {name: stage.seat for name, stage in self.stages.items()} != expected:
            raise ValueError(
                "intended topic programme requires its complete five-stage seat roster"
            )
        return self


class TopicConfiguration(EvaluationModel):
    """Unknown deployment details stay null; no vendor is implicitly selected."""

    configuration_id: Identifier
    policy: Literal[
        "standalone-topics/3",
        "standalone-topics/4",
        "standalone-topics/5",
        "standalone-topics/6",
        "standalone-topics/7",
    ]
    source_sha256: SHA256 | None = None
    transcript_sha256: SHA256 | None = None
    rubric_sha256: SHA256 | None = None
    route_snapshot_sha256: SHA256 | None = None
    route_snapshot: dict[str, JSONValue] | None = None
    program_identity: dict[str, JSONValue] = Field(default_factory=dict)
    author_identity: dict[str, JSONValue] = Field(default_factory=dict)
    reviewer_identity: dict[str, JSONValue] = Field(default_factory=dict)
    prompt_identity: dict[str, JSONValue] = Field(default_factory=dict)
    schema_identity: dict[str, JSONValue] = Field(default_factory=dict)
    media_identity: dict[str, JSONValue] = Field(default_factory=dict)
    execution_identity: dict[str, JSONValue] = Field(default_factory=dict)
    intended_program: TopicProgramManifest | None = None
    intended_program_sha256: SHA256 | None = None
    observed_call_identities: tuple[dict[str, JSONValue], ...] = ()
    retained_run_configuration: dict[str, JSONValue] = Field(default_factory=dict)


def effective_output_projection(
    _policy: str,
    run_configuration: dict[str, JSONValue],
    route_snapshot: dict[str, JSONValue] | None,
) -> dict[str, JSONValue]:
    """Derive role allowances from complete frozen inputs, never current worker defaults."""
    unknown: dict[str, JSONValue] = {"author": None, "reviewer": None}
    requested = run_configuration.get("maxOutputTokens")
    if (
        type(requested) is not int
        or requested <= 0
        or route_snapshot is None
        or not {"version", "snapshot_id", "routes", "seats"} <= route_snapshot.keys()
    ):
        return unknown
    snapshot = RouteSnapshot.model_validate_json(canonical_json(route_snapshot), strict=True)
    if (
        run_configuration.get("routeSnapshotId") is not None
        and run_configuration["routeSnapshotId"] != snapshot.snapshot_id
    ):
        raise ValueError("effective output inputs name different frozen route snapshots")
    if not {"propose", "verify"} <= snapshot.seats.keys():
        return unknown
    author, reviewer = editorial_routes(snapshot)
    return {
        name: effective_topic_output_tokens(requested, route)
        for name, route in (("author", author), ("reviewer", reviewer))
    }


def known_effective_outputs(configuration: TopicConfiguration) -> bool:
    """An old absent/null projection is readable but cannot establish a fixed setting."""
    value = configuration.execution_identity.get("effectiveOutputTokens")
    return (
        isinstance(value, dict)
        and set(value) == {"author", "reviewer"}
        and all(type(allowance) is int and allowance > 0 for allowance in value.values())
    )


def transport_projection(route_snapshot: dict[str, JSONValue] | None) -> dict[str, JSONValue]:
    """Project only frozen explicit transport facts, leaving legacy process settings unknown."""
    unknown: dict[str, JSONValue] = {"author": None, "reviewer": None}
    if (
        route_snapshot is None
        or not {"version", "snapshot_id", "routes", "seats"} <= route_snapshot.keys()
    ):
        return unknown
    snapshot = RouteSnapshot.model_validate_json(canonical_json(route_snapshot), strict=True)
    if not {"propose", "verify"} <= snapshot.seats.keys():
        return unknown
    author, reviewer = editorial_routes(snapshot)
    return {
        # Provider-specific wire spelling stays in the complete role route identity.
        # It does not change the shared effective output allowance or request deadline.
        name: route.transport.model_dump(mode="json", exclude={"output_token_parameter"})
        if route.transport is not None
        else None
        for name, route in (("author", author), ("reviewer", reviewer))
    }


def known_transport(configuration: TopicConfiguration) -> bool:
    """Both intended roles must carry an explicit, source-bound transport projection."""
    value = configuration.execution_identity.get("transport")
    return (
        isinstance(value, dict)
        and set(value) == {"author", "reviewer"}
        and all(isinstance(policy, dict) for policy in value.values())
        and value == transport_projection(configuration.route_snapshot)
    )


def _validate_transport(configuration: TopicConfiguration) -> None:
    if "transport" in configuration.execution_identity and configuration.execution_identity[
        "transport"
    ] != transport_projection(configuration.route_snapshot):
        raise ValueError("transport projection contradicts its frozen route configuration")


def _validate_effective_outputs(configuration: TopicConfiguration) -> None:
    """Refuse explicit projection contradictions; do not populate archived export bytes."""
    if "effectiveOutputTokens" not in configuration.execution_identity:
        return
    declared = configuration.execution_identity["effectiveOutputTokens"]
    if (
        not isinstance(declared, dict)
        or set(declared) != {"author", "reviewer"}
        or any(
            value is not None and (type(value) is not int or value <= 0)
            for value in declared.values()
        )
        or declared
        != effective_output_projection(
            configuration.policy,
            configuration.retained_run_configuration,
            configuration.route_snapshot,
        )
    ):
        raise ValueError("effective output projection contradicts its frozen configuration")
    if configuration.retained_run_configuration and configuration.execution_identity.get(
        "config"
    ) != {
        key: value
        for key, value in configuration.retained_run_configuration.items()
        if key != "routeSnapshotId"
    }:
        raise ValueError("execution configuration contradicts retained run settings")


def program_identity_projections(
    manifest: TopicProgramManifest,
) -> tuple[dict[str, JSONValue], dict[str, JSONValue], dict[str, JSONValue]]:
    """Derive fixed factors from one frozen complete roster, never observed call counts."""
    return (
        {
            "policy": manifest.policy,
            "programVersion": manifest.program_version,
            "implementationSha256": manifest.implementation_sha256,
        },
        {
            name: {"version": stage.prompt_version, "templateSha256": stage.prompt_template_sha256}
            for name, stage in manifest.stages.items()
        },
        {
            name: {
                "version": stage.schema_version,
                "nativeSchemaSha256": stage.native_schema_sha256,
            }
            for name, stage in manifest.stages.items()
        },
    )


def validate_program_observation(
    manifest: TopicProgramManifest | None, stage: str, metadata: dict[str, JSONValue]
) -> None:
    """Known observed IDs and available template/schema hashes must fit their intended seat."""
    if manifest is None:
        return
    if (
        metadata.get("programVersion") is not None
        and metadata["programVersion"] != manifest.program_version
    ):
        raise ValueError("observed runtime programme differs from frozen programme")
    fields = {
        "promptVersion": "prompt_version",
        "schemaVersion": "schema_version",
        "promptTemplateSha256": "prompt_template_sha256",
        "nativeSchemaSha256": "native_schema_sha256",
    }
    observed = {key: metadata[key] for key in fields if metadata.get(key) is not None}
    seat = "reviewer" if stage.startswith("verify") else "author"
    if observed and not any(
        candidate.seat == seat
        and all(value == getattr(candidate, fields[key]) for key, value in observed.items())
        for candidate in manifest.stages.values()
    ):
        raise ValueError("observed prompt or schema differs from intended programme roster")


class TopicAttempt(EvaluationModel):
    """A physical attempt with an explicit retained or current origin."""

    stage: Identifier
    fact: AttemptFact
    origin: Literal["current_run", "historical_retained"] = "current_run"
    request_sha256: SHA256 | None = None
    receipt_sha256: SHA256 | None = None


class RetainedExpense(EvaluationModel):
    """An external receipt is not a Temnia database operation or attempt."""

    external_attempt_id: Identifier
    generation_id: Identifier
    response_sha256: SHA256
    receipt_sha256: SHA256
    actual_cost_micros: Nonnegative | None
    estimated_exposure_micros: Nonnegative
    usage: dict[str, Nonnegative]


class TopicRevision(EvaluationModel):
    """One immutable source-scoped edit revision."""

    revision: Annotated[int, Field(gt=0)]
    artifact_id: UUID
    parent_revision: Nonnegative | None = None
    created_at: datetime


class TopicReviewEvent(EvaluationModel):
    """A replayable human command outcome; elapsed time is not effort."""

    id: UUID
    action: Identifier
    state: Literal["applied", "conflict", "refused"]
    base_revision: Nonnegative
    resulting_revision: Annotated[int, Field(gt=0)] | None = None
    created_at: datetime
    payload: dict[str, JSONValue] = Field(default_factory=dict)


class StageObservation(EvaluationModel):
    """Observed stage completion backed by retained artifacts."""

    stage: Stage
    status: Literal["complete", "partial", "failed", "unknown", "not_run"]
    artifact_sha256s: tuple[SHA256, ...] = ()
    candidate_sha256s: tuple[SHA256, ...] = ()
    reason: Identifier


class TopicEvaluationBundle(EvaluationModel):
    """A frozen portable source/run closure, including incomplete work."""

    format: Literal["temnia-topic-evaluation-bundle/1"] = "temnia-topic-evaluation-bundle/1"
    source_id: UUID
    recording_group: Identifier
    source_fingerprint: SHA256 | None = None
    duration_ms: Nonnegative
    run_id: UUID | None
    observed_at: datetime
    split: Split
    mode: Literal["workflow", "retained_author_diagnostic"] = "workflow"
    status: Identifier
    current_revision: Nonnegative = 0
    accepted_revision: Annotated[int, Field(gt=0)] | None = None
    configuration: TopicConfiguration
    evidence_sha256: SHA256 | None = None
    evidence: HarnessEvidence | None = None
    artifacts: tuple[TopicArtifact, ...] = ()
    revisions: tuple[TopicRevision, ...] = ()
    review_events: tuple[TopicReviewEvent, ...] = ()
    attempts: tuple[TopicAttempt, ...] = ()
    stages: tuple[StageObservation, ...] = ()
    final_candidate_sha256s: tuple[SHA256, ...] = ()
    final_selection_sha256: SHA256 | None = None
    final_edit_sha256: SHA256 | None = None
    final_renders_sha256: SHA256 | None = None
    retained_proposal: TopicProposal | None = None
    retained_identity: dict[str, JSONValue] | None = None
    retained_expense: RetainedExpense | None = None
    limitations: tuple[str, ...] = ()


class OpportunityLabel(EvaluationModel):
    """An independent source-level editorial label, separate from model hypotheses."""

    id: Identifier
    viewer_purpose: Identifier
    worthwhile: bool | None
    core_spans: Annotated[tuple[TopicSentenceSpan, ...], Field(min_length=1)]
    mandatory_spans: tuple[TopicSentenceSpan, ...] = ()
    reason: Identifier
    annotator: Identifier
    independently_labeled: bool
    source_fully_reviewed: bool
    origin: Literal["human", "model_development"] = "human"


class CandidateJudgment(EvaluationModel):
    """Adjudicated decision with separately identified cold and source observations."""

    candidate_sha256: SHA256
    rubric_sha256: SHA256
    edit_sha256: SHA256 | None
    render_descriptor_sha256: SHA256 | None
    decision: Literal["accept", "reject", "unknown"]
    editorial_changes: Literal["none", "title_only", "content_boundary", "major", "unknown"]
    cold_annotator: Identifier
    source_annotator: Identifier
    cold_prior_source_exposure: bool
    cold_prior_variant_exposure: bool
    full_playback: bool
    full_selected_text_reviewed: bool = False
    source_fully_reviewed: bool
    observed_modalities: tuple[Literal["text", "audio", "video"], ...]
    audience_value: Literal["strong", "adequate", "weak", "unknown"]
    comprehension: Literal["pass", "fail", "unknown"]
    completion: Literal["pass", "fail", "unknown"]
    fidelity: Literal["pass", "fail", "unknown"]
    title_faithful: Literal["pass", "fail", "unknown"]
    media_quality: Literal["pass", "fail", "unknown"]
    severe_fidelity_defect: bool | None
    redundant_core: bool | None
    reason: Identifier


class OpportunityMatch(EvaluationModel):
    """Human semantic matching with explicit mandatory-content checks."""

    opportunity_id: Identifier
    candidate_sha256: SHA256
    independently_usable: bool
    mandatory_content_present: bool
    annotator: Identifier
    reason: Identifier


class OpportunityLoss(EvaluationModel):
    """An adjudicated stage loss with traceable evidence."""

    opportunity_id: Identifier
    stage: Stage
    artifact_sha256s: Annotated[tuple[SHA256, ...], Field(min_length=1)]
    candidate_sha256: SHA256 | None = None
    annotator: Identifier
    reason: Identifier


class ReviewerCase(EvaluationModel):
    """A fixed-candidate judgment paired with an independent human label."""

    candidate_sha256: SHA256
    judgment_artifact_sha256: SHA256
    criterion: Identifier
    human_valid: bool | None
    model_decision: Literal["pass", "fail", "unknown"]
    annotator: Identifier
    reason: Identifier


class RepairLabel(EvaluationModel):
    """Grounded before/after adjudication of repair success and regression."""

    before_candidate_sha256: SHA256
    after_candidate_sha256s: tuple[SHA256, ...]
    patch_artifact_sha256: SHA256
    failure_resolved: bool | None
    unaffected_regression: bool | None
    annotator: Identifier
    reason: Identifier


class HumanEditLabel(EvaluationModel):
    """An exact-revision correction with actively measured effort."""

    event_id: UUID | None = None
    base_edit_sha256: SHA256
    resulting_edit_sha256: SHA256 | None
    operation: Literal["adjust_extent", "retitle", "add", "drop", "merge", "split"]
    candidate_sha256s: tuple[SHA256, ...]
    active_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    measurement_method: str | None
    reason: Identifier


class TopicHumanLabels(EvaluationModel):
    """Source-bound independent labels and candidate adjudications."""

    format: Literal["temnia-topic-human-labels/1"] = "temnia-topic-human-labels/1"
    source_id: UUID
    evidence_sha256: SHA256
    rubric_sha256: SHA256
    recording_group: Identifier
    split: Split
    opportunities_complete: bool
    opportunity_annotator: Identifier
    independent_source_review: bool
    opportunities: tuple[OpportunityLabel, ...] = ()
    judgments: tuple[CandidateJudgment, ...] = ()
    matches: tuple[OpportunityMatch, ...] = ()
    losses: tuple[OpportunityLoss, ...] = ()
    reviewer_cases: tuple[ReviewerCase, ...] = ()
    repairs: tuple[RepairLabel, ...] = ()
    human_edits: tuple[HumanEditLabel, ...] = ()
    evaluation_active_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


def topic_label_template(bundle: TopicEvaluationBundle) -> TopicHumanLabels:
    """Freeze actual candidate identities while leaving all human observations unmeasured."""
    validate_topic_bundle(bundle)
    if bundle.evidence_sha256 is None or bundle.configuration.rubric_sha256 is None:
        raise ValueError("human-label template requires retained evidence and rubric identities")
    artifacts = {artifact.sha256: artifact for artifact in bundle.artifacts}
    edit_artifact = artifacts.get(bundle.final_edit_sha256 or "")
    edit = artifact_model(edit_artifact) if edit_artifact else None
    edited: set[str] = (
        {digest(video.candidate.model_dump(mode="json")) for video in edit.videos}
        if isinstance(edit, TopicEditSpec)
        else set()
    )
    renders_artifact = artifacts.get(bundle.final_renders_sha256 or "")
    renders = artifact_model(renders_artifact) if renders_artifact else None
    rendered: set[str] = (
        {video.candidateId for video in renders.videos}
        if isinstance(renders, TopicRenders)
        else set()
    )
    candidates = candidate_index(bundle)
    return TopicHumanLabels(
        source_id=bundle.source_id,
        evidence_sha256=bundle.evidence_sha256,
        rubric_sha256=bundle.configuration.rubric_sha256,
        recording_group=bundle.recording_group,
        split=bundle.split,
        opportunities_complete=False,
        opportunity_annotator="unassigned-opportunity-annotator",
        independent_source_review=False,
        judgments=tuple(
            CandidateJudgment(
                candidate_sha256=sha,
                rubric_sha256=bundle.configuration.rubric_sha256,
                edit_sha256=bundle.final_edit_sha256 if sha in edited else None,
                render_descriptor_sha256=bundle.final_renders_sha256
                if sha in edited and candidates[sha].id in rendered
                else None,
                decision="unknown",
                editorial_changes="unknown",
                cold_annotator="unassigned-cold-annotator",
                source_annotator="unassigned-source-annotator",
                cold_prior_source_exposure=False,
                cold_prior_variant_exposure=False,
                full_playback=False,
                source_fully_reviewed=False,
                observed_modalities=(),
                audience_value="unknown",
                comprehension="unknown",
                completion="unknown",
                fidelity="unknown",
                title_faithful="unknown",
                media_quality="unknown",
                severe_fidelity_defect=None,
                redundant_core=None,
                reason="Not evaluated; replace only with observed human judgments.",
            )
            for sha in bundle.final_candidate_sha256s
        ),
    )


def artifact_model(artifact: TopicArtifact) -> object | None:
    """Dispatch public records by their real format."""
    if artifact.body is None:
        return None
    format_name = artifact.body.get("format") or artifact.metadata.get("format")
    models = {
        "topic-selection/2": TopicSelectionRecord,
        "topic-selection-assessment/2": TopicSelectionAssessment,
        "topic-renders/1": TopicRenders,
        "topic-export/1": TopicExport,
        "topic-proposal/1": TopicProposal,
        "topic-edit/1": TopicEditSpec,
        "chapter-edit/1": ChapterEditSpec,
        "chapter-renders/1": ChapterRenders,
        "chapter-checks/1": ChapterChecks,
        "topic-rubric/1": TopicEditorialRubric,
    }
    model = models.get(str(format_name))
    return model.model_validate(artifact.body) if model is not None else None


def candidate_index(bundle: TopicEvaluationBundle) -> dict[str, TopicCandidate]:
    """Index every retained candidate by exact content identity across revisions."""
    candidates: dict[str, TopicCandidate] = {}
    for artifact in bundle.artifacts:
        model = artifact_model(artifact)
        if isinstance(model, TopicSelectionRecord):
            rows = model.draft.proposal.candidates
        elif isinstance(model, TopicProposal):
            rows = model.candidates
        elif isinstance(model, TopicEditSpec):
            rows = [video.candidate for video in model.videos]
        else:
            continue
        for candidate in rows:
            candidates[digest(candidate.model_dump(mode="json"))] = candidate
    if bundle.retained_proposal is not None:
        for candidate in bundle.retained_proposal.candidates:
            candidates[digest(candidate.model_dump(mode="json"))] = candidate
    return candidates


def _verify_reference(reference: object, by_id: dict[UUID, TopicArtifact]) -> TopicArtifact:
    from temnia_pipeline.contracts import HarnessArtifactRef  # noqa: PLC0415

    if not isinstance(reference, HarnessArtifactRef):
        raise ValueError("descriptor reference is malformed")
    artifact = by_id.get(reference.id)
    if artifact is None or (
        artifact.sha256,
        artifact.fingerprint,
        artifact.kind,
        artifact.size_bytes,
        artifact.storage_key,
    ) != (
        reference.sha256,
        reference.fingerprint,
        reference.kind.value,
        reference.sizeBytes,
        reference.storageKey,
    ):
        raise ValueError("descriptor reference differs from its immutable artifact")
    return artifact


def validate_topic_bundle(bundle: TopicEvaluationBundle) -> None:
    """Reject corrupt/scoped/stale input before computing any quality metric."""
    by_id = {artifact.id: artifact for artifact in bundle.artifacts}
    if len(by_id) != len(bundle.artifacts):
        raise ValueError("duplicate artifact identity")
    hashes = {artifact.sha256 for artifact in bundle.artifacts}
    for artifact in bundle.artifacts:
        if artifact.source_id != bundle.source_id:
            raise ValueError("artifact belongs to another source")
        if not set(artifact.dependencies) <= by_id.keys():
            raise ValueError("artifact dependency closure is incomplete")
        if artifact.body_status == "included":
            if artifact.body is None or digest(artifact.body) != artifact.sha256:
                raise ValueError("artifact canonical body hash differs")
        elif artifact.body is not None:
            raise ValueError("reference-only artifact unexpectedly contains a body")
        model = artifact_model(artifact)
        if isinstance(model, TopicEditSpec):
            if bundle.evidence is None or bundle.evidence_sha256 is None:
                raise ValueError("compiled topic lacks its original evidence")
            validate_topic_edit(
                bundle.evidence, model, expected_evidence_sha256=bundle.evidence_sha256
            )
        if isinstance(model, TopicRenders):
            edit_artifact = next(
                (item for item in bundle.artifacts if item.sha256 == model.editSha256), None
            )
            edit = artifact_model(edit_artifact) if edit_artifact else None
            if model.runId != bundle.run_id or not isinstance(edit, TopicEditSpec):
                raise ValueError("topic renders lack their exact run/edit")
            if {video.candidateId for video in model.videos} != {
                video.candidate.id for video in edit.videos
            }:
                raise ValueError("topic render descriptor does not cover its complete edit")
            for video in model.videos:
                execution = _verify_reference(video.execution, by_id)
                descriptor = _verify_reference(video.descriptor, by_id)
                chapter = artifact_model(descriptor)
                if (
                    not isinstance(chapter, ChapterRenders)
                    or chapter.editSha256 != execution.sha256
                ):
                    raise ValueError("topic video descriptor differs from execution")
                if video.candidateId not in {render.sectionId for render in chapter.renders}:
                    raise ValueError("topic video is absent from its child descriptor")
        if isinstance(model, ChapterRenders):
            if model.runId != bundle.run_id:
                raise ValueError("child renders belong to another run")
            for render in model.renders:
                if render.editSha256 != model.editSha256:
                    raise ValueError("child media descriptor names a different edit")
                _verify_reference(render.media, by_id)
                for reference in (render.checks, render.captions):
                    if reference is not None:
                        _verify_reference(reference, by_id)
        if isinstance(model, (TopicSelectionRecord, TopicSelectionAssessment)) and (
            model.runId != bundle.run_id or model.evidenceSha256 != bundle.evidence_sha256
        ):
            raise ValueError("editorial artifact belongs to another run or evidence")
        if (
            isinstance(model, TopicSelectionRecord)
            and digest(model.rubric.model_dump(mode="json")) != model.rubricSha256
        ):
            raise ValueError("selection rubric hash differs")
        if isinstance(model, TopicSelectionAssessment) and model.selectionSha256 not in hashes:
            raise ValueError("assessment selection is missing")
    if bundle.evidence is not None:
        validate_evidence(bundle.evidence)
        if (
            digest(bundle.evidence.model_dump(mode="json")) != bundle.evidence_sha256
            or bundle.evidence.sourceId != bundle.source_id
            or bundle.evidence.durationMs != bundle.duration_ms
        ):
            raise ValueError("source evidence identity differs")
        if bundle.configuration.transcript_sha256 != bundle.evidence.transcriptSha256:
            raise ValueError("configuration transcript differs from evidence")
        for artifact in bundle.artifacts:
            if artifact.sha256 != bundle.evidence_sha256:
                continue
            for field, expected in (
                ("sourceSha256", bundle.configuration.source_sha256),
                ("sourceFingerprint", bundle.evidence.sourceFingerprint),
                ("transcriptSha256", bundle.evidence.transcriptSha256),
            ):
                if (
                    expected is not None
                    and artifact.metadata.get(field) is not None
                    and artifact.metadata[field] != expected
                ):
                    raise ValueError(
                        "configuration source identity differs from retained evidence metadata"
                    )
    elif bundle.evidence_sha256 is not None:
        raise ValueError("named evidence body is unavailable")
    if bundle.configuration.route_snapshot is not None and (
        digest(bundle.configuration.route_snapshot) != bundle.configuration.route_snapshot_sha256
    ):
        raise ValueError("route snapshot canonical hash differs")
    _validate_effective_outputs(bundle.configuration)
    _validate_transport(bundle.configuration)
    intended = bundle.configuration.intended_program
    if intended is not None:
        if (
            intended.policy != bundle.configuration.policy
            or digest(intended.model_dump(mode="json", by_alias=True))
            != bundle.configuration.intended_program_sha256
        ):
            raise ValueError("intended programme differs from frozen policy or hash")
        if (
            bundle.configuration.program_identity,
            bundle.configuration.prompt_identity,
            bundle.configuration.schema_identity,
        ) != program_identity_projections(intended):
            raise ValueError("projected programme factors contradict the frozen manifest")
        for observed in bundle.configuration.observed_call_identities:
            stage, metadata = observed.get("stage"), observed.get("identities")
            if not isinstance(stage, str) or not isinstance(metadata, dict):
                raise ValueError("observed programme identity has an invalid stage or metadata")
            validate_program_observation(intended, stage, metadata)
    elif bundle.configuration.intended_program_sha256 is not None:
        raise ValueError("intended programme body is absent")
    candidates = candidate_index(bundle)
    if bundle.evidence is not None:
        for candidate in candidates.values():
            validate_topic_proposal(
                bundle.evidence,
                TopicProposal(version=1, candidates=[candidate], summary="Evaluation"),
            )
    if len(set(bundle.final_candidate_sha256s)) != len(bundle.final_candidate_sha256s):
        raise ValueError("duplicate final candidate")
    if not set(bundle.final_candidate_sha256s) <= candidates.keys():
        raise ValueError("final candidate is absent from retained proposals")
    for value in (
        bundle.final_edit_sha256,
        bundle.final_renders_sha256,
        bundle.final_selection_sha256,
    ):
        if (
            value is not None
            and value not in hashes
            and not (
                value == bundle.final_selection_sha256
                and bundle.retained_proposal is not None
                and digest(bundle.retained_proposal.model_dump(mode="json")) == value
            )
        ):
            raise ValueError("final artifact is missing")
    revisions = {revision.revision: revision for revision in bundle.revisions}
    if len(revisions) != len(bundle.revisions):
        raise ValueError("duplicate revision")
    if bundle.current_revision and bundle.current_revision not in revisions:
        raise ValueError("current revision is absent")
    if bundle.accepted_revision is not None and bundle.accepted_revision not in revisions:
        raise ValueError("accepted revision is absent")
    for revision in bundle.revisions:
        if revision.artifact_id not in by_id:
            raise ValueError("revision artifact is absent")
    if (
        bundle.current_revision
        and by_id[revisions[bundle.current_revision].artifact_id].sha256 != bundle.final_edit_sha256
    ):
        raise ValueError("final edit is not the current revision")
    if len({attempt.fact.id for attempt in bundle.attempts}) != len(bundle.attempts):
        raise ValueError("duplicate physical attempt")
    for observation in bundle.stages:
        if (
            not set(observation.artifact_sha256s) <= hashes
            or not set(observation.candidate_sha256s) <= candidates.keys()
        ):
            raise ValueError("stage observation lacks retained evidence")
    if bundle.mode == "retained_author_diagnostic" and (
        bundle.run_id is not None
        or bundle.current_revision
        or bundle.retained_proposal is None
        or bundle.retained_identity is None
    ):
        raise ValueError("retained diagnostic cannot impersonate a workflow run")


def validate_topic_labels(bundle: TopicEvaluationBundle, labels: TopicHumanLabels) -> None:
    """Refuse cross-source labels, stale media and unsupported semantic matches."""
    if (labels.source_id, labels.evidence_sha256, labels.recording_group, labels.split) != (
        bundle.source_id,
        bundle.evidence_sha256,
        bundle.recording_group,
        bundle.split,
    ):
        raise ValueError("labels belong to another source, evidence or split")
    if labels.rubric_sha256 != bundle.configuration.rubric_sha256:
        raise ValueError("labels belong to another audience rubric")
    candidates = candidate_index(bundle)
    artifacts = {artifact.sha256: artifact for artifact in bundle.artifacts}
    opportunities = {opportunity.id: opportunity for opportunity in labels.opportunities}
    if len(opportunities) != len(labels.opportunities):
        raise ValueError("duplicate opportunity label; retain disagreements under distinct labels")
    positions = (
        {sentence.id: index for index, sentence in enumerate(bundle.evidence.sentences)}
        if bundle.evidence
        else {}
    )
    for opportunity in labels.opportunities:
        for span in (*opportunity.core_spans, *opportunity.mandatory_spans):
            if (
                span.firstSentenceId not in positions
                or span.lastSentenceId not in positions
                or positions[span.firstSentenceId] > positions[span.lastSentenceId]
            ):
                raise ValueError("human opportunity has a foreign or reversed source span")
    if len({judgment.candidate_sha256 for judgment in labels.judgments}) != len(labels.judgments):
        raise ValueError("duplicate adjudication; retain separate rater files before adjudication")
    for judgment in labels.judgments:
        if (
            judgment.candidate_sha256 not in candidates
            or judgment.rubric_sha256 != labels.rubric_sha256
        ):
            raise ValueError("human judgment has foreign candidate or rubric")
        if judgment.edit_sha256 is not None:
            artifact = artifacts.get(judgment.edit_sha256)
            edit = artifact_model(artifact) if artifact else None
            if not isinstance(edit, TopicEditSpec) or judgment.candidate_sha256 not in {
                digest(video.candidate.model_dump(mode="json")) for video in edit.videos
            }:
                raise ValueError("judgment candidate differs from the inspected edit")
        if judgment.render_descriptor_sha256 is not None:
            artifact = artifacts.get(judgment.render_descriptor_sha256)
            renders = artifact_model(artifact) if artifact else None
            if not isinstance(renders, TopicRenders) or renders.editSha256 != judgment.edit_sha256:
                raise ValueError("judgment media is not bound to its exact edit")
            if candidates[judgment.candidate_sha256].id not in {
                video.candidateId for video in renders.videos
            }:
                raise ValueError("judgment candidate is absent from inspected media")
        if judgment.full_playback and judgment.render_descriptor_sha256 is None:
            raise ValueError("full playback requires an exact retained media descriptor")
        if judgment.full_playback and not set(judgment.observed_modalities) & {"audio", "video"}:
            raise ValueError("text-only observation cannot claim full media playback")
        if judgment.full_selected_text_reviewed and "text" not in judgment.observed_modalities:
            raise ValueError("complete selected-text review requires an observed text modality")
        if judgment.decision == "accept" and any(
            value == "fail"
            for value in (
                judgment.comprehension,
                judgment.completion,
                judgment.fidelity,
                judgment.title_faithful,
                judgment.media_quality,
            )
        ):
            raise ValueError("acceptance contradicts a failed critical judgment")
    for match in labels.matches:
        if match.opportunity_id not in opportunities or match.candidate_sha256 not in candidates:
            raise ValueError("opportunity match has foreign identities")
        if match.mandatory_content_present:
            candidate = candidates[match.candidate_sha256]
            first, last = positions[candidate.firstSentenceId], positions[candidate.lastSentenceId]
            for span in (
                *opportunities[match.opportunity_id].core_spans,
                *opportunities[match.opportunity_id].mandatory_spans,
            ):
                if (
                    not first
                    <= positions[span.firstSentenceId]
                    <= positions[span.lastSentenceId]
                    <= last
                ):
                    raise ValueError("match claims absent mandatory source content")
    for loss in labels.losses:
        if (
            loss.opportunity_id not in opportunities
            or not set(loss.artifact_sha256s) <= artifacts.keys()
            or (loss.candidate_sha256 is not None and loss.candidate_sha256 not in candidates)
        ):
            raise ValueError("opportunity loss lacks source-bound evidence")
        if loss.stage == "discovery" and not any(
            stage.stage == "discovery" and stage.status == "complete" for stage in bundle.stages
        ):
            raise ValueError("incomplete discovery cannot establish a discovery miss")
    for case in labels.reviewer_cases:
        if (
            case.candidate_sha256 not in candidates
            or case.judgment_artifact_sha256 not in artifacts
        ):
            raise ValueError("reviewer calibration lacks exact retained inputs")
        assessment = artifact_model(artifacts[case.judgment_artifact_sha256])
        candidate_id = candidates[case.candidate_sha256].id
        if isinstance(assessment, TopicSelectionAssessment):
            cold = next(
                (item for item in assessment.coldReviews if item.candidateId == candidate_id), None
            )
            source = (
                next(
                    (
                        item
                        for item in assessment.portfolioReview.candidates
                        if item.candidateId == candidate_id
                    ),
                    None,
                )
                if assessment.portfolioReview
                else None
            )
        else:
            raise ValueError("reviewer calibration requires a retained editorial assessment")
        parts = case.criterion.split(".")
        observed = cold if parts[0] == "cold" else source if parts[0] == "source" else None
        for part in parts[1:]:
            observed = getattr(observed, part, None)
        if getattr(observed, "status", None) != case.model_decision:
            raise ValueError("calibration decision differs from the actual model assessment")
    calibration_keys = {
        (case.candidate_sha256, case.judgment_artifact_sha256, case.criterion)
        for case in labels.reviewer_cases
    }
    if len(calibration_keys) != len(labels.reviewer_cases):
        raise ValueError("duplicate calibration case would bias the confusion matrix")
    for repair in labels.repairs:
        if (
            repair.before_candidate_sha256 not in candidates
            or not set(repair.after_candidate_sha256s) <= candidates.keys()
            or repair.patch_artifact_sha256 not in artifacts
        ):
            raise ValueError("repair label lacks retained before/after evidence")
    for edit in labels.human_edits:
        if (
            edit.base_edit_sha256 not in artifacts
            or (
                edit.resulting_edit_sha256 is not None
                and edit.resulting_edit_sha256 not in artifacts
            )
            or not set(edit.candidate_sha256s) <= candidates.keys()
        ):
            raise ValueError("human edit label has foreign artifacts")
        if (edit.active_seconds is None) != (edit.measurement_method is None):
            raise ValueError("measured correction time requires its measurement method")
