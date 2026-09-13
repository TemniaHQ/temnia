"""Shared review and proposal fixtures for the topic tests."""

# pyright: reportUnusedFunction=false, reportPrivateUsage=false
from __future__ import annotations

from temnia_pipeline.contracts import (
    TopicCandidate,
    TopicColdReview,
    TopicCriterion,
    TopicProposal,
    TopicSourceJudgment,
)
from test_topic_compiler import _span


def _criterion(status: str = "pass", *, first: int = 1) -> TopicCriterion:
    return TopicCriterion.model_validate(
        {
            "status": status,
            "reason": "This supplied passage contains the supporting evidence.",
            "evidenceSpans": [_span(first).model_dump()],
        }
    )


def _cold(identifier: str, status: str = "pass") -> TopicColdReview:
    return TopicColdReview.model_validate(
        {
            "candidateId": identifier,
            **{
                field: _criterion(status).model_dump()
                for field in (
                    "intelligibleBeginning",
                    "coherentTopic",
                    "completeDiscussion",
                    "titleFaithful",
                )
            },
        }
    )


def _source(identifier: str, status: str = "pass") -> TopicSourceJudgment:
    return TopicSourceJudgment.model_validate(
        {
            "candidateId": identifier,
            **{
                field: _criterion(status).model_dump()
                for field in ("faithfulMeaning", "completeContext", "distinctPurpose")
            },
        }
    )


def _proposal(*candidates: TopicCandidate) -> TopicProposal:
    return TopicProposal(
        candidates=list(candidates), summary="Useful source discussions.", version=1
    )
