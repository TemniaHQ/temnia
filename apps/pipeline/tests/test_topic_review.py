"""Human decisions and accepted topic downloads require exact independent executions."""

# Test doubles replace persistence transport, never the validation under test.
# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRenders,
    ChapterReviewInput,
    ChapterReviewOutput,
    HarnessArtifactKind,
    HarnessArtifactRef,
    ReviewState,
    Scope,
    TopicExport,
    TopicRenderedVideo,
    TopicRenders,
)
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness import topic_review as review_module
from temnia_pipeline.harness.review import ReviewRefused
from temnia_pipeline.harness.runtime_types import (
    CommitReviewMutationResult,
    ExportRevisionRequest,
    ExportRevisionResult,
    PreparedReviewMutation,
    RenderRevisionRequest,
)
from temnia_pipeline.harness.topic_review import (
    TopicReviewActivities,
    TopicReviewWorkflow,
    apply_topic_decision,
    topic_human_states,
    topic_run_ref,
    validate_topic_command,
)
from temnia_pipeline.harness.topic_runtime import TopicContext, TopicRenderResult
from test_topic_compiler import _candidate, _case, _compile

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from temnia_pipeline.harness.activities import HarnessActivities


def command(**changes: object) -> ChapterReviewInput:
    return ChapterReviewInput.model_validate(
        {
            "action": "accept",
            "baseRevision": 1,
            "boundaryId": None,
            "budgetMicros": None,
            "mutationKey": uuid4(),
            "otherSectionId": None,
            "reason": "I listened to the full independent discussion.",
            "runId": uuid4(),
            "scope": Scope(organizationId=uuid4(), userId=uuid4()),
            "sectionId": "one",
            "sourceId": _case().sourceId,
            "targetRevision": None,
            "targetTimeMs": None,
            **changes,
        }
    )


def test_accept_and_reject_change_only_one_keep_without_changing_overlapping_extents() -> None:
    edit = _compile(_case(), _candidate("one", 0, 2), _candidate("two", 1, 3))
    original = edit.model_dump_json()
    decision = command(sourceId=edit.sourceId)
    accepted = apply_topic_decision(edit, decision)
    assert edit.model_dump_json() == original
    assert accepted.videos[1] == edit.videos[1]
    assert accepted.videos[0].candidate == edit.videos[0].candidate
    assert accepted.videos[0].edit.boundaries == edit.videos[0].edit.boundaries
    assert topic_human_states(accepted) == {
        "one": ReviewState.accepted,
        "two": ReviewState.proposed,
    }
    for before, after in zip(
        edit.videos[0].edit.sections, accepted.videos[0].edit.sections, strict=True
    ):
        if before.id != "one":
            assert before == after
    rejected = apply_topic_decision(accepted, decision.model_copy(update={"action": "reject"}))
    assert topic_human_states(rejected)["one"] == ReviewState.rejected
    assert rejected.videos[1] == edit.videos[1]


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "merge", "otherSectionId": "two"},
        {"action": "accept", "targetTimeMs": 50},
        {"action": "reject", "budgetMicros": 100},
        {"action": "cancel", "sectionId": "one"},
        {"action": "accept", "sectionId": None},
        {"reason": " "},
    ],
)
def test_topic_commands_do_not_admit_chapter_mutations(changes: dict[str, object]) -> None:
    with pytest.raises(ReviewRefused):
        validate_topic_command(command(**changes))


def test_unknown_foreign_and_duplicate_human_decisions_are_refused() -> None:
    edit = _compile(_case(), _candidate("one", 0, 2))
    accepted = apply_topic_decision(edit, command(sourceId=edit.sourceId))
    for decision in (
        command(sourceId=edit.sourceId),
        command(sourceId=uuid4()),
        command(sourceId=edit.sourceId, sectionId="unknown"),
    ):
        with pytest.raises(ReviewRefused):
            apply_topic_decision(accepted, decision)


