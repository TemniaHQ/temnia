"""Reproduce physical feasibility on the actual output grid, without editorial claims."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from fractions import Fraction

import pytest

from temnia_pipeline.contracts import PositiveRational, SignedRationalTime, SpeechCoverageInterval
from temnia_pipeline.harness.compiler import candidate_time
from temnia_pipeline.harness.topic_compiler import (
    compile_topics_v2,
    topic_boundary_issues,
    topic_boundary_issues_v2,
    validate_topic_edit,
)
from temnia_pipeline.harness.topic_feasible import (
    PREFIX,
    augment_topic_evidence,
    derived_candidate_time,
)
from temnia_pipeline.harness.validators import HarnessValidationError, rational, validate_evidence
from test_harness_compiler import ARTIFACT_ID, SHA, _clear_coverage, _evidence, _transcript, _word
from test_topic_compiler import _candidate, _case
from test_topic_editorial import _proposal


def test_submillisecond_audio_grid_preserves_safe_interior_without_mutating_source() -> None:
    source = _evidence(
        _transcript([_word("Before.", 0, 1), _word("After.", 2, 4)], 5),
        [(0, 0), (1, 1)],
        audio_sample_rate=1100,
    )
    original = source.model_dump_json()
    evidence = augment_topic_evidence(source)
    derived = [c for c in evidence.boundaries if c.id.startswith(PREFIX)]
    assert len(derived) == 1
    assert derived_candidate_time(evidence, derived[0]) == Fraction(1, 550)
    # 1.818ms is the only safe sample; integer-ms truncation would choose a different sample.
    assert derived[0].timeMs == 2
    edit = compile_topics_v2(
        evidence,
        _proposal(_candidate("after", 1, 1)),
        evidence_artifact_id=ARTIFACT_ID,
        evidence_sha256=SHA,
    )
    boundary = edit.videos[0].edit.boundaries[1]
    assert rational(boundary.time) == Fraction(1, 550)
    assert boundary.candidateId == derived[0].id
    validate_topic_edit(evidence, edit, expected_evidence_sha256=SHA)
    assert source.model_dump_json() == original
    assert augment_topic_evidence(evidence) == evidence


def test_sparse_old_inventory_does_not_mean_no_feasible_frame() -> None:
    evidence = _case().model_copy(
        update={"frameRate": PositiveRational(numerator=25, denominator=1)}
    )
    # Retained source observations may have no suggested interior cut at all.
    evidence = evidence.model_copy(
        update={"boundaries": [c for c in evidence.boundaries if str(c.kind) == "edge"]}
    )
    candidate = _candidate("claim", 1, 2)
    assert topic_boundary_issues(evidence, candidate)
    augmented = augment_topic_evidence(evidence)
    assert not topic_boundary_issues_v2(augmented, candidate)
    edit = compile_topics_v2(
        augmented, _proposal(candidate), evidence_artifact_id=ARTIFACT_ID, evidence_sha256=SHA
    )
    validate_topic_edit(augmented, edit, expected_evidence_sha256=SHA)


def test_derived_inventory_preserves_acoustic_risk_and_reports_genuine_infeasibility() -> None:
    source = _case()
    blocked = source.model_copy(
        update={
            "speechCoverage": _clear_coverage().model_copy(
                update={"intervals": [SpeechCoverageInterval(startMs=0, endMs=4000)]}
            )
        }
    )
    augmented = augment_topic_evidence(blocked)
    assert not [c for c in augmented.boundaries if c.id.startswith(PREFIX)]
    assert topic_boundary_issues_v2(augmented, _candidate("inside", 1, 2))
    unknown = source.model_copy(
        update={
            "speechCoverage": source.speechCoverage.model_copy(
                update={"status": type(source.speechCoverage.status)("unknown")}
            )
        }
    )
    uncertain = augment_topic_evidence(unknown)
    assert all(c.requiresReview for c in uncertain.boundaries if c.id.startswith(PREFIX))


@pytest.mark.parametrize("mutation", ["remove", "time", "score", "inject"])
def test_changed_derived_candidates_cannot_be_admitted(mutation: str) -> None:
    evidence = augment_topic_evidence(_case())
    selected = next(c for c in evidence.boundaries if c.id.startswith(PREFIX))
    rows = list(evidence.boundaries)
    if mutation == "remove":
        rows.remove(selected)
    elif mutation == "inject":
        rows.append(selected.model_copy(update={"id": PREFIX + "1:17"}))
    else:
        field, value = ("timeMs", selected.timeMs + 1) if mutation == "time" else ("score", 0.0)
        rows = [c.model_copy(update={field: value}) if c.id == selected.id else c for c in rows]
    with pytest.raises(HarnessValidationError):
        validate_evidence(evidence.model_copy(update={"boundaries": rows}))


def test_grid_is_source_relative_and_v2_requires_persisted_derivation() -> None:
    source = _case()
    offset = source.model_copy(
        update={"sourceStart": SignedRationalTime(numerator=17, denominator=3)}
    )
    # Use the actual evidence clock field; source offset never rephases the output grid.
    if offset == source:
        pytest.fail("offset fixture failed to change evidence")
    left, right = augment_topic_evidence(source), augment_topic_evidence(offset)
    assert [(c.id, candidate_time(left, c)) for c in left.boundaries] == [
        (c.id, candidate_time(right, c)) for c in right.boundaries
    ]
    with pytest.raises(HarnessValidationError, match="persisted"):
        compile_topics_v2(
            source,
            _proposal(_candidate("all", 0, 3)),
            evidence_artifact_id=ARTIFACT_ID,
            evidence_sha256=SHA,
        )
