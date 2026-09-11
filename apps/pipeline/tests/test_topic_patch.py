"""Human portfolio corrections preserve source grounding and atomic operation ownership."""

# pyright: reportPrivateUsage=false
from uuid import uuid4

import pytest
from pydantic import ValidationError

from temnia_pipeline.contracts import (
    Scope,
    TopicCandidate,
    TopicEditorialPatchInput,
    TopicProposal,
    TopicSelectionDraft,
)
from temnia_pipeline.harness.topic_patch import apply_human_topic_patch, remap_human_opportunities
from temnia_pipeline.harness.topic_selection import validate_selection
from temnia_pipeline.harness.validators import HarnessValidationError
from test_topic_compiler import _candidate, _case


def patch(
    kind: str, parents: list[str], children: list[TopicCandidate], **changes: object
) -> TopicEditorialPatchInput:
    return TopicEditorialPatchInput.model_validate(
        {
            "action": "topic_edit",
            "version": 1,
            "runId": uuid4(),
            "sourceId": _case().sourceId,
            "scope": Scope(organizationId=uuid4(), userId=uuid4()),
            "mutationKey": uuid4(),
            "baseRevision": 1,
            "baseEditSha256": "a" * 64,
            "evidenceSha256": "b" * 64,
            "reason": "The source contains a more useful complete discussion.",
            "correctionActiveSeconds": 12,
            "correctionMeasurementMethod": "focused_editor",
            "operations": [
                {
                    "operationId": "operation",
                    "kind": kind,
                    "affectedCandidateIds": parents,
                    "replacementCandidates": children,
                }
            ],
            **changes,
        }
    )


def proposal() -> TopicProposal:
    return TopicProposal(
        version=1,
        summary="Useful source discussions.",
        candidates=[_candidate("one", 0, 1), _candidate("two", 2, 3)],
    )


@pytest.mark.parametrize(
    ("kind", "parents", "children", "expected"),
    [
        ("add", [], [_candidate("new", 1, 2)], {"one", "two", "new"}),
        ("drop", ["one"], [], {"two"}),
        ("merge", ["one", "two"], [_candidate("merged", 0, 3)], {"merged"}),
        ("split", ["one"], [_candidate("a", 0, 0), _candidate("b", 1, 1)], {"a", "b", "two"}),
        ("adjust_extent", ["one"], [_candidate("one", 0, 2)], {"one", "two"}),
    ],
)
def test_operations_preserve_unaffected_source_choices(
    kind: str, parents: list[str], children: list[TopicCandidate], expected: set[str]
) -> None:
    previous = proposal()
    before = previous.model_dump_json()
    result = apply_human_topic_patch(_case(), previous, patch(kind, parents, children))
    assert {candidate.id for candidate in result.proposal.candidates} == expected
    assert previous.model_dump_json() == before
    for candidate in previous.candidates:
        if candidate.id not in parents:
            assert candidate in result.proposal.candidates
    assert result.lineage == {candidate.id: tuple(parents) for candidate in children}


def test_retitle_cannot_change_annotations_or_content() -> None:
    original = proposal()
    changed = original.candidates[0].model_copy(update={"title": "A more faithful title"})
    result = apply_human_topic_patch(_case(), original, patch("retitle", ["one"], [changed]))
    assert result.proposal.candidates[0].title == changed.title
    foreign_change = changed.model_copy(update={"lastSentenceId": "s000002"})
    with pytest.raises(HarnessValidationError, match="title correction cannot"):
        apply_human_topic_patch(_case(), original, patch("retitle", ["one"], [foreign_change]))


def test_invalid_second_operation_does_not_apply_first() -> None:
    previous = proposal()
    before = previous.model_dump_json()
    command = patch("drop", ["one"], [])
    body = command.model_dump(mode="json")
    body["operations"].append(
        {
            "operationId": "second",
            "kind": "drop",
            "affectedCandidateIds": ["missing"],
            "replacementCandidates": [],
        }
    )
    with pytest.raises(HarnessValidationError, match="absent parent"):
        apply_human_topic_patch(_case(), previous, TopicEditorialPatchInput.model_validate(body))
    assert previous.model_dump_json() == before


def test_missing_completion_annotations_are_not_invented() -> None:
    child = _candidate("new", 0, 2).model_copy(update={"completionSpans": []})
    with pytest.raises(ValidationError, match="completionSpans"):
        TopicEditorialPatchInput.model_validate(
            {
                **patch("add", [], [_candidate("new", 0, 2)]).model_dump(mode="json"),
                "operations": [
                    {
                        "operationId": "bad",
                        "kind": "add",
                        "affectedCandidateIds": [],
                        "replacementCandidates": [child.model_dump(mode="json")],
                    }
                ],
            }
        )
    child = _candidate("one", 1, 1).model_copy(
        update={"coreSpans": proposal().candidates[0].coreSpans}
    )
    with pytest.raises(HarnessValidationError):
        apply_human_topic_patch(_case(), proposal(), patch("adjust_extent", ["one"], [child]))


def test_new_identity_and_measured_effort_are_validated() -> None:
    with pytest.raises(HarnessValidationError, match="new stable identities"):
        apply_human_topic_patch(
            _case(), proposal(), patch("merge", ["one", "two"], [_candidate("one", 0, 3)])
        )
    with pytest.raises(HarnessValidationError, match="measurement method"):
        apply_human_topic_patch(
            _case(), proposal(), patch("drop", ["one"], [], correctionMeasurementMethod=None)
        )


def test_human_added_opportunity_is_grounded_and_unassessed() -> None:
    previous = TopicSelectionDraft(
        proposal=TopicProposal(version=1, summary="No initial videos.", candidates=[]),
        opportunities=[],
    )
    command = patch("add", [], [_candidate("new", 0, 3)])
    result = apply_human_topic_patch(_case(), previous.proposal, command)
    draft = remap_human_opportunities(previous, result, command)
    validate_selection(_case(), draft)
    assert draft.opportunities[0].candidateIds == ["new"]
    assert "unassessed" in draft.opportunities[0].dispositionReason
