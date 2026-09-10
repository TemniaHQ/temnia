from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from temnia_pipeline.contracts import (
    ChapterEditSpec,
    ChapterProposal,
    ChapterProposalSection,
    HarnessEvidence,
    Kind,
    PositiveRational,
    QuoteWordId,
    SignedRationalTime,
    TranscriptProvider,
    TranscriptV1,
    TranscriptWord,
    WordTiming,
)
from temnia_pipeline.harness.compiler import compile_chapters
from temnia_pipeline.harness.editorial import (
    EDITORIAL_ASSESSMENT_PROMPT_VERSION,
    EDITORIAL_REPAIR_PROMPT_VERSION,
    EditorialBoundaryChoice,
    EditorialFinding,
    EditorialRepairV1,
    EditorialVerdictV2,
    FindingCode,
    editorial_candidate_fingerprint,
    editorial_compiled_fingerprint,
    ground_editorial_verdict,
    preserved_editorial_constraints,
    render_editorial_assessment_prompt,
    render_editorial_repair_prompt,
    validate_compiled_editorial_repair,
    validate_editorial_repair,
)
from temnia_pipeline.harness.evidence import build_evidence
from temnia_pipeline.harness.routes import ContextWindowExceeded
from temnia_pipeline.harness.validators import HarnessValidationError
from temnia_pipeline.substrate.model import Layers, Provenance, Sentence

EVIDENCE_ID = UUID("30000000-0000-0000-0000-000000000003")
EVIDENCE_SHA = "a" * 64


def _evidence(texts: tuple[str, ...]) -> HarnessEvidence:
    words: list[TranscriptWord] = []
    sentences: list[Sentence] = []
    for index, text in enumerate(texts):
        first = len(words)
        for offset, token in enumerate(text.split()):
            words.append(
                TranscriptWord(
                    confidence=0.99,
                    startMs=index * 3000 + offset * 250 + 100,
                    endMs=index * 3000 + offset * 250 + 300,
                    text=token,
                    timing=WordTiming.aligned,
                    speaker="speaker-a",
                )
            )
        sentences.append(
            Sentence(
                id=index,
                start_ms=words[first].startMs,
                end_ms=words[-1].endMs,
                word_start=first,
                word_end=len(words) - 1,
                text=text,
                speaker="speaker-a",
                is_question=False,
            )
        )
    transcript = TranscriptV1(
        version=1,
        durationMs=len(texts) * 3000,
        language="en",
        provider=TranscriptProvider(name="recorded", model="test", version="1"),
        words=words,
        speakers=["speaker-a"],
        utterances=[],
    )
    layers = Layers(
        words=words,
        sentences=tuple(sentences),
        paragraphs=(),
        candidates=(),
        provenance=Provenance(segmenter="fixture", versions={}, models={}, params={}),
    )
    return build_evidence(
        transcript,
        layers,
        source_id=UUID("10000000-0000-0000-0000-000000000001"),
        source_fingerprint="c" * 64,
        transcript_id=UUID("20000000-0000-0000-0000-000000000002"),
        transcript_revision=1,
        transcript_sha256="b" * 64,
        source_start=SignedRationalTime(numerator=0, denominator=1),
        frame_rate=PositiveRational(numerator=25, denominator=1),
        audio_sample_rate=48000,
        video_time_base=None,
    )


def _section(
    evidence: HarnessEvidence, first: int, last: int, name: str, *, kind: Kind = Kind.keep
) -> ChapterProposalSection:
    return ChapterProposalSection(
        id=name,
        firstSentenceId=evidence.sentences[first].id,
        lastSentenceId=evidence.sentences[last].id,
        kind=kind,
        title=name,
        reason="Preserve the source discussion.",
        quoteWordIds=[
            QuoteWordId(evidence.sentences[first].wordIds[0].root),
            QuoteWordId(evidence.sentences[last].wordIds[-1].root),
        ],
    )


