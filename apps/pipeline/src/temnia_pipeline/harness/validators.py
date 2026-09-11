"""Cross-field harness validation independent of generated schema refinements."""

# ruff: noqa: C901, EM101, EM102, PLR0912, TRY003

from __future__ import annotations

import json
import math
import re
from fractions import Fraction
from typing import TYPE_CHECKING

from temnia_pipeline.contracts import Kind2

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.contracts import (
        ChapterEditSpec,
        ChapterProposal,
        HarnessEvidence,
        RationalTime,
    )

SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class HarnessValidationError(ValueError):
    """A harness artifact violates an editorial or timeline invariant."""


def word_id(value: object) -> str:
    """Read generated root-string IDs without leaking generator shape elsewhere."""
    root = getattr(value, "root", value)
    return str(root)


def rational(time: RationalTime) -> Fraction:
    """Convert a public rational without floating-point loss."""
    return Fraction(time.numerator, time.denominator)


def rounded_milliseconds(value: Fraction) -> int:
    """Round a nonnegative rational to milliseconds; exact halves round later."""
    scaled = value * 1000
    quotient, remainder = divmod(scaled.numerator, scaled.denominator)
    return quotient + int(remainder * 2 >= scaled.denominator)


def _unique(values: Sequence[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise HarnessValidationError(f"{name} must be unique")


def _validate_hash(value: str, name: str) -> None:
    if SHA256.fullmatch(value) is None:
        raise HarnessValidationError(f"{name} must be a SHA-256 hex digest")


def validate_evidence(evidence: HarnessEvidence) -> None:  # noqa: PLR0915
    """Validate grounding, ownership, timing and finite evidence values."""
    _validate_hash(evidence.transcriptSha256, "transcriptSha256")
    _validate_hash(evidence.sourceFingerprint, "sourceFingerprint")
    try:
        json.dumps(evidence.config, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise HarnessValidationError("evidence config must be finite JSON") from error
    word_ids = [word.id for word in evidence.words]
    sentence_ids = [sentence.id for sentence in evidence.sentences]
    boundary_ids = [boundary.id for boundary in evidence.boundaries]
    pause_ids = [pause.id for pause in evidence.pauses]
    _unique(word_ids, "word ids")
    _unique(sentence_ids, "sentence ids")
    _unique(boundary_ids, "boundary candidate ids")
    _unique(pause_ids, "pause ids")
    if [word.wordIndex for word in evidence.words] != list(range(len(evidence.words))):
        raise HarnessValidationError("wordIndex must preserve raw lexical order")
    for word in evidence.words:
        if not word.text:
            raise HarnessValidationError("evidence words must preserve nonempty source text")
        if word.startMs < 0 or word.endMs < word.startMs or word.endMs > evidence.durationMs:
            raise HarnessValidationError("word timing lies outside the source")

    flattened: list[str] = []
    word_by_id = {word.id: word for word in evidence.words}
    for sentence in evidence.sentences:
        owned = [word_id(value) for value in sentence.wordIds]
        if not owned or any(value not in word_by_id for value in owned):
            raise HarnessValidationError("sentence has an invalid word reference")
        flattened.extend(owned)
        source_words = [word_by_id[value] for value in owned]
        if sentence.text != " ".join(word.text for word in source_words):
            raise HarnessValidationError("sentence text must derive from its source words")
        if (
            sentence.startMs != source_words[0].startMs
            or sentence.endMs != max(word.endMs for word in source_words)
            or sentence.endMs < sentence.startMs
            or sentence.endMs > evidence.durationMs
        ):
            raise HarnessValidationError("sentence timing does not match its source words")
    if flattened != word_ids:
        raise HarnessValidationError("sentences must own every word exactly once in lexical order")

    known_words = set(word_ids)
    known_sentences = set(sentence_ids)
    for pause in evidence.pauses:
        if pause.startMs < 0 or pause.endMs <= pause.startMs or pause.endMs > evidence.durationMs:
            raise HarnessValidationError("pause must be a positive in-source interval")
        if (pause.leftWordId is not None and pause.leftWordId not in known_words) or (
            pause.rightWordId is not None and pause.rightWordId not in known_words
        ):
            raise HarnessValidationError("pause refers to an unknown adjacent word")
    for boundary in evidence.boundaries:
        if (
            boundary.timeMs < 0
            or boundary.timeMs > evidence.durationMs
            or not math.isfinite(boundary.score)
            or boundary.clearanceMs < 0
        ):
            raise HarnessValidationError("boundary candidate has invalid timing or score")
        if boundary.sentenceId is not None and boundary.sentenceId not in known_sentences:
            raise HarnessValidationError("boundary candidate refers to an unknown sentence")
    for shot in evidence.shots:
        if shot.timeMs < 0 or shot.timeMs > evidence.durationMs or not math.isfinite(shot.score):
            raise HarnessValidationError("shot has invalid timing or score")
    for interval in evidence.speechCoverage.intervals:
        if (
            interval.startMs < 0
            or interval.endMs < interval.startMs
            or interval.endMs > evidence.durationMs
        ):
            raise HarnessValidationError("speech coverage interval lies outside the source")
    from temnia_pipeline.harness.topic_feasible import validate_topic_derivation  # noqa: PLC0415

    try:
        validate_topic_derivation(evidence)
    except (ValueError, ZeroDivisionError) as error:
        raise HarnessValidationError("invalid derived topic boundary evidence") from error


def validate_proposal(evidence: HarnessEvidence, proposal: ChapterProposal) -> None:
    """Require a model proposal to be an ordered, grounded sentence cover."""
    validate_evidence(evidence)
    if not evidence.sentences:
        raise HarnessValidationError(
            "transcript evidence is empty; paid chapter planning cannot run"
        )
    section_ids = [section.id for section in proposal.sections]
    _unique(section_ids, "proposal section ids")
    sentence_positions = {sentence.id: index for index, sentence in enumerate(evidence.sentences)}
    words_by_sentence = {
        sentence.id: {word_id(value) for value in sentence.wordIds}
        for sentence in evidence.sentences
    }
    expected_start = 0
    for section in proposal.sections:
        try:
            first = sentence_positions[section.firstSentenceId]
            last = sentence_positions[section.lastSentenceId]
        except KeyError as error:
            raise HarnessValidationError("proposal refers to an unknown sentence") from error
        if first != expected_start or last < first:
            raise HarnessValidationError(
                "proposal sentence ranges must be ordered without gaps or overlaps"
            )
        owned_words: set[str] = set()
        for sentence in evidence.sentences[first : last + 1]:
            owned_words.update(words_by_sentence[sentence.id])
        quote_ids = [word_id(value) for value in section.quoteWordIds]
        if any(quote not in owned_words for quote in quote_ids):
            raise HarnessValidationError("proposal quote word must belong to its proposed section")
        expected_start = last + 1
    if expected_start != len(evidence.sentences):
        raise HarnessValidationError("proposal does not cover every evidence sentence")


def validate_edit(
    evidence: HarnessEvidence,
    edit: ChapterEditSpec,
    *,
    expected_evidence_sha256: str | None = None,
) -> None:
    """Require an edit to be an exact, grounded, source-scoped rational cover."""
    validate_evidence(evidence)
    if edit.sourceId != evidence.sourceId:
        raise HarnessValidationError("edit belongs to a different source")
    if expected_evidence_sha256 is not None and edit.evidenceSha256 != expected_evidence_sha256:
        raise HarnessValidationError("edit names a different evidence artifact hash")
    _validate_hash(edit.evidenceSha256, "evidenceSha256")
    if edit.durationMs != evidence.durationMs:
        raise HarnessValidationError("edit duration differs from evidence")
    if edit.sourceFrameRate != evidence.frameRate:
        raise HarnessValidationError("edit frame rate differs from evidence")
    if edit.sourceAudioSampleRate != evidence.audioSampleRate:
        raise HarnessValidationError("edit audio sample rate differs from evidence")
    if len(edit.boundaries) != len(edit.sections) + 1:
        raise HarnessValidationError("edit boundaries and sections do not form a cover")
    boundary_ids = [boundary.id for boundary in edit.boundaries]
    section_ids = [section.id for section in edit.sections]
    _unique(boundary_ids, "compiled boundary ids")
    _unique(section_ids, "compiled section ids")
    times = [rational(boundary.time) for boundary in edit.boundaries]
    if not times or times[0] != 0 or times[-1] != Fraction(edit.durationMs, 1000):
        raise HarnessValidationError("edit edges must be exact source start and duration")
    for index, (boundary, time) in enumerate(zip(edit.boundaries, times, strict=True)):
        if rounded_milliseconds(time) != boundary.timeMs:
            raise HarnessValidationError("boundary timeMs disagrees with rational time")
        if index and time <= times[index - 1]:
            raise HarnessValidationError("every edit interval must have positive duration")

    candidate_ids = {candidate.id for candidate in evidence.boundaries}
    for index, boundary in enumerate(edit.boundaries):
        if boundary.candidateId is None:
            if not any(reason.startswith("manual_") for reason in boundary.reasons):
                raise HarnessValidationError(
                    "only an explicit manual boundary may omit candidateId"
                )
        elif boundary.candidateId not in candidate_ids:
            raise HarnessValidationError("edit refers to an unknown evidence candidate")
        if index in {0, len(edit.boundaries) - 1} and boundary.candidateId is None:
            raise HarnessValidationError("fixed source edges must retain their evidence candidate")

    known_words = {word.id: word for word in evidence.words}
    for index, section in enumerate(edit.sections):
        if (
            section.startBoundaryId != edit.boundaries[index].id
            or section.endBoundaryId != edit.boundaries[index + 1].id
        ):
            raise HarnessValidationError(
                "neighboring sections must reference exactly one shared boundary"
            )
        start, end = times[index], times[index + 1]
        for value in section.quoteWordIds:
            identifier = word_id(value)
            if identifier not in known_words:
                raise HarnessValidationError("edit quote refers to an unknown word")
            word = known_words[identifier]
            center = Fraction(word.startMs + word.endMs, 2000)
            if not (start <= center < end):
                raise HarnessValidationError(
                    "edit quote word center lies outside its owning section"
                )

    edge_candidates = {
        candidate.id for candidate in evidence.boundaries if candidate.kind == Kind2.edge
    }
    if (
        edit.boundaries[0].candidateId not in edge_candidates
        or edit.boundaries[-1].candidateId not in edge_candidates
    ):
        raise HarnessValidationError("source edges must use evidence edge candidates")
