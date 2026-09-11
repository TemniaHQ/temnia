"""Human corrections retain exact observations while invalidating changed editorial inputs."""

# Test doubles replace scoped persistence, not compilation or patch validation.
# pyright: reportPrivateUsage=false
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    ReviewState,
    TopicProposal,
)
from temnia_pipeline.harness.topic_compiler import (
    augment_topic_evidence,
    compile_topics,
    compile_topics_v2,
)
from temnia_pipeline.harness.topic_patch_review import (
    TopicEditorialPatchActivities,
    patch_run_ref,
)
from temnia_pipeline.harness.topic_runtime import TopicContext
from temnia_pipeline.harness.topic_selection import content_hash, make_rubric
from test_topic_compiler import _candidate, _case
from test_topic_patch import patch
from test_topic_review import ref

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from pydantic import BaseModel

    from temnia_pipeline.harness.activities import HarnessActivities


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("identity", ["matching", "stale", "hidden_id_collision"])
async def test_prepare_retains_unchanged_checks_and_human_state_without_fake_reviews(  # noqa: C901, PLR0915
    monkeypatch: pytest.MonkeyPatch, version: int, identity: str
) -> None:
    evidence = augment_topic_evidence(_case()) if version == 2 else _case()
    evidence_ref = ref(HarnessArtifactKind.evidence, evidence.model_dump(mode="json"))
    candidates = [_candidate("one", 0, 1), _candidate("two", 2, 3)]
    proposed = TopicProposal(version=1, summary="Source discussions.", candidates=candidates)
    compiler = compile_topics_v2 if version == 2 else compile_topics
    portfolio = compiler(
        evidence,
        proposed,
        evidence_artifact_id=evidence_ref.id,
        evidence_sha256=evidence_ref.sha256,
    )
    for video in portfolio.videos:
        for section in video.edit.sections:
            if section.id == video.keptSectionId:
                section.reviewState = ReviewState.accepted
    edit_ref = ref(HarnessArtifactKind.edit, portfolio.model_dump(mode="json"))
    child = candidates[0].model_copy(update={"title": "A faithful clearer title"})
    command = patch(
        "retitle",
        ["one"],
        [child],
        sourceId=evidence.sourceId,
        baseEditSha256="d" * 64 if identity == "stale" else edit_ref.sha256,
        evidenceSha256=evidence_ref.sha256,
    )
    if identity == "hidden_id_collision":
        command = patch(
            "add",
            [],
            [_candidate("declined", 0, 0)],
            sourceId=evidence.sourceId,
            baseEditSha256=edit_ref.sha256,
            evidenceSha256=evidence_ref.sha256,
        )
    context = TopicContext(run=patch_run_ref(command), evidence=evidence_ref)
    stored: dict[str, tuple[HarnessArtifactRef, Any]] = {}
    published: dict[str, tuple[Any, dict[str, Any]]] = {}

    def retain(kind: HarnessArtifactKind, body: object) -> HarnessArtifactRef:
        reference = ref(kind, body)
        stored[str(reference.id)] = reference, body
        return reference

    stored[str(edit_ref.id)] = edit_ref, portfolio.model_dump(mode="json")
    # An unrendered proposal is retained as evidence; the human did not drop it.
    complete = proposed.model_copy(
        update={"candidates": [*candidates, _candidate("declined", 0, 0)]}
    )
    criterion: dict[str, Any] = {"status": "pass", "reason": "Supported.", "evidenceSpans": []}
    cold: list[dict[str, Any]] = [
        {
            "candidateId": candidate.id,
            **dict.fromkeys(
                ("coherentTopic", "completeDiscussion", "intelligibleBeginning", "titleFaithful"),
                criterion,
            ),
        }
        for candidate in complete.candidates
    ]
    rubric = make_rubric("Find independently useful discussions.")
    rubric_ref = retain(HarnessArtifactKind.checks, rubric.model_dump(mode="json"))
    assessment: dict[str, Any]
    if version == 2:
        record = {
            "format": "topic-selection/2",
            "draft": {
                "proposal": complete.model_dump(mode="json"),
                "opportunities": [
                    {
                        "id": f"opportunity:{candidate.id}",
                        "candidateIds": [candidate.id],
                        "coreSpans": [span.model_dump() for span in candidate.coreSpans],
                        "completionSpans": [
                            span.model_dump() for span in candidate.completionSpans
                        ],
                        "valueEvidenceSpans": [span.model_dump() for span in candidate.coreSpans],
                        "requiredContextSpans": [],
                        "meaningChangingFollowups": [],
                        "viewerPurpose": candidate.purpose,
                        "disposition": "proposed",
                        "dispositionReason": "A source-grounded opportunity.",
                    }
                    for candidate in complete.candidates
                ],
            },
            "evidenceSha256": evidence_ref.sha256,
            "rubric": rubric.model_dump(),
            "rubricSha256": content_hash(rubric),
            "runId": str(command.runId),
            "origin": "model",
            "parentSelectionSha256": None,
        }
        proposal_ref = retain(HarnessArtifactKind.proposal, record)
        assessment = {
            "format": "topic-selection-assessment/2",
            "runId": str(command.runId),
            "selectionSha256": proposal_ref.sha256,
            "evidenceSha256": evidence_ref.sha256,
            "rubricSha256": content_hash(rubric),
            "coldReviews": [
                {
                    **item,
                    "value": {
                        **dict.fromkeys(
                            (
                                "viewerReasonToWatch",
                                "deliveredValue",
                                "focusedDevelopment",
                                "openingEffectiveness",
                            ),
                            criterion,
                        ),
                        "reconstructedPurpose": "A useful answer.",
                        "reconstructedTakeaway": "The answer is complete.",
                    },
                }
                for item in cold
            ],
            "portfolioReview": None,
            "findings": [],
            "executionStatus": "needs_review",
            "proposerFamily": "author",
            "verifierFamily": "reviewer",
            "reasons": [],
            "responseArtifacts": [],
        }
        metadata: dict[str, Any] = {
            "selectionArtifactId": str(proposal_ref.id),
            "selectionSha256": proposal_ref.sha256,
            "rubricArtifactId": str(rubric_ref.id),
        }
    else:
        proposal_ref = retain(HarnessArtifactKind.proposal, complete.model_dump(mode="json"))
        assessment = {
            "format": "topic-assessment/1",
            "runId": str(command.runId),
            "proposalSha256": proposal_ref.sha256,
            "evidenceSha256": evidence_ref.sha256,
            "candidates": [
                {
                    "candidateId": item["candidateId"],
                    "coldReview": item,
                    "sourceReview": None,
                    "status": "needs_review",
                    "reasons": [],
                    "physicalBoundaryIssues": [],
                }
                for item in cold
            ],
            "summary": "Retained judgments.",
            "proposerFamily": "author",
            "verifierFamily": "reviewer",
        }
        metadata = {
            "proposalArtifactId": str(proposal_ref.id),
            "proposalSha256": proposal_ref.sha256,
        }
    assessment_ref = retain(HarnessArtifactKind.checks, assessment)
    metadata["assessmentArtifactId"] = str(assessment_ref.id)
    owner = cast(
        "HarnessActivities",
        SimpleNamespace(ctx=SimpleNamespace(settings=SimpleNamespace(database_url="fixture"))),
    )
    activities = TopicEditorialPatchActivities(owner)

    async def load_context(_run: object) -> TopicContext:
        return context

    async def reference(_context: object, identifier: object) -> HarnessArtifactRef:
        return stored[str(identifier)][0]

    async def load_portfolio(*_args: object) -> tuple[Any, Any, dict[str, Any]]:
        return portfolio, evidence, metadata

    async def read(_context: object, reference: HarnessArtifactRef) -> object:
        return stored[str(reference.id)][1]

    async def publish(_context: object, **kwargs: object) -> HarnessArtifactRef:
        body = cast("BaseModel", kwargs["content"]).model_dump(mode="json")
        published[str(kwargs["format_name"])] = body, cast("dict[str, Any]", kwargs["metadata"])
        return retain(HarnessArtifactKind(kwargs["kind"]), body)

    async def fetchone() -> dict[str, object]:
        return {"artifact_id": edit_ref.id}

    async def execute(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(fetchone=fetchone)

    @asynccontextmanager
    async def scoped(*_args: object) -> AsyncGenerator[SimpleNamespace]:
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(db, "scoped", scoped)
    monkeypatch.setattr(activities.review, "context", load_context)
    monkeypatch.setattr(activities.review, "reference", reference)
    monkeypatch.setattr(activities.review, "portfolio", load_portfolio)
    monkeypatch.setattr(activities.topics, "read", read)
    monkeypatch.setattr(activities.topics, "publish", publish)
    result = await activities.prepare(command)
    if identity != "matching":
        assert result.candidate is None
        assert published == {}
        if identity == "hidden_id_collision":
            assert "new stable identities" in result.message
        return
    assert result.candidate is not None
    body, provenance = published["topic-edit/1"]
    assert body["videos"][1] == portfolio.videos[1].model_dump(mode="json")
    assert (
        next(
            section for section in body["videos"][0]["edit"]["sections"] if section["id"] == "one"
        )["reviewState"]
        == "proposed"
    )
    assert provenance["origin"] == "human"
    assert published["topic-editorial-patch/1"][1]["candidateLineage"] == {"one": ["one"]}
    if version == 2:
        selected = published["topic-selection/2"][0]
        assert selected["origin"] == "human"
        assert selected["rubric"] == rubric.model_dump(mode="json")
        assert selected["parentSelectionSha256"] == proposal_ref.sha256
        revised = published["topic-selection-assessment/2"][0]
        assert revised["portfolioReview"] is None
        assert [item["candidateId"] for item in revised["coldReviews"]] == ["two", "declined"]
    else:
        selected = published["topic-proposal/1"][0]
        assert {item["id"] for item in selected["candidates"]} == {"one", "two", "declined"}
        revised = published["topic-assessment/1"][0]
        assert all(item["sourceReview"] is None for item in revised["candidates"])
        assert (
            next(item for item in revised["candidates"] if item["candidateId"] == "one")[
                "coldReview"
            ]
            is None
        )