def _proposal(sections: list[ChapterProposalSection]) -> ChapterProposal:
    return ChapterProposal(version=1, sections=sections, summary="Source topics.")


def _case(
    *, opening: str = "A complete opening.", ending: str = "And if"
) -> tuple[HarnessEvidence, ChapterProposal, ChapterEditSpec]:
    evidence = _evidence(
        (
            opening,
            "Why does this matter?",
            "It helps us learn.",
            "That answers the question.",
            ending,
        )
    )
    proposal = _proposal(
        [_section(evidence, 0, 1, "chapter-a"), _section(evidence, 2, 4, "chapter-b")]
    )
    edit = compile_chapters(
        evidence, proposal, evidence_artifact_id=EVIDENCE_ID, evidence_sha256=EVIDENCE_SHA
    )
    return evidence, proposal, edit


def _edge_verdict(
    evidence: HarnessEvidence,
    edit: ChapterEditSpec,
    *,
    at_start: bool = False,
    code: FindingCode | None = None,
) -> EditorialVerdictV2:
    index = 0 if at_start else -1
    selected_code = code or ("source_start_fragment" if at_start else "source_end_fragment")
    return EditorialVerdictV2(
        status="needs_review",
        findings=(
            EditorialFinding(
                id="edge-finding",
                code=selected_code,
                sectionIds=(edit.sections[index].id,),
                boundaryIds=(edit.boundaries[index].id,),
                sentenceIds=(evidence.sentences[index].id,),
                wordIds=tuple(word.root for word in evidence.sentences[index].wordIds),
                reason="This source edge retains a fragment without its complete thought.",
                disposition=(
                    "instruction_required"
                    if selected_code in {"brief_conflict", "timing_uncertainty"}
                    else "repairable"
                ),
            ),
        ),
    )


def _tail_repair(evidence: HarnessEvidence, original: ChapterProposal) -> ChapterProposal:
    return _proposal(
        [
            original.sections[0].model_copy(update={"id": "regenerated-a"}),
            _section(evidence, 2, 3, "complete-answer"),
            _section(evidence, 4, 4, "unfinished-tail", kind=Kind.drop),
        ]
    )


def test_end_fragment_repair_is_exact_scoped_and_retains_unaffected_identity() -> None:
    evidence, original, edit = _case()
    verdict = _edge_verdict(evidence, edit)
    result = validate_editorial_repair(
        evidence=evidence,
        original_proposal=original,
        original_edit=edit,
        verdict=verdict,
        replacement_proposal=_tail_repair(evidence, original),
        allow_source_edge_drops=True,
    )
    assert result.proposal.sections[0] == original.sections[0]
    assert [section.kind for section in result.proposal.sections] == [
        Kind.keep,
        Kind.keep,
        Kind.drop,
    ]
    assert result.proposal.sections[-1].firstSentenceId == evidence.sentences[-1].id
    compiled = compile_chapters(
        evidence, result.proposal, evidence_artifact_id=EVIDENCE_ID, evidence_sha256=EVIDENCE_SHA
    )
    assert evidence.sentences[-2].endMs < compiled.boundaries[-2].timeMs
    assert compiled.boundaries[-2].timeMs < evidence.sentences[-1].startMs
    assert compiled.boundaries[-1].timeMs == evidence.durationMs


def test_start_fragment_repair_preserves_the_following_unaffected_id() -> None:
    evidence, original, edit = _case(opening="because it", ending="That is all.")
    replacement = _proposal(
        [
            _section(evidence, 0, 0, "unfinished-opening", kind=Kind.drop),
            _section(evidence, 1, 1, "question"),
            original.sections[1].model_copy(update={"id": "new-index-label"}),
        ]
    )
    result = validate_editorial_repair(
        evidence=evidence,
        original_proposal=original,
        original_edit=edit,
        verdict=_edge_verdict(evidence, edit, at_start=True),
        replacement_proposal=replacement,
        allow_source_edge_drops=True,
    )
    assert result.proposal.sections[-1] == original.sections[-1]
    assert result.proposal.sections[0].kind == Kind.drop


