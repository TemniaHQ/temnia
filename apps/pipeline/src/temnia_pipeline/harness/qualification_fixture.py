"""The small invented transcript every request-shape pre-flight runs against."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from temnia_pipeline.contracts import (
    SignedRationalTime,
    TranscriptProvider,
    TranscriptV1,
    TranscriptWord,
    WordTiming,
)
from temnia_pipeline.harness.evidence import build_evidence
from temnia_pipeline.substrate.model import Layers, Provenance, Sentence

if TYPE_CHECKING:
    from temnia_pipeline.contracts import HarnessEvidence

EDITORIAL_QUALIFICATION_BRIEF = (
    "Create coherent chapters, preserving complete explanations. If the recording ends with an "
    "incomplete fragment, propose an explicit reversible drop of only that fragment."
)


def synthetic_qualification_evidence() -> HarnessEvidence:
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
    return build_evidence(
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
