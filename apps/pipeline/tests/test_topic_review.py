"""Human decisions and accepted topic downloads require exact independent executions."""

# Test doubles replace persistence transport, never the validation under test.
# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from temporalio import activity

from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRenders,
    ChapterReviewAction,
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
from temnia_pipeline.harness.rendering import kept_sections
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
from temnia_pipeline.harness.topic_selection_activities import TopicSelectionActivities
from temnia_pipeline.harness.validators import rounded_milliseconds
from test_topic_compiler import _candidate, _case, _compile

if TYPE_CHECKING:
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
    extent = kept_sections(video.edit)[0]
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
                "durationMs": rounded_milliseconds(extent.end - extent.start),
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


async def test_selection_editorial_lineage_preserves_v3_program_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = command()
    evidence = ref(HarnessArtifactKind.evidence, {"source": "evidence"})
    rubric = ref(HarnessArtifactKind.checks, {"source": "rubric"})
    selection = ref(HarnessArtifactKind.proposal, {"source": "selection"})
    assessment = ref(HarnessArtifactKind.checks, {"source": "assessment"})
    edit = ref(HarnessArtifactKind.edit, {"source": "portfolio"})
    context = TopicContext(run=topic_run_ref(decision), evidence=evidence)
    metadata: dict[str, object] = {
        "selectionArtifactId": str(selection.id),
        "assessmentArtifactId": str(assessment.id),
        "rubricArtifactId": str(rubric.id),
        "selectionSha256": selection.sha256,
        "programVersion": "standalone-topics/3",
    }
    records = {
        rubric.id: SimpleNamespace(reference=rubric, dependency_ids=[evidence.id], metadata={}),
        selection.id: SimpleNamespace(
            reference=selection,
            dependency_ids=[evidence.id, rubric.id],
            metadata={"format": "topic-selection/2", "runId": str(decision.runId)},
        ),
        assessment.id: SimpleNamespace(
            reference=assessment,
            dependency_ids=[evidence.id, rubric.id, selection.id],
            metadata={"format": "topic-selection-assessment/2", "runId": str(decision.runId)},
        ),
        edit.id: SimpleNamespace(
            reference=edit,
            dependency_ids=[evidence.id, rubric.id, selection.id, assessment.id],
            metadata=metadata,
        ),
    }

    def artifact_ref(record: SimpleNamespace) -> HarnessArtifactRef:
        return cast("HarnessArtifactRef", record.reference)

    owner = cast(
        "HarnessActivities",
        SimpleNamespace(
            ctx=SimpleNamespace(settings=SimpleNamespace(database_url="test")),
            _artifact_ref=artifact_ref,
        ),
    )
    handler = TopicReviewActivities(owner)
    observed: list[str] = []

    async def accepted_record(_database_url: str, **kwargs: object) -> SimpleNamespace:
        return records[cast("Any", kwargs["artifact_id"])]

    async def load_selection(_activities: TopicSelectionActivities, lineage: object) -> None:
        observed.append(cast("Any", lineage).program_version)

    monkeypatch.setattr(artifacts, "_artifact_for_read", accepted_record)
    monkeypatch.setattr(TopicSelectionActivities, "load", load_selection)
    monkeypatch.setattr(TopicSelectionActivities, "assessment", load_selection)

    assert await handler.editorial_lineage(context, edit, metadata) == (selection, assessment)
    assert observed == ["standalone-topics/3", "standalone-topics/3"]


def test_retry_is_an_operational_command_that_names_no_candidate() -> None:
    base = command()
    retry = base.model_copy(update={"action": ChapterReviewAction.retry, "sectionId": None})
    validate_topic_command(retry)
    with pytest.raises(ReviewRefused, match="cancellation and retry name none"):
        validate_topic_command(base.model_copy(update={"action": ChapterReviewAction.retry}))
    with pytest.raises(ReviewRefused, match="does not name this topic portfolio"):
        apply_topic_decision(cast("Any", SimpleNamespace(sourceId=retry.sourceId)), retry)
