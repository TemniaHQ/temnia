"""Existing reviewed-risk cuts are distinct from newly requested timing choices."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from fractions import Fraction
from uuid import uuid4

import pytest

from temnia_pipeline.contracts import (
    ChapterEditSpec,
    ChapterProposal,
    HarnessEvidence,
    PositiveRational,
    RationalTime,
)
from temnia_pipeline.harness.compiler import (
    PRESERVED_COMPILER_VERSION,
    compile_chapters,
    quantize_time,
)
from temnia_pipeline.harness.validators import (
    HarnessValidationError,
    rational,
    rounded_milliseconds,
)
from test_harness_editorial import EVIDENCE_ID, EVIDENCE_SHA, _case, _tail_repair
from test_harness_editorial_cut_facts import _detected


def _risky_case() -> tuple[HarnessEvidence, ChapterProposal, ChapterEditSpec]:
    evidence, proposal, _ = _case()
    evidence = _detected(evidence, ((evidence.sentences[1].startMs, evidence.sentences[2].endMs),))
    edit = compile_chapters(
        evidence, proposal, evidence_artifact_id=EVIDENCE_ID, evidence_sha256=EVIDENCE_SHA
    )
    assert "compiler_quantized_inside_detected_speech" in edit.boundaries[1].reasons
    return evidence, proposal, edit


@pytest.mark.parametrize("rate", [25, 30000])
def test_tail_repair_preserves_existing_risky_cut_exactly_and_old_constraint_still_refuses(
    rate: int,
) -> None:
    evidence, proposal, _ = _risky_case()
    evidence = evidence.model_copy(
        update={
            "frameRate": PositiveRational(numerator=rate, denominator=1001 if rate == 30000 else 1)
        }
    )
    prior = compile_chapters(
        evidence, proposal, evidence_artifact_id=EVIDENCE_ID, evidence_sha256=EVIDENCE_SHA
    )
    replacement = _tail_repair(evidence, proposal)
    replacement.sections[0].id = proposal.sections[0].id
    prior = prior.model_copy(
        update={
            "sections": [
                prior.sections[0].model_copy(update={"flags": ["retained-review-risk"]}),
                prior.sections[1],
            ]
        }
    )
    old = prior.boundaries[1]
    assert old.candidateId is not None
    result = compile_chapters(
        evidence,
        replacement,
        evidence_artifact_id=EVIDENCE_ID,
        evidence_sha256=EVIDENCE_SHA,
        preserved_proposal=proposal,
        preserved_edit=prior,
    )
    assert result.compilerVersion == PRESERVED_COMPILER_VERSION
    assert result.boundaries[1] == old
    assert result.boundaries[1].requiresReview
    assert result.sections[0].flags == ["retained-review-risk"]
    assert result.boundaries[0] == prior.boundaries[0]
    assert result.boundaries[-1].model_dump(exclude={"id"}) == prior.boundaries[-1].model_dump(
        exclude={"id"}
    )
    assert result.sections[0].model_dump(exclude={"id"}) == prior.sections[0].model_dump(
        exclude={"id"}
    )
    for preserved in (False, True):
        with pytest.raises(HarnessValidationError, match="intersects grounded speech"):
            compile_chapters(
                evidence,
                replacement,
                evidence_artifact_id=EVIDENCE_ID,
                evidence_sha256=EVIDENCE_SHA,
                boundary_constraints={
                    (
                        proposal.sections[0].lastSentenceId,
                        proposal.sections[1].firstSentenceId,
                    ): old.candidateId
                },
                preserved_proposal=proposal if preserved else None,
                preserved_edit=prior if preserved else None,
            )


@pytest.mark.parametrize(
    "corruption",
    [
        "source",
        "evidence_id",
        "evidence_hash",
        "grid",
        "candidate",
        "cut_time",
        "risk",
        "section",
        "source_edge",
        "transition",
    ],
)
def test_preservation_refuses_unrelated_or_changed_prior_pair(corruption: str) -> None:
    evidence, proposal, prior = _risky_case()
    if corruption == "source":
        prior = prior.model_copy(update={"sourceId": uuid4()})
    elif corruption == "evidence_id":
        prior = prior.model_copy(update={"evidenceArtifactId": uuid4()})
    elif corruption == "evidence_hash":
        prior = prior.model_copy(update={"evidenceSha256": "f" * 64})
    elif corruption == "grid":
        prior = prior.model_copy(update={"sourceAudioSampleRate": 44100})
    elif corruption == "section":
        proposal = proposal.model_copy(
            update={
                "sections": [
                    proposal.sections[0].model_copy(update={"title": "Changed without compiling"}),
                    proposal.sections[1],
                ]
            }
        )
    elif corruption == "transition":
        proposal = proposal.model_copy(
            update={
                "sections": [
                    proposal.sections[0].model_copy(
                        update={"lastSentenceId": evidence.sentences[2].id, "quoteWordIds": []}
                    ),
                    proposal.sections[1].model_copy(
                        update={"firstSentenceId": evidence.sentences[3].id, "quoteWordIds": []}
                    ),
                ]
            }
        )
        prior = prior.model_copy(
            update={"sections": [s.model_copy(update={"quoteWordIds": []}) for s in prior.sections]}
        )
    else:
        boundaries = list(prior.boundaries)
        if corruption == "source_edge":
            boundaries[-1] = boundaries[-1].model_copy(
                update={"candidateId": boundaries[0].candidateId}
            )
        elif corruption == "candidate":
            boundaries[1] = boundaries[1].model_copy(update={"candidateId": "foreign"})
        elif corruption == "risk":
            boundaries[1] = boundaries[1].model_copy(update={"requiresReview": False})
        else:
            time = rational(boundaries[1].time) + Fraction(1, 1000)
            boundaries[1] = boundaries[1].model_copy(
                update={
                    "time": RationalTime(numerator=time.numerator, denominator=time.denominator),
                    "timeMs": rounded_milliseconds(time),
                }
            )
        prior = prior.model_copy(update={"boundaries": boundaries})
    with pytest.raises(HarnessValidationError):
        compile_chapters(
            evidence,
            proposal,
            evidence_artifact_id=EVIDENCE_ID,
            evidence_sha256=EVIDENCE_SHA,
            preserved_proposal=proposal,
            preserved_edit=prior,
        )


def test_absent_preservation_has_identical_old_output_and_pair_is_required() -> None:
    evidence, proposal, prior = _case()
    assert (
        compile_chapters(
            evidence,
            proposal,
            evidence_artifact_id=EVIDENCE_ID,
            evidence_sha256=EVIDENCE_SHA,
            preserved_proposal=None,
            preserved_edit=None,
        ).model_dump_json()
        == prior.model_dump_json()
    )
    with pytest.raises(HarnessValidationError, match="both prior"):
        compile_chapters(
            evidence,
            proposal,
            evidence_artifact_id=EVIDENCE_ID,
            evidence_sha256=EVIDENCE_SHA,
            preserved_edit=prior,
        )


def test_preserved_cut_cannot_bypass_new_joint_quote_ownership() -> None:
    evidence, proposal, prior = _risky_case()
    proposal = proposal.model_copy(
        update={"sections": [s.model_copy(update={"quoteWordIds": []}) for s in proposal.sections]}
    )
    candidate = next(c for c in evidence.boundaries if c.timeMs == evidence.sentences[1].startMs)
    time = quantize_time(evidence, candidate.timeMs)
    boundary = prior.boundaries[1].model_copy(
        update={
            "candidateId": candidate.id,
            "time": RationalTime(numerator=time.numerator, denominator=time.denominator),
            "timeMs": rounded_milliseconds(time),
        }
    )
    prior = prior.model_copy(
        update={
            "boundaries": [prior.boundaries[0], boundary, prior.boundaries[-1]],
            "sections": [s.model_copy(update={"quoteWordIds": []}) for s in prior.sections],
        }
    )
    replacement = _tail_repair(evidence, proposal)
    replacement.sections[0].quoteWordIds = _case()[1].sections[0].quoteWordIds
    with pytest.raises(HarnessValidationError, match="no monotonic grounded candidate path"):
        compile_chapters(
            evidence,
            replacement,
            evidence_artifact_id=EVIDENCE_ID,
            evidence_sha256=EVIDENCE_SHA,
            preserved_proposal=proposal,
            preserved_edit=prior,
        )