def test_complete_edge_is_not_automatically_declared_a_fragment() -> None:
    evidence, _, edit = _case(ending="We are done")
    verdict = EditorialVerdictV2(status="passed", findings=())
    assert ground_editorial_verdict(evidence=evidence, edit=edit, verdict=verdict) == verdict
    prompt = render_editorial_assessment_prompt(
        evidence=evidence, edit=edit, brief="Make chapters."
    )
    assert "We are done" in prompt
    assert "unpunctuated speech" in prompt


@pytest.mark.parametrize("code", ["brief_conflict", "timing_uncertainty"])
def test_instruction_required_findings_cannot_authorize_repair(code: FindingCode) -> None:
    evidence, original, edit = _case()
    verdict = _edge_verdict(evidence, edit, code=code)
    with pytest.raises(HarnessValidationError, match="exclusively repairable"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=verdict,
            replacement_proposal=_tail_repair(evidence, original),
            allow_source_edge_drops=True,
        )
    with pytest.raises(HarnessValidationError, match="instruction-required"):
        render_editorial_repair_prompt(
            evidence=evidence,
            edit=edit,
            proposal=original,
            verdict=verdict,
            brief="Keep every word, including source fragments.",
        )


def test_keep_all_conflict_cannot_be_relabelled_as_repairable() -> None:
    evidence, _, edit = _case()
    finding = _edge_verdict(evidence, edit, code="brief_conflict").findings[0]
    with pytest.raises(ValidationError, match="cannot authorize"):
        EditorialFinding.model_validate_json(
            json.dumps({**finding.model_dump(mode="json"), "disposition": "repairable"})
        )


@pytest.mark.parametrize("field", ["sectionIds", "boundaryIds", "sentenceIds", "wordIds"])
def test_foreign_finding_references_are_rejected(field: str) -> None:
    evidence, _, edit = _case()
    verdict = _edge_verdict(evidence, edit)
    finding = verdict.findings[0].model_copy(update={field: ("foreign-id",)})
    with pytest.raises(HarnessValidationError, match="foreign"):
        ground_editorial_verdict(
            evidence=evidence,
            edit=edit,
            verdict=verdict.model_copy(update={"findings": (finding,)}),
        )


def test_known_but_unrelated_source_support_is_rejected() -> None:
    evidence, _, edit = _case()
    verdict = _edge_verdict(evidence, edit)
    finding = verdict.findings[0].model_copy(
        update={
            "sentenceIds": (evidence.sentences[0].id,),
            "wordIds": (evidence.sentences[0].wordIds[0].root,),
        }
    )
    with pytest.raises(HarnessValidationError, match="outside"):
        ground_editorial_verdict(
            evidence=evidence,
            edit=edit,
            verdict=verdict.model_copy(update={"findings": (finding,)}),
        )


@pytest.mark.parametrize("change", ["title", "reason", "quoteWordIds"])
def test_unaffected_editorial_content_cannot_change(change: str) -> None:
    evidence, original, edit = _case()
    replacement = _tail_repair(evidence, original)
    value = [original.sections[0].quoteWordIds[0]] if change == "quoteWordIds" else "Changed"
    replacement.sections[0] = replacement.sections[0].model_copy(update={change: value})
    with pytest.raises(HarnessValidationError, match="unaffected"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=_edge_verdict(evidence, edit),
            replacement_proposal=replacement,
            allow_source_edge_drops=True,
        )


