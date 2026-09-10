"""Measured cut evidence is local, exact and separate from semantic judgments."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

import pytest

from temnia_pipeline.contracts import (
    ChapterEditSpec,
    ChapterProposal,
    HarnessEvidence,
    RationalTime,
    SignedRationalTime,
    SpeechCoverage,
    SpeechCoverageInterval,
    Status1,
    Timing,
)
from temnia_pipeline.harness import qualification_editorial
from temnia_pipeline.harness.compiler import compile_chapters
from temnia_pipeline.harness.editorial import (
    EditorialBoundaryChoice,
    EditorialVerdictV2,
    _cut_facts_at,
    editorial_cut_facts,
    ground_editorial_verdict,
    render_editorial_assessment_prompt,
    render_editorial_repair_prompt,
    validate_editorial_repair,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_harness_editorial import (
    EVIDENCE_ID,
    EVIDENCE_SHA,
    _case,
    _cut_verdict,
    _edge_verdict,
    _proposal,
    _section,
    _tail_repair,
)


def _detected(
    evidence: HarnessEvidence, intervals: tuple[tuple[int, int], ...] = ()
) -> HarnessEvidence:
    return evidence.model_copy(
        update={
            "speechCoverage": SpeechCoverage(
                detector="recorded-detector",
                detectorHash="d" * 64,
                detectorRevision="fixture/1",
                status=Status1.needs_review,
                intervals=[
                    SpeechCoverageInterval(startMs=start, endMs=end) for start, end in intervals
                ],
                uncoveredSpeechMs=60999,
                uncoveredTailMs=63,
                warnings=["Global detector/aligned-word comparison needs review."],
            )
        }
    )


def _measured_timing_fixture() -> HarnessEvidence:
    """Retained short-run coordinates, with invented text and no source transcript."""
    evidence, _, _ = _case()
    coordinates = (
        (130148, 130428, 0.841),
        (130568, 130909, 0.886),
        (132349, 132549, 0.843),
        (132589, 132609, 0.855),
        (236015, 236055, 0.493),
        (236075, 236596, 0.895),
        (239577, 239717, 0.863),
        (239737, 239877, 0.898),
    )
    words = [
        word.model_copy(update={"startMs": start, "endMs": end, "confidence": confidence})
        for word, (start, end, confidence) in zip(evidence.words, coordinates, strict=False)
    ]
    return _detected(
        evidence.model_copy(update={"words": words, "durationMs": 240040}),
        (
            (127042, 129150),
            (129314, 136094),
            (233858, 235358),
            (235650, 236734),
            (239586, 240040),
        ),
    )


def test_measured_risky_cut_and_clear_tail_do_not_inherit_global_disagreement() -> None:
    evidence = _measured_timing_fixture()
    risky = _cut_facts_at(evidence, Fraction(3291, 25))
    clear = _cut_facts_at(evidence, Fraction(5952, 25))
    assert risky["exactTimeMs"] == 131640
    assert risky["intersectingWordIntervals"] == []
    assert risky["intersectingSpeechIntervals"] == [{"startMs": 129314, "endMs": 136094}]
    assert risky["measuredDetectorGap"] is None
    assert risky["localTimingRiskReasons"] == ["inside_detected_speech"]
    assert clear["exactTimeMs"] == 238080
    assert clear["intersectingSpeechIntervals"] == []
    assert clear["measuredDetectorGap"] == {
        "startMs": 236734,
        "endMs": 239586,
        "durationMs": 2852,
        "leftClearanceMs": 1346,
        "rightClearanceMs": 1506,
    }
    assert clear["alignedWordGap"] == {"startMs": 236596, "endMs": 239577}
    assert clear["localTimingRiskReasons"] == []
    # The low-confidence earlier word is not adjacent to this cut.
    assert clear["nearestLeftWord"]["confidence"] == 0.895
    assert clear["localWordUncertainty"] == []


@pytest.mark.parametrize("unknown_detector", [None, "unavailable-recorded-detector"])
def test_absent_or_unknown_detector_never_claims_a_measured_gap(
    unknown_detector: str | None,
) -> None:
    evidence, _, edit = _case()
    evidence = evidence.model_copy(
        update={
            "speechCoverage": evidence.speechCoverage.model_copy(
                update={"detector": unknown_detector}
            )
        }
    )
    facts = editorial_cut_facts(evidence, edit)[1]
    assert facts["detectorAvailable"] is False
    assert facts["measuredDetectorGap"] is None
    assert facts["localTimingRiskReasons"] == ["detector_unavailable"]


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"timing": Timing.interpolated}, "word_timing_interpolated"),
        ({"confidence": None}, "word_confidence_unknown"),
        ({"confidence": 0.49}, "word_confidence_low"),
    ],
)
def test_adjacent_word_uncertainty_is_local_even_in_a_detector_gap(
    updates: dict[str, object], reason: str
) -> None:
    evidence = _measured_timing_fixture()
    evidence.words[5] = evidence.words[5].model_copy(update=updates)
    facts = _cut_facts_at(evidence, Fraction(5952, 25))
    assert facts["localTimingRiskReasons"] == [reason]
    assert facts["localWordUncertainty"] == [{"wordId": evidence.words[5].id, "reasons": [reason]}]
    assert facts["measuredDetectorGap"]["durationMs"] == 2852


def test_exact_fraction_and_interval_endpoints_match_compiler_clock() -> None:
    evidence, _, edit = _case()
    cut_ms = edit.boundaries[1].timeMs
    evidence = _detected(evidence, ((cut_ms - 100, cut_ms), (cut_ms + 1, cut_ms + 100)))
    boundary = edit.boundaries[1].model_copy(
        update={"time": RationalTime(numerator=cut_ms * 3 + 1, denominator=3000)}
    )
    edit = edit.model_copy(
        update={"boundaries": [edit.boundaries[0], boundary, edit.boundaries[2]]}
    )
    facts = editorial_cut_facts(evidence, edit)[1]
    assert facts["timeMs"] == cut_ms  # Display rounding is not used for intersection tests.
    assert facts["exactTimeMs"] == {"numerator": cut_ms * 3 + 1, "denominator": 3}
    assert facts["measuredDetectorGap"]["leftClearanceMs"] == {"numerator": 1, "denominator": 3}
    assert facts["localTimingRiskReasons"] == []
    for endpoint in (cut_ms, cut_ms + 1):
        assert (
            _cut_facts_at(evidence, Fraction(endpoint, 1000))["intersectingSpeechIntervals"] == []
        )
    # Touching detector intervals are one continuous interval, including their shared point.
    touching = _detected(evidence, ((cut_ms - 100, cut_ms), (cut_ms, cut_ms + 100)))
    assert _cut_facts_at(touching, Fraction(cut_ms, 1000))["intersectingSpeechIntervals"] == [
        {"startMs": cut_ms - 100, "endMs": cut_ms + 100}
    ]


def test_source_mapping_does_not_shift_cut_evidence_and_edges_report_contact() -> None:
    evidence, _, edit = _case()
    evidence = _detected(evidence, ((0, 50), (evidence.durationMs - 50, evidence.durationMs)))
    old = editorial_cut_facts(evidence, edit)
    evidence = evidence.model_copy(
        update={"sourceStart": SignedRationalTime(numerator=900, denominator=1)}
    )
    assert editorial_cut_facts(evidence, edit) == old
    for fact in (old[0], old[-1]):
        assert fact["intersectingSpeechIntervals"] == []
        assert fact["sourceEdgeDetectedSpeechContact"] is True
        assert fact["localTimingRiskReasons"] == ["source_edge_detected_speech_contact"]


def _uncertainty(evidence: HarnessEvidence, edit: ChapterEditSpec) -> EditorialVerdictV2:
    cut = _cut_verdict(evidence, edit)
    return cut.model_copy(
        update={
            "findings": (
                cut.findings[0].model_copy(
                    update={"code": "timing_uncertainty", "disposition": "instruction_required"}
                ),
            )
        }
    )


def test_new_timing_grounding_refuses_clear_cut_but_historical_reader_is_compatible() -> None:
    evidence, _, edit = _case()
    evidence = _detected(evidence)
    verdict = _uncertainty(evidence, edit)
    assert ground_editorial_verdict(evidence=evidence, edit=edit, verdict=verdict) == verdict
    with pytest.raises(HarnessValidationError, match="local cut evidence"):
        ground_editorial_verdict(
            evidence=evidence, edit=edit, verdict=verdict, require_local_timing_evidence=True
        )
    for local_evidence in (
        _detected(evidence, ((edit.boundaries[1].timeMs - 50, edit.boundaries[1].timeMs + 50),)),
        _case()[0],
    ):
        assert (
            ground_editorial_verdict(
                evidence=local_evidence,
                edit=edit,
                verdict=verdict,
                require_local_timing_evidence=True,
            )
            == verdict
        )


def _context(prompt: str) -> dict[str, Any]:
    return json.loads(prompt.split("EDITORIAL_CONTEXT_JSON\n", 1)[1])


def test_compact_context_preserves_global_text_and_anchors_without_full_catalogues() -> None:
    evidence, _, edit = _case()
    evidence = _detected(evidence)
    payload = _context(
        render_editorial_assessment_prompt(
            evidence=evidence, edit=edit, brief="Make complete chapters."
        )
    )
    context = payload["context"]
    for actual, sentence in zip(context["globalSentences"], evidence.sentences, strict=True):
        assert actual == {
            "id": sentence.id,
            "text": sentence.text,
            "startMs": sentence.startMs,
            "endMs": sentence.endMs,
            "speakers": sentence.speakers,
            "endpointWordIds": list(
                dict.fromkeys([sentence.wordIds[0].root, sentence.wordIds[-1].root])
            ),
        }
    assert context["compiledEdit"] == edit.model_dump(mode="json")
    assert "candidateBoundaries" not in context
    assert "intervals" not in context["speechCoverage"]
    assert "timingRepairOptions" not in context
    assert context["speechCoverage"]["comparisonBasis"] == "aligned_word_time_intervals"
    assert "natural inter-word gaps" in context["speechCoverage"]["interpretation"]
    assert 0 < len(context["contextWords"]) < len(evidence.words)


def test_repair_preserves_original_fields_and_supplies_only_affected_safe_local_choices() -> None:
    evidence, proposal, edit = _case()
    evidence = _detected(evidence)
    verdict = _cut_verdict(evidence, edit)
    payload = _context(
        render_editorial_repair_prompt(
            evidence=evidence,
            edit=edit,
            proposal=proposal,
            verdict=verdict,
            brief="Complete topics.",
        )
    )
    assert payload["originalProposal"] == proposal.model_dump(mode="json")
    options = payload["context"]["timingRepairOptions"]
    assert len(options) == 1
    assert options[0]["boundaryId"] == edit.boundaries[1].id
    assert options[0]["candidates"]
    start, end = evidence.sentences[1].endMs, evidence.sentences[2].startMs
    assert all(
        start <= item["timeMs"] <= end and item["candidateId"] != edit.boundaries[1].candidateId
        for item in options[0]["candidates"]
    )
    # The same local transition offers no fake cure when every candidate is inside speech.
    risky = _detected(evidence, ((start - 1, end + 1),))
    assert (
        _context(
            render_editorial_repair_prompt(
                evidence=risky,
                edit=edit,
                proposal=proposal,
                verdict=verdict,
                brief="Complete topics.",
            )
        )["context"]["timingRepairOptions"][0]["candidates"]
        == []
    )
    semantic = _context(
        render_editorial_repair_prompt(
            evidence=evidence,
            edit=edit,
            proposal=proposal,
            verdict=_edge_verdict(evidence, edit),
            brief="Complete topics.",
            allow_source_edge_drops=True,
        )
    )
    assert semantic["context"]["timingRepairOptions"] == []


def test_coherent_merge_removes_a_measured_risky_cut_and_preserves_all_sentences() -> None:
    evidence, proposal, edit = _case(ending="That completes this answer.")
    evidence = _detected(
        evidence, ((edit.boundaries[1].timeMs - 50, edit.boundaries[1].timeMs + 50),)
    )
    replacement: ChapterProposal = _proposal([_section(evidence, 0, 4, "complete-topic")])
    validated = validate_editorial_repair(
        evidence=evidence,
        original_proposal=proposal,
        original_edit=edit,
        verdict=_cut_verdict(evidence, edit),
        replacement_proposal=replacement,
    )
    result = compile_chapters(
        evidence, validated.proposal, evidence_artifact_id=EVIDENCE_ID, evidence_sha256=EVIDENCE_SHA
    )
    assert len(result.boundaries) == 2
    assert validated.proposal.sections[0].firstSentenceId == evidence.sentences[0].id
    assert validated.proposal.sections[0].lastSentenceId == evidence.sentences[-1].id


def test_candidate_options_use_compiled_clock_and_refuse_omitted_real_candidates() -> None:
    evidence, proposal, edit = _case()
    evidence = _detected(evidence, ((4999, 5002),))
    candidate = next(
        item
        for item in evidence.boundaries
        if item.id != edit.boundaries[1].candidateId
        and item.timeMs == evidence.sentences[2].startMs
    )
    candidate = candidate.model_copy(update={"id": "quantized-risk", "timeMs": 5007})
    unchanged = candidate.model_copy(
        update={"id": "same-physical-cut", "timeMs": edit.boundaries[1].timeMs + 1}
    )
    evidence = evidence.model_copy(
        update={
            "boundaries": sorted(
                [*evidence.boundaries, candidate, unchanged],
                key=lambda item: (item.timeMs, item.id),
            )
        }
    )
    assert _cut_facts_at(evidence, Fraction(5007, 1000))["localTimingRiskReasons"] == []
    assert _cut_facts_at(evidence, Fraction(5))["localTimingRiskReasons"] == [
        "inside_detected_speech"
    ]
    verdict = _cut_verdict(evidence, edit)
    payload = _context(
        render_editorial_repair_prompt(
            evidence=evidence,
            edit=edit,
            proposal=proposal,
            verdict=verdict,
            brief="Complete topics.",
        )
    )
    supplied = payload["context"]["timingRepairOptions"][0]["candidates"]
    assert candidate.id not in {item["candidateId"] for item in supplied}
    assert unchanged.id not in {item["candidateId"] for item in supplied}
    assert all("compiledTime" in item and "compiledExactTimeMs" in item for item in supplied)
    assert all(item["compiledExactTimeMs"] != edit.boundaries[1].timeMs for item in supplied)
    choice = EditorialBoundaryChoice(
        leftLastSentenceId=evidence.sentences[1].id,
        rightFirstSentenceId=evidence.sentences[2].id,
        candidateId=candidate.id,
    )
    # Older accepted outputs remain readable under their reference-only contract.
    validate_editorial_repair(
        evidence=evidence,
        original_proposal=proposal,
        original_edit=edit,
        verdict=verdict,
        replacement_proposal=proposal,
        boundary_choices=(choice,),
    )
    with pytest.raises(HarnessValidationError, match="absent from its prompt"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=proposal,
            original_edit=edit,
            verdict=verdict,
            replacement_proposal=proposal,
            boundary_choices=(choice,),
            require_supplied_timing_options=True,
        )


def test_unknown_detector_empty_options_cannot_authorize_a_timing_override() -> None:
    evidence, proposal, edit = _case()
    candidate = next(
        item for item in evidence.boundaries if item.timeMs == evidence.sentences[2].startMs
    )
    verdict = _cut_verdict(evidence, edit)
    payload = _context(
        render_editorial_repair_prompt(
            evidence=evidence,
            edit=edit,
            proposal=proposal,
            verdict=verdict,
            brief="Complete topics.",
        )
    )
    assert payload["context"]["timingRepairOptions"][0]["candidates"] == []
    choice = EditorialBoundaryChoice(
        leftLastSentenceId=evidence.sentences[1].id,
        rightFirstSentenceId=evidence.sentences[2].id,
        candidateId=candidate.id,
    )
    with pytest.raises(HarnessValidationError, match="absent from its prompt"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=proposal,
            original_edit=edit,
            verdict=verdict,
            replacement_proposal=proposal,
            boundary_choices=(choice,),
            require_supplied_timing_options=True,
        )


def test_exact_word_endpoint_is_contact_not_interior_and_source_edge_contact_is_explicit() -> None:
    evidence, _, _ = _case()
    evidence = _detected(evidence)
    word = evidence.words[0]
    for endpoint in (word.startMs, word.endMs):
        assert _cut_facts_at(evidence, Fraction(endpoint, 1000))["intersectingWordIntervals"] == []
    assert _cut_facts_at(evidence, Fraction(word.startMs + 1, 1000))["localTimingRiskReasons"] == [
        "inside_spoken_word"
    ]
    evidence.words[0] = word.model_copy(update={"startMs": 0})
    fact = _cut_facts_at(evidence, Fraction(0))
    assert fact["sourceEdgeWordContact"] is True
    assert fact["intersectingWordIntervals"] == []
    assert fact["localTimingRiskReasons"] == ["source_edge_word_contact"]


def test_current_qualification_uses_strict_local_timing_grounding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, proposal, edit = _case()
    evidence = _detected(evidence)
    verdict = _uncertainty(evidence, edit)
    monkeypatch.setattr(
        qualification_editorial,
        "editorial_qualification_case",
        lambda: (evidence, proposal, edit, verdict),
    )
    with pytest.raises(HarnessValidationError, match="local cut evidence"):
        qualification_editorial.validate_editorial_qualification_output("editorial_assess", verdict)


def test_v4_quote_guidance_preserves_empty_unaffected_arrays_without_coercion() -> None:
    evidence, original, _ = _case()
    original = original.model_copy(
        update={
            "sections": [
                original.sections[0].model_copy(update={"quoteWordIds": []}),
                original.sections[1],
            ]
        }
    )
    edit = compile_chapters(
        evidence, original, evidence_artifact_id=EVIDENCE_ID, evidence_sha256=EVIDENCE_SHA
    )
    verdict = _edge_verdict(evidence, edit)
    prompt = render_editorial_repair_prompt(
        evidence=evidence,
        edit=edit,
        proposal=original,
        verdict=verdict,
        brief="Keep complete thoughts.",
        allow_source_edge_drops=True,
    )
    instruction = prompt.split("EDITORIAL_CONTEXT_JSON", 1)[0]
    assert "including an empty quoteWordIds=[] array" in instruction
    assert "ONLY for changed or affected sections" in instruction
    assert "unaffected sections copy their original quoteWordIds verbatim" in instruction
    assert not any(
        sentence.id in instruction or sentence.text in instruction
        for sentence in evidence.sentences
    )
    replacement = _tail_repair(evidence, original)
    validate_editorial_repair(
        evidence=evidence,
        original_proposal=original,
        original_edit=edit,
        verdict=verdict,
        replacement_proposal=replacement,
        allow_source_edge_drops=True,
        require_supplied_timing_options=True,
    )
    wrong = replacement.model_copy(
        update={
            "sections": [
                replacement.sections[0].model_copy(
                    update={"quoteWordIds": _section(evidence, 0, 1, "ignored").quoteWordIds}
                ),
                *replacement.sections[1:],
            ]
        }
    )
    with pytest.raises(HarnessValidationError, match="changed an unaffected section"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=verdict,
            replacement_proposal=wrong,
            allow_source_edge_drops=True,
            require_supplied_timing_options=True,
        )
