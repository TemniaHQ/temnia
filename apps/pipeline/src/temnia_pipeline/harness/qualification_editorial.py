"""Synthetic, source-grounded requests for the exact editorial native-output schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from temnia_pipeline.contracts import (
    ChapterProposal,
    ChapterProposalSection,
    Kind,
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
    EditorialFinding,
    EditorialRepairV1,
    EditorialVerdictV2,
    ground_editorial_verdict,
    render_editorial_assessment_prompt,
    render_editorial_repair_prompt,
    validate_editorial_repair,
)
from temnia_pipeline.harness.evidence import build_evidence
from temnia_pipeline.harness.models import CompactChapterProposal, canonical_chapter_proposal
from temnia_pipeline.substrate.model import Layers, Provenance, Sentence

if TYPE_CHECKING:
    from temnia_pipeline.contracts import ChapterEditSpec, HarnessEvidence

EDITORIAL_QUALIFICATION_BRIEF = (
    "Create coherent chapters, preserving complete explanations. If the recording ends with an "
    "incomplete fragment, propose an explicit reversible drop of only that fragment."
)


def editorial_qualification_case() -> tuple[
    HarnessEvidence, ChapterProposal, ChapterEditSpec, EditorialVerdictV2
]:
    """Build a small invented transcript with a complete opening and a truncated ending."""
    texts = (
        "Welcome to the discussion.",
        "A garden needs regular attention.",
        "Water supports healthy roots.",
        "That completes our explanation.",
        "And if",
    )
    words: list[TranscriptWord] = []
    sentences: list[Sentence] = []
    for index, text in enumerate(texts):
        first = len(words)
        for offset, token in enumerate(text.split()):
            words.append(
                TranscriptWord(
                    text=token,
                    startMs=index * 3000 + offset * 250 + 100,
                    endMs=index * 3000 + offset * 250 + 300,
                    confidence=0.99,
                    speaker="speaker-a",
                    timing=WordTiming.aligned,
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
        durationMs=15_000,
        language="en",
        speakers=["speaker-a"],
        utterances=[],
        words=words,
        provider=TranscriptProvider(name="synthetic", model="qualification", version="1"),
    )
    evidence = build_evidence(
        transcript,
        Layers(
            sentences=tuple(sentences),
            words=words,
            paragraphs=(),
            candidates=(),
            provenance=Provenance(segmenter="synthetic", versions={}, models={}, params={}),
        ),
        source_id=UUID("10000000-0000-4000-8000-000000000001"),
        transcript_id=UUID("20000000-0000-4000-8000-000000000002"),
        transcript_revision=1,
        transcript_sha256="b" * 64,
        source_fingerprint="c" * 64,
        source_start=SignedRationalTime(numerator=0, denominator=1),
        frame_rate=None,
        video_time_base=None,
        audio_sample_rate=None,
    )
    proposal = ChapterProposal(
        version=1,
        summary="Two chapters covering the source.",
        sections=[
            ChapterProposalSection(
                id=identifier,
                firstSentenceId=evidence.sentences[first].id,
                lastSentenceId=evidence.sentences[last].id,
                kind=Kind.keep,
                title=title,
                reason="Preserve this complete topic.",
                quoteWordIds=[],
            )
            for identifier, first, last, title in (
                ("opening", 0, 1, "Garden care"),
                ("explanation", 2, 4, "Roots and conclusion"),
            )
        ],
    )
    edit = compile_chapters(
        evidence,
        proposal,
        evidence_artifact_id=UUID("30000000-0000-4000-8000-000000000003"),
        evidence_sha256="a" * 64,
    )
    verdict = EditorialVerdictV2(
        status="needs_review",
        findings=(
            EditorialFinding(
                id="truncated-ending",
                code="source_end_fragment",
                sectionIds=("explanation",),
                boundaryIds=(edit.boundaries[-1].id,),
                sentenceIds=(evidence.sentences[-1].id,),
                wordIds=tuple(word.root for word in evidence.sentences[-1].wordIds),
                reason="The final conditional fragment has no continuation in the source.",
                disposition="repairable",
            ),
        ),
    )
    return evidence, proposal, edit, verdict


def editorial_qualification_prompts() -> dict[
    str, tuple[str, type[EditorialVerdictV2 | EditorialRepairV1], str]
]:
    """Use production prompt builders and exact production native-output types."""
    evidence, proposal, edit, verdict = editorial_qualification_case()
    return {
        "editorial_assess": (
            render_editorial_assessment_prompt(
                evidence=evidence,
                edit=edit,
                brief=EDITORIAL_QUALIFICATION_BRIEF,
                technical_report={
                    "sections": [],
                    "note": "Synthetic transport fixture; no media inspected.",
                },
            ),
            EditorialVerdictV2,
            EDITORIAL_ASSESSMENT_PROMPT_VERSION,
        ),
        "editorial_repair": (
            render_editorial_repair_prompt(
                evidence=evidence,
                edit=edit,
                proposal=proposal,
                verdict=verdict,
                brief=EDITORIAL_QUALIFICATION_BRIEF,
                allow_source_edge_drops=True,
            ),
            EditorialRepairV1,
            EDITORIAL_REPAIR_PROMPT_VERSION,
        ),
    }


def validate_editorial_qualification_output(stage: str, output: object) -> None:
    """Check grounding separately from the strict native-schema transport result."""
    evidence, proposal, edit, verdict = editorial_qualification_case()
    if stage == "editorial_assess" and isinstance(output, EditorialVerdictV2):
        ground_editorial_verdict(
            evidence=evidence, edit=edit, verdict=output, require_local_timing_evidence=True
        )
    elif stage == "editorial_repair" and isinstance(output, EditorialRepairV1):
        compact = CompactChapterProposal.model_validate_json(
            output.model_dump_json(exclude={"boundaryChoices"})
        )
        validated = validate_editorial_repair(
            evidence=evidence,
            original_proposal=proposal,
            original_edit=edit,
            verdict=verdict,
            replacement_proposal=canonical_chapter_proposal(compact),
            boundary_choices=output.boundaryChoices,
            allow_source_edge_drops=True,
            require_supplied_timing_options=True,
        )
        compile_chapters(
            evidence,
            validated.proposal,
            evidence_artifact_id=edit.evidenceArtifactId,
            evidence_sha256=edit.evidenceSha256,
            boundary_constraints=validated.candidate_constraints,
        )
    else:
        raise ValueError("editorial qualification output has the wrong stage or type")  # noqa: EM101, TRY003