def test_new_drop_requires_permission_and_cannot_extend_beyond_supported_fragment() -> None:
    evidence, original, edit = _case()
    with pytest.raises(HarnessValidationError, match="unauthorized"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=_edge_verdict(evidence, edit),
            replacement_proposal=_tail_repair(evidence, original),
        )
    too_large = _proposal(
        [original.sections[0], _section(evidence, 2, 4, "all-tail", kind=Kind.drop)]
    )
    with pytest.raises(HarnessValidationError, match="unauthorized"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=_edge_verdict(evidence, edit),
            replacement_proposal=too_large,
            allow_source_edge_drops=True,
        )


def test_reason_or_label_changes_do_not_evade_noop_detection() -> None:
    evidence, original, edit = _case()
    candidate = original.model_copy(
        update={
            "summary": "Different summary.",
            "sections": [
                original.sections[0],
                original.sections[1].model_copy(update={"id": "new-id", "reason": "Fixed now."}),
            ],
        }
    )
    with pytest.raises(HarnessValidationError, match="unchanged"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=_edge_verdict(evidence, edit),
            replacement_proposal=candidate,
        )


def test_repeated_candidate_is_rejected_before_reassessment() -> None:
    evidence, original, edit = _case()
    candidate = _tail_repair(evidence, original)
    with pytest.raises(HarnessValidationError, match="repeats"):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=_edge_verdict(evidence, edit),
            replacement_proposal=candidate,
            allow_source_edge_drops=True,
            seen_candidate_hashes=(editorial_candidate_fingerprint(candidate),),
        )


def _cut_verdict(evidence: HarnessEvidence, edit: ChapterEditSpec) -> EditorialVerdictV2:
    return EditorialVerdictV2(
        status="needs_review",
        findings=(
            EditorialFinding(
                id="shared-cut",
                code="internal_cut",
                sectionIds=tuple(section.id for section in edit.sections),
                boundaryIds=(edit.boundaries[1].id,),
                sentenceIds=(evidence.sentences[1].id, evidence.sentences[2].id),
                wordIds=(
                    evidence.sentences[1].wordIds[-1].root,
                    evidence.sentences[2].wordIds[0].root,
                ),
                reason="The shared boundary needs a different grounded transition candidate.",
                disposition="repairable",
            ),
        ),
    )


def test_internal_timing_repair_changes_candidate_without_changing_semantic_spans() -> None:
    evidence, original, edit = _case()
    candidate = next(
        item for item in evidence.boundaries if item.timeMs == evidence.sentences[2].startMs
    )
    choice = EditorialBoundaryChoice(
        leftLastSentenceId=evidence.sentences[1].id,
        rightFirstSentenceId=evidence.sentences[2].id,
        candidateId=candidate.id,
    )
    result = validate_editorial_repair(
        evidence=evidence,
        original_proposal=original,
        original_edit=edit,
        verdict=_cut_verdict(evidence, edit),
        replacement_proposal=original,
        boundary_choices=(choice,),
    )
    assert result.proposal == original
    assert result.candidate_constraints == {
        (choice.leftLastSentenceId, choice.rightFirstSentenceId): candidate.id
    }


@pytest.mark.parametrize("bad_choice", ["same", "foreign", "outside", "duplicate"])
def test_invalid_timing_constraints_are_rejected(bad_choice: str) -> None:
    evidence, original, edit = _case()
    candidate_id = {
        "same": edit.boundaries[1].candidateId,
        "foreign": "invented-candidate",
        "outside": evidence.boundaries[0].id,
        "duplicate": next(
            item.id for item in evidence.boundaries if item.timeMs == evidence.sentences[2].startMs
        ),
    }[bad_choice]
    assert candidate_id is not None
    choice = EditorialBoundaryChoice(
        leftLastSentenceId=evidence.sentences[1].id,
        rightFirstSentenceId=evidence.sentences[2].id,
        candidateId=candidate_id,
    )
    with pytest.raises(HarnessValidationError):
        validate_editorial_repair(
            evidence=evidence,
            original_proposal=original,
            original_edit=edit,
            verdict=_cut_verdict(evidence, edit),
            replacement_proposal=original,
            boundary_choices=(choice, choice) if bad_choice == "duplicate" else (choice,),
        )