def ref(kind: HarnessArtifactKind, body: object) -> HarnessArtifactRef:
    raw = artifacts.canonical_json(body)
    digest = hashlib.sha256(raw).hexdigest()
    return HarnessArtifactRef(
        id=uuid4(),
        kind=kind,
        fingerprint=digest,
        sha256=digest,
        sizeBytes=len(raw),
        storageKey=f"fixture/{digest}.json",
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "none",
        "execution",
        "run",
        "duration",
        "section",
        "checks_hash",
        "missing_check",
        "failed_check",
        "media_reference",
        "checks_execution_dependency",
        "checks_media_dependency",
        "checks_captions_dependency",
        "descriptor_execution_dependency",
        "descriptor_media_dependency",
        "descriptor_checks_dependency",
        "descriptor_captions_dependency",
    ],
)
async def test_acceptance_validates_the_exact_nested_execution_and_checks(  # noqa: C901, PLR0915
    monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    evidence = _case()
    portfolio = _compile(evidence, _candidate("one", 0, 2))
    video = portfolio.videos[0]
    decision = command(sourceId=evidence.sourceId)
    context = TopicContext(
        run=topic_run_ref(decision),
        evidence=ref(HarnessArtifactKind.evidence, evidence.model_dump(mode="json")),
    )
    execution = ref(HarnessArtifactKind.edit, video.edit.model_dump(mode="json"))
    check_body: dict[str, Any] = {
        "editorialReasons": [],
        "editorialStatus": "not_run",
        "editSha256": execution.sha256,
        "technicalChecks": [
            {
                "name": name,
                "status": "pass",
                "sectionId": "one",
                "message": "Pass",
                "expected": 0,
                "measured": 0,
            }
            for name in (
                "full_decode",
                "duration",
                "video_presence",
                "audio_presence",
                "caption_bounds",
            )
        ],
        "verifierFamily": None,
        "version": 1,
    }
    checks = ref(HarnessArtifactKind.checks, check_body)
    media = ref(HarnessArtifactKind.render, {"fixture": "media"})
    captions = ref(HarnessArtifactKind.render, {"fixture": "captions"})
    descriptor_body: dict[str, Any] = {
        "format": "chapter-renders/1",
        "runId": str(decision.runId),
        "editSha256": execution.sha256,
        "renders": [
            {
                "sectionId": "one",
                "editSha256": execution.sha256,
                "durationMs": 3000,
                "media": media.model_dump(mode="json"),
                "captions": captions.model_dump(mode="json"),
                "checks": checks.model_dump(mode="json"),
            }
        ],
    }
    descriptor = ref(HarnessArtifactKind.render, descriptor_body)
    dependencies = {
        checks.id: [execution.id, media.id, captions.id],
        descriptor.id: [execution.id, media.id, captions.id, checks.id],
    }
    if corruption.endswith("_dependency"):
        group, input_name, _ = corruption.split("_")
        dependent = checks if group == "checks" else descriptor
        inputs = {"execution": execution, "media": media, "captions": captions, "checks": checks}
        dependencies[dependent.id].remove(inputs[input_name].id)
    execution_body = video.edit.model_dump(mode="json")
    if corruption == "execution":
        execution_body["sections"][0]["title"] = "Changed title"
    elif corruption == "run":
        descriptor_body["runId"] = str(uuid4())
    elif corruption == "duration":
        descriptor_body["renders"][0]["durationMs"] += 1
    elif corruption == "section":
        descriptor_body["renders"][0]["sectionId"] = "two"
    elif corruption == "checks_hash":
        check_body["editSha256"] = "0" * 64
    elif corruption == "missing_check":
        check_body["technicalChecks"].pop()
    elif corruption == "failed_check":
        check_body["technicalChecks"][0]["status"] = "fail"
    bodies = {execution.id: execution_body, descriptor.id: descriptor_body, checks.id: check_body}

    def timeline_identity(_evidence: object) -> tuple[None, None, dict[str, object]]:
        return None, None, {}

    def timeline(_identity: object) -> SimpleNamespace:
        return SimpleNamespace(has_video=False, has_audio=False)

    def artifact_ref(record: SimpleNamespace) -> HarnessArtifactRef:
        return record.reference

    owner = cast(
        "HarnessActivities",
        SimpleNamespace(
            _evidence_source=timeline_identity,
            _timeline_from_identity=timeline,
            ctx=SimpleNamespace(settings=SimpleNamespace(database_url="test")),
            _artifact_ref=artifact_ref,
        ),
    )
    activities = TopicReviewActivities(owner)

    async def read(_context: TopicContext, reference: HarnessArtifactRef) -> object:
        return bodies[reference.id]

    async def retained_reference(_context: TopicContext, identifier: object) -> HarnessArtifactRef:
        selected = captions if identifier == captions.id else media
        return (
            selected.model_copy(update={"sha256": "0" * 64})
            if corruption == "media_reference"
            else selected
        )

    async def accepted_record(_database_url: str, **kwargs: object) -> SimpleNamespace:
        reference = checks if kwargs["artifact_id"] == checks.id else descriptor
        return SimpleNamespace(
            reference=reference, dependency_ids=tuple(dependencies[reference.id])
        )

    monkeypatch.setattr(artifacts, "_artifact_for_read", accepted_record)
    monkeypatch.setattr(activities.topics, "read", read)
    monkeypatch.setattr(activities, "reference", retained_reference)
    rendered = TopicRenderedVideo(candidateId="one", execution=execution, descriptor=descriptor)
    # These schemas are the real persisted reader shapes, even when graph relationships are corrupt.
    ChapterChecks.model_validate(check_body)
    ChapterRenders.model_validate(descriptor_body)
    ChapterEditSpec.model_validate(execution_body)
    if corruption == "none":
        await activities.checked_video(context, video, rendered, evidence)
    else:
        with pytest.raises(ReviewRefused):
            await activities.checked_video(context, video, rendered, evidence)


@pytest.mark.parametrize(
    ("states", "members", "ready"),
    [
        (("proposed", "proposed"), [], False),
        (("accepted", "proposed"), ["one"], False),
        (("accepted", "rejected"), ["one"], True),
        (("accepted", "accepted"), ["one", "two"], True),
        (("rejected", "rejected"), [], False),
    ],
)
async def test_export_contains_only_human_accepted_members_and_finishes_only_complete_decisions(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch, states: tuple[str, str], members: list[str], *, ready: bool
) -> None:
    evidence = _case()
    portfolio = _compile(evidence, _candidate("one", 0, 2), _candidate("two", 1, 3))
    for video, state in zip(portfolio.videos, states, strict=True):
        for section in video.edit.sections:
            if section.id == video.keptSectionId:
                section.reviewState = ReviewState(state)
    decision = command(sourceId=evidence.sourceId)
    run_ref = topic_run_ref(decision)
    context = TopicContext(
        run=run_ref, evidence=ref(HarnessArtifactKind.evidence, evidence.model_dump(mode="json"))
    )
    edit_ref = ref(HarnessArtifactKind.edit, portfolio.model_dump(mode="json"))
    renders = TopicRenders(
        format="topic-renders/1",
        runId=decision.runId,
        editSha256=edit_ref.sha256,
        videos=[
            TopicRenderedVideo(
                candidateId=video.candidate.id,
                execution=ref(HarnessArtifactKind.edit, video.edit.model_dump(mode="json")),
                descriptor=ref(HarnessArtifactKind.render, {"candidate": video.candidate.id}),
            )
            for video in portfolio.videos
        ],
    )
    descriptor_ref = ref(HarnessArtifactKind.render, renders.model_dump(mode="json"))
    request = ExportRevisionRequest(
        run=run_ref, revision=2, edit=edit_ref, descriptor=descriptor_ref
    )
    activities = TopicReviewActivities(cast("HarnessActivities", SimpleNamespace()))
    events: list[object] = []

    async def retained_context(_run: object) -> TopicContext:
        return context

    async def retained_portfolio(*_args: object) -> tuple[Any, Any, dict[str, object]]:
        return portfolio, evidence, {}

    async def retained_descriptor(*_args: object) -> tuple[HarnessArtifactRef, TopicRenders]:
        return descriptor_ref, renders

    async def current_revision(*_args: object) -> None:
        events.append("current-revision-checked")

    async def checked(*args: object) -> None:
        events.append(cast("Any", args[1]).candidate.id)

    async def publish(_context: object, **kwargs: object) -> HarnessArtifactRef:
        manifest = cast("TopicExport", kwargs["content"])
        events.append([video.candidateId for video in manifest.videos])
        return ref(HarnessArtifactKind.export, manifest.model_dump(mode="json"))

    async def accept(*_args: object) -> None:
        events.append("ready-CAS")

    monkeypatch.setattr(activities, "context", retained_context)
    monkeypatch.setattr(activities, "portfolio", retained_portfolio)
    monkeypatch.setattr(activities, "descriptor", retained_descriptor)
    monkeypatch.setattr(activities, "assert_revision", current_revision)
    monkeypatch.setattr(activities, "checked_video", checked)
    monkeypatch.setattr(activities.topics, "publish", publish)
    monkeypatch.setattr(activities, "accept_export", accept)
    result = await activities.export(request)
    assert result.ready is ready
    assert (result.artifact is not None) == bool(members)
    assert events == [
        "current-revision-checked",
        *members,
        *([members] if members else []),
        *(["ready-CAS"] if ready else []),
    ]


@pytest.mark.parametrize("outcome", ["refused", "partial", "ready"])
async def test_review_program_dispatches_registered_activities_with_exact_revision(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Exercise the real workflow program at its dispatch boundary, without a service."""
    decision = command()
    run_ref = topic_run_ref(decision)
    edit = ref(HarnessArtifactKind.edit, {"fixture": "edit"})
    descriptor = ref(HarnessArtifactKind.render, {"fixture": "descriptor"})
    evidence = ref(HarnessArtifactKind.evidence, {"fixture": "evidence"})
    output = ChapterReviewOutput.model_validate(
        {
            "message": "Decision recorded.",
            "mutationKey": decision.mutationKey,
            "revision": 2,
            "runId": decision.runId,
            "state": "refused" if outcome == "refused" else "applied",
        }
    )
    calls: list[tuple[str, object, object]] = []

    async def dispatch(name: str, request: object, **kwargs: object) -> object:
        calls.append((name, request, kwargs.get("task_queue")))
        if name == "prepare_topic_review":
            return PreparedReviewMutation(
                request=decision,
                candidate=edit if outcome != "refused" else None,
                evidence=evidence,
                message="Decision prepared.",
            )
        if name == "commit_chapter_review":
            return CommitReviewMutationResult(
                output=output,
                render=RenderRevisionRequest(run=run_ref, edit=edit, revision=2)
                if outcome != "refused"
                else None,
            )
        if name == "render_topic_revision":
            assert request == RenderRevisionRequest(run=run_ref, edit=edit, revision=2)
            return TopicRenderResult(descriptor=descriptor, technical_passed=True, count=2)
        if name == "export_topic_revision":
            assert request == ExportRevisionRequest(
                run=run_ref, edit=edit, revision=2, descriptor=descriptor
            )
            return ExportRevisionResult(artifact=None, ready=outcome == "ready")
        if name == "update_chapter_run_stage":
            assert cast("Any", request).expected_revision == 2
            assert cast("Any", request).expected_stage == "render"
            return None
        raise AssertionError(name)

    def info() -> SimpleNamespace:
        return SimpleNamespace(task_queue="topics", workflow_id="review", run_id="execution")

    monkeypatch.setattr(review_module.workflow, "info", info)
    monkeypatch.setattr(review_module.workflow, "execute_activity", dispatch)
    assert await TopicReviewWorkflow().program(decision) == output
    names = [(name, queue) for name, _, queue in calls]
    expected = [("prepare_topic_review", None), ("commit_chapter_review", "topics-control")]
    if outcome != "refused":
        expected.extend([("render_topic_revision", None), ("export_topic_revision", None)])
    if outcome == "partial":
        expected.append(("update_chapter_run_stage", "topics-control"))
    assert names == expected


def test_topic_review_control_registration_matches_workflow_dispatch() -> None:
    owner = cast("HarnessActivities", SimpleNamespace())
    handler = TopicReviewActivities(owner)
    assert [
        cast("Any", activity._Definition).must_from_callable(item).name
        for item in handler.activities()
    ] == ["prepare_topic_review", "export_topic_revision"]
    assert [
        cast("Any", activity._Definition).must_from_callable(item).name
        for item in handler.control_activities()
    ] == ["apply_topic_operational_review"]


@pytest.mark.parametrize(
    "corruption",
    [
        "none",
        "portfolio_assessment_dependency",
        "assessment_proposal_dependency",
        "assessment_metadata_format",
        "assessment_metadata_run",
        "proposal_sha",
    ],
)
async def test_editorial_lineage_keeps_exact_resolvable_original_references(
    monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    decision = command()
    evidence = ref(HarnessArtifactKind.evidence, {"source": "evidence"})
    proposal = ref(HarnessArtifactKind.proposal, {"source": "proposal"})
    assessment = ref(HarnessArtifactKind.checks, {"source": "assessment"})
    edit = ref(HarnessArtifactKind.edit, {"source": "portfolio"})
    context = TopicContext(run=topic_run_ref(decision), evidence=evidence)
    metadata: dict[str, object] = {
        "proposalArtifactId": str(proposal.id),
        "assessmentArtifactId": str(assessment.id),
        "proposalSha256": proposal.sha256,
    }
    records = {
        proposal.id: SimpleNamespace(
            reference=proposal,
            dependency_ids=[evidence.id],
            metadata={"format": "topic-proposal/1", "runId": str(decision.runId)},
        ),
        assessment.id: SimpleNamespace(
            reference=assessment,
            dependency_ids=[evidence.id, proposal.id],
            metadata={"format": "topic-assessment/1", "runId": str(decision.runId)},
        ),
        edit.id: SimpleNamespace(
            reference=edit,
            dependency_ids=[evidence.id, proposal.id, assessment.id],
            metadata=metadata,
        ),
    }
    if corruption == "portfolio_assessment_dependency":
        records[edit.id].dependency_ids.remove(assessment.id)
    elif corruption == "assessment_proposal_dependency":
        records[assessment.id].dependency_ids.remove(proposal.id)
    elif corruption == "assessment_metadata_format":
        records[assessment.id].metadata["format"] = "chapter-checks/1"
    elif corruption == "assessment_metadata_run":
        records[assessment.id].metadata["runId"] = str(uuid4())
    elif corruption == "proposal_sha":
        metadata["proposalSha256"] = "0" * 64

    def artifact_ref(record: SimpleNamespace) -> HarnessArtifactRef:
        return record.reference

    owner = cast(
        "HarnessActivities",
        SimpleNamespace(
            ctx=SimpleNamespace(settings=SimpleNamespace(database_url="test")),
            _artifact_ref=artifact_ref,
        ),
    )
    handler = TopicReviewActivities(owner)

    async def accepted_record(_database_url: str, **kwargs: object) -> SimpleNamespace:
        return records[cast("Any", kwargs["artifact_id"])]

    async def validate_body(lineage: TopicContext) -> None:
        assert lineage.proposal == proposal
        assert lineage.assessment == assessment
        assert lineage.evidence == evidence

    monkeypatch.setattr(artifacts, "_artifact_for_read", accepted_record)
    monkeypatch.setattr(handler.topics, "load", validate_body)
    monkeypatch.setattr(handler.topics, "assessment", validate_body)
    if corruption == "none":
        assert await handler.editorial_lineage(context, edit, metadata) == (proposal, assessment)
    else:
        with pytest.raises(ReviewRefused):
            await handler.editorial_lineage(context, edit, metadata)


async def test_human_revision_preserves_editorial_metadata_and_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _case()
    edit = _compile(evidence, _candidate("one", 0, 2))
    decision = command(sourceId=evidence.sourceId, action="reject")
    evidence_ref = ref(HarnessArtifactKind.evidence, evidence.model_dump(mode="json")).model_copy(
        update={"id": edit.evidenceArtifactId, "sha256": edit.evidenceSha256}
    )
    context = TopicContext(run=topic_run_ref(decision), evidence=evidence_ref)
    previous = ref(HarnessArtifactKind.edit, edit.model_dump(mode="json"))
    proposal = ref(HarnessArtifactKind.proposal, {"original": "proposal"})
    assessment = ref(HarnessArtifactKind.checks, {"original": "assessment"})
    metadata: dict[str, object] = {
        "format": "topic-edit/1",
        "runId": str(decision.runId),
        "proposalArtifactId": str(proposal.id),
        "assessmentArtifactId": str(assessment.id),
        "proposalSha256": proposal.sha256,
        "compilerRefusals": ["Retained physical finding"],
        "mutationKey": str(uuid4()),
        "revision": 1,
    }
    captured: list[dict[str, object]] = []

    def artifact_ref(record: SimpleNamespace) -> HarnessArtifactRef:
        return record.reference

    owner = cast(
        "HarnessActivities",
        SimpleNamespace(
            ctx=SimpleNamespace(settings=SimpleNamespace(database_url="test"), store=object()),
            _artifact_ref=artifact_ref,
        ),
    )
    handler = TopicReviewActivities(owner)

    async def retained_context(_run: object) -> TopicContext:
        return context

    async def retained_portfolio(*_args: object) -> tuple[Any, Any, dict[str, object]]:
        return edit, evidence, metadata

    async def retained_reference(*_args: object) -> HarnessArtifactRef:
        return previous

    async def fetchone() -> dict[str, object]:
        return {"artifact_id": previous.id}

    async def execute(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(fetchone=fetchone)

    @asynccontextmanager
    async def scoped(*_args: object) -> AsyncGenerator[SimpleNamespace]:
        yield SimpleNamespace(execute=execute)

    async def publish(_database_url: str, **kwargs: object) -> SimpleNamespace:
        captured.append(kwargs)
        return SimpleNamespace(reference=ref(HarnessArtifactKind.edit, kwargs["content"]))

    monkeypatch.setattr(handler, "context", retained_context)
    monkeypatch.setattr(handler, "portfolio", retained_portfolio)
    monkeypatch.setattr(handler, "reference", retained_reference)
    monkeypatch.setattr(db, "scoped", scoped)
    monkeypatch.setattr(artifacts, "publish_json", publish)
    result = await handler.prepare(decision)
    assert result.candidate is not None
    assert len(captured) == 1
    assert captured[0]["metadata"] == {
        **metadata,
        "revision": 2,
        "mutationKey": str(decision.mutationKey),
    }
    assert captured[0]["dependency_ids"] == (
        evidence_ref.id,
        previous.id,
        proposal.id,
        assessment.id,
    )
    published = cast("dict[str, Any]", captured[0]["content"])
    assert published["videos"][0]["edit"]["sections"][0]["reviewState"] == "rejected"