def test_prompts_include_actual_edges_all_text_unknown_speech_and_technical_report() -> None:
    evidence, original, edit = _case()
    prompt = render_editorial_assessment_prompt(
        evidence=evidence, edit=edit, brief="Keep all words.", technical_report={"pass": 17}
    )
    payload = json.loads(prompt.split("EDITORIAL_CONTEXT_JSON\n", 1)[1])
    assert payload["promptVersion"] == EDITORIAL_ASSESSMENT_PROMPT_VERSION
    assert payload["brief"] == "Keep all words."
    assert payload["technicalReport"] == {"pass": 17}
    context = payload["context"]
    assert context["compiledEdit"] == edit.model_dump(mode="json")
    assert [(s["id"], s["text"], s["startMs"], s["endMs"]) for s in context["globalSentences"]] == [
        (sentence.id, sentence.text, sentence.startMs, sentence.endMs)
        for sentence in evidence.sentences
    ]
    assert [s["endpointWordIds"] for s in context["globalSentences"]] == [
        list(dict.fromkeys((sentence.wordIds[0].root, sentence.wordIds[-1].root)))
        for sentence in evidence.sentences
    ]
    assert context["speechCoverage"]["status"] == "unknown"
    assert context["coverage"]["globalContextComplete"] is True
    assert context["coverage"]["inspectedMedia"] is False
    assert context["cutFacts"][0]["timeMs"] == 0
    assert context["cutFacts"][-1]["timeMs"] == evidence.durationMs
    repair = render_editorial_repair_prompt(
        evidence=evidence,
        edit=edit,
        proposal=original,
        verdict=_edge_verdict(evidence, edit),
        brief="Remove only incomplete source fragments.",
        allow_source_edge_drops=True,
    )
    repair_payload = json.loads(repair.split("EDITORIAL_CONTEXT_JSON\n", 1)[1])
    assert repair_payload["promptVersion"] == EDITORIAL_REPAIR_PROMPT_VERSION
    assert repair_payload["allowSourceEdgeDrops"] is True
    assert repair_payload["affectedSectionIds"] == [original.sections[-1].id]


def test_oversized_complete_context_refuses_without_truncation() -> None:
    evidence, _, edit = _case()
    with pytest.raises(ContextWindowExceeded, match="complete editorial context"):
        render_editorial_assessment_prompt(evidence=evidence, edit=edit, brief="x" * 524288)


def test_reference_guide_distinguishes_cut_ownership_and_actual_sentence_contents() -> None:
    evidence, _, edit = _case()
    prompt = render_editorial_assessment_prompt(evidence=evidence, edit=edit, brief="Chapters.")
    payload = json.loads(prompt.split("EDITORIAL_CONTEXT_JSON\n", 1)[1])
    guide = payload["context"]["referenceGuide"]
    assert payload["promptVersion"] == "chapter-editorial-assess-v3"
    assert guide["compiledBoundaryIds"] == [boundary.id for boundary in edit.boundaries]
    assert guide["compiledBoundaryOwnership"] == [
        {
            "boundaryId": edit.boundaries[0].id,
            "position": "source_start",
            "sectionIds": [edit.sections[0].id],
        },
        {
            "boundaryId": edit.boundaries[1].id,
            "position": "shared",
            "sectionIds": [section.id for section in edit.sections],
        },
        {
            "boundaryId": edit.boundaries[-1].id,
            "position": "source_end",
            "sectionIds": [edit.sections[-1].id],
        },
    ]
    assert guide["sectionContents"][0]["overlappingSentenceIds"] == [
        sentence.id for sentence in evidence.sentences[:2]
    ]
    assert guide["sectionContents"][1]["overlappingSentenceIds"] == [
        sentence.id for sentence in evidence.sentences[2:]
    ]
    assert guide["keptSourceEdges"][-1] == {
        "fragmentFindingCodeIfNeeded": "source_end_fragment",
        "boundaryId": edit.boundaries[-1].id,
        "sectionId": edit.sections[-1].id,
        "sourceSentenceId": evidence.sentences[-1].id,
    }
    assert "never finding boundaryIds" in prompt


def test_reference_guide_does_not_turn_a_dropped_edge_into_a_kept_fragment() -> None:
    evidence, original, _ = _case()
    repaired = _tail_repair(evidence, original)
    edit = compile_chapters(
        evidence, repaired, evidence_artifact_id=EVIDENCE_ID, evidence_sha256=EVIDENCE_SHA
    )
    prompt = render_editorial_assessment_prompt(evidence=evidence, edit=edit, brief="Chapters.")
    guide = json.loads(prompt.split("EDITORIAL_CONTEXT_JSON\n", 1)[1])["context"]["referenceGuide"]
    assert [row["fragmentFindingCodeIfNeeded"] for row in guide["keptSourceEdges"]] == [
        "source_start_fragment"
    ]
    assert guide["sectionContents"][-1]["kind"] == "drop"
    assert guide["compiledBoundaryOwnership"][-1]["sectionIds"] == [repaired.sections[-1].id]


def test_repair_v2_instructions_are_generic_and_leave_structural_cuts_to_compiler() -> None:
    evidence, original, edit = _case()
    prompt = render_editorial_repair_prompt(
        evidence=evidence,
        edit=edit,
        proposal=original,
        verdict=_edge_verdict(evidence, edit),
        brief="Remove only incomplete fragments.",
        allow_source_edge_drops=True,
    )
    instruction, raw = prompt.split("EDITORIAL_CONTEXT_JSON\n", 1)
    assert json.loads(raw)["promptVersion"] == "chapter-editorial-repair-v4"
    assert "boundaryChoices=[]" in instruction
    assert "two consecutive replacement sections" in instruction
    assert "source-start/source-end candidate" in instruction
    assert "unchanged cut" in instruction
    assert "additional timing override" in instruction
    for sentence in evidence.sentences:
        assert sentence.id not in instruction
        assert sentence.text not in instruction
    for boundary in evidence.boundaries:
        assert boundary.id not in instruction


def test_complete_source_edges_get_reference_entries_without_prescribed_findings() -> None:
    evidence, _, edit = _case(opening="Welcome everyone.", ending="Thank you for listening.")
    prompt = render_editorial_assessment_prompt(evidence=evidence, edit=edit, brief="Chapters.")
    instruction, raw = prompt.split("EDITORIAL_CONTEXT_JSON\n", 1)
    payload = json.loads(raw)
    guide = payload["context"]["referenceGuide"]
    assert len(guide["keptSourceEdges"]) == 2
    assert "findingCode" not in json.dumps(guide)
    assert "findings" not in payload
    assert "reference index, not evidence of defects" in instruction
    assert "complete source edges receive no fragment finding" in instruction
    assert payload["context"]["globalSentences"][-1]["text"] == "Thank you for listening."
    assert (
        ground_editorial_verdict(
            evidence=evidence,
            edit=edit,
            verdict=EditorialVerdictV2(status="passed", findings=()),
        ).status
        == "passed"
    )


def test_repair_wire_rejects_authored_timestamps_and_verdict_cannot_claim_media() -> None:
    with pytest.raises(ValidationError):
        EditorialRepairV1.model_validate_json(
            json.dumps({"version": 1, "sections": [], "summary": "", "timeMs": 500})
        )
    with pytest.raises(ValidationError):
        EditorialVerdictV2.model_validate_json(
            json.dumps(
                {"version": 2, "status": "passed", "findings": [], "inspectedModalities": "audio"}
            )
        )


def test_duplicate_references_and_findings_are_refused() -> None:
    evidence, _, edit = _case()
    finding = _edge_verdict(evidence, edit).findings[0]
    with pytest.raises(ValidationError, match="duplicate references"):
        EditorialFinding.model_validate_json(
            json.dumps({**finding.model_dump(mode="json"), "wordIds": [finding.wordIds[0]] * 2})
        )
    with pytest.raises(ValidationError, match="identities must be unique"):
        EditorialVerdictV2(status="needs_review", findings=(finding, finding))


def test_later_fragment_repair_preserves_a_previous_timing_correction() -> None:
    evidence, original, baseline = _case()
    candidate = evidence.boundaries[8].model_copy(update={"id": "extra-safe", "timeMs": 5500})
    evidence = evidence.model_copy(
        update={
            "boundaries": sorted([*evidence.boundaries, candidate], key=lambda item: item.timeMs)
        }
    )
    corrected = compile_chapters(
        evidence,
        original,
        evidence_artifact_id=EVIDENCE_ID,
        evidence_sha256=EVIDENCE_SHA,
        boundary_constraints={("s000001", "s000002"): candidate.id},
    )
    assert corrected.boundaries[1].timeMs != baseline.boundaries[1].timeMs
    verdict = _edge_verdict(evidence, corrected)
    validated = validate_editorial_repair(
        evidence=evidence,
        original_proposal=original,
        original_edit=corrected,
        verdict=verdict,
        replacement_proposal=_tail_repair(evidence, original),
        allow_source_edge_drops=True,
    )
    constraints = preserved_editorial_constraints(
        original_proposal=original,
        original_edit=corrected,
        replacement_proposal=validated.proposal,
        boundary_choices=(),
    )
    repaired = compile_chapters(
        evidence,
        validated.proposal,
        evidence_artifact_id=EVIDENCE_ID,
        evidence_sha256=EVIDENCE_SHA,
        boundary_constraints=constraints,
    )
    assert repaired.boundaries[1].time == corrected.boundaries[1].time
    result = validate_compiled_editorial_repair(
        original_proposal=original,
        original_edit=corrected,
        replacement_proposal=validated.proposal,
        replacement_edit=repaired,
        verdict=verdict,
    )
    assert result == editorial_compiled_fingerprint(validated.proposal, repaired)
    reverted = compile_chapters(
        evidence,
        validated.proposal,
        evidence_artifact_id=EVIDENCE_ID,
        evidence_sha256=EVIDENCE_SHA,
    )
    with pytest.raises(HarnessValidationError, match="unaffected section"):
        validate_compiled_editorial_repair(
            original_proposal=original,
            original_edit=corrected,
            replacement_proposal=validated.proposal,
            replacement_edit=reverted,
            verdict=verdict,
        )
    with pytest.raises(HarnessValidationError, match="repeats a candidate"):
        validate_compiled_editorial_repair(
            original_proposal=original,
            original_edit=corrected,
            replacement_proposal=validated.proposal,
            replacement_edit=repaired,
            verdict=verdict,
            seen_candidate_hashes=(result,),
        )


def test_compiled_identity_includes_timing_and_rejects_physical_noop() -> None:
    evidence, original, edit = _case()
    with pytest.raises(HarnessValidationError, match="unchanged"):
        validate_compiled_editorial_repair(
            original_proposal=original,
            original_edit=edit,
            replacement_proposal=original,
            replacement_edit=edit,
            verdict=_cut_verdict(evidence, edit),
        )
    changed = edit.model_copy(
        update={
            "boundaries": [
                edit.boundaries[0],
                edit.boundaries[1].model_copy(
                    update={"time": edit.boundaries[1].time.model_copy(update={"numerator": 150})}
                ),
                edit.boundaries[2],
            ]
        }
    )
    assert editorial_compiled_fingerprint(original, changed) != editorial_compiled_fingerprint(
        original, edit
    )
