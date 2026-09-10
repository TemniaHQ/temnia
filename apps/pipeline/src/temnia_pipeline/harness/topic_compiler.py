"""Ground independent topic extents and reuse one-keep chapter execution plans.

The portfolio may overlap or omit source material. Each execution still forms a
private exact cover for the existing renderer; its outside drops are not editorial
decisions about another video. Semantic membership never changes to obtain a cut.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Never

from temnia_pipeline.contracts import (
    ChapterProposal,
    ChapterProposalSection,
    Kind,
    Kind2,
    QuoteWordId,
    TopicBoundaryIssue,
    TopicCompiledVideo,
    TopicEditSpec,
    TopicProposal,
)
from temnia_pipeline.harness.compiler import CompilerConfig, compile_chapters, quantize_time
from temnia_pipeline.harness.validators import (
    HarnessValidationError,
    rational,
    validate_edit,
    validate_evidence,
    word_id,
)
from temnia_pipeline.speech.coverage import merge_intervals

if TYPE_CHECKING:
    from uuid import UUID

    from temnia_pipeline.contracts import (
        ChapterEditSpec,
        HarnessBoundaryCandidate,
        HarnessEvidence,
        HarnessEvidenceWord,
        TopicCandidate,
        TopicSentenceSpan,
    )

TOPIC_COMPILER_VERSION = "topic-compiler/1"
_SPAN_FIELDS = (
    "coreSpans",
    "requiredContextSpans",
    "completionSpans",
    "meaningChangingFollowups",
)
_UNKNOWN_SPEECH_REASON = "topic_speech_evidence_unavailable"
_DEFAULT_COMPILER_CONFIG = CompilerConfig()


def _refuse(message: str, *, cause: Exception | None = None) -> Never:
    raise HarnessValidationError(message) from cause


@dataclass(frozen=True, slots=True)
class TopicSourceUsage:
    """Source accounting is separate from the independent video timelines."""

    used_intervals: tuple[tuple[Fraction, Fraction], ...]
    unused_intervals: tuple[tuple[Fraction, Fraction], ...]
    used_duration: Fraction
    unused_duration: Fraction
    repeated_duration: Fraction


@dataclass(frozen=True, slots=True)
class _IntervalIndex:
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    @classmethod
    def build(cls, intervals: list[tuple[int, int]], duration_ms: int) -> _IntervalIndex:
        merged = merge_intervals(intervals, duration_ms)
        return cls(tuple(start for start, _end in merged), tuple(end for _start, end in merged))

    def inside(self, instant: Fraction) -> bool:
        milliseconds = instant * 1000
        index = bisect_right(self.starts, milliseconds) - 1
        return index >= 0 and self.starts[index] < milliseconds < self.ends[index]


@dataclass(frozen=True, slots=True)
class _Membership:
    first: int
    last: int
    words: tuple[HarnessEvidenceWord, ...]
    before: tuple[HarnessEvidenceWord, ...]
    after: tuple[HarnessEvidenceWord, ...]

    @property
    def start_window(self) -> tuple[Fraction, Fraction]:
        return (
            Fraction(max((word.endMs for word in self.before), default=0), 1000),
            Fraction(min(word.startMs for word in self.words), 1000),
        )

    @property
    def end_window(self) -> tuple[Fraction, Fraction]:
        last_end = max(word.endMs for word in self.words)
        return (
            Fraction(last_end, 1000),
            Fraction(min((word.startMs for word in self.after), default=last_end), 1000),
        )


def _span_positions(
    positions: dict[str, int], span: TopicSentenceSpan | TopicCandidate, *, field: str
) -> tuple[int, int]:
    try:
        first, last = positions[span.firstSentenceId], positions[span.lastSentenceId]
    except KeyError as error:
        _refuse(f"{field}: unknown sentence {error.args[0]}", cause=error)
    if last < first:
        _refuse(f"{field}: lastSentenceId precedes firstSentenceId")
    return first, last


def _validate_candidate(evidence: HarnessEvidence, candidate: TopicCandidate) -> None:
    for field in ("id", "title", "purpose", "reason"):
        if not getattr(candidate, field).strip():
            _refuse(f"topic {candidate.id}.{field}: must not be blank")
    positions = {sentence.id: index for index, sentence in enumerate(evidence.sentences)}
    first, last = _span_positions(positions, candidate, field=f"topic {candidate.id}")
    for field in _SPAN_FIELDS:
        spans: list[TopicSentenceSpan] = getattr(candidate, field)
        if field in {"coreSpans", "completionSpans"} and not spans:
            _refuse(f"topic {candidate.id}.{field}: evidence is required")
        for index, span in enumerate(spans):
            name = f"topic {candidate.id}.{field}[{index}]"
            span_first, span_last = _span_positions(positions, span, field=name)
            if span_first < first or span_last > last:
                _refuse(f"{name}: required evidence lies outside the video")


def validate_topic_proposal(evidence: HarnessEvidence, proposal: TopicProposal) -> None:
    """Validate source references, not whether the model's editorial claim is true."""
    proposal = TopicProposal.model_validate(proposal.model_dump(), strict=True)
    validate_evidence(evidence)
    if not proposal.summary.strip():
        _refuse("topic proposal summary must explain its result")
    seen: set[str] = set()
    for candidate in proposal.candidates:
        if candidate.id in seen:
            _refuse(f"duplicate topic candidate id: {candidate.id}")
        seen.add(candidate.id)
        _validate_candidate(evidence, candidate)


def _membership(evidence: HarnessEvidence, candidate: TopicCandidate) -> _Membership:
    positions = {sentence.id: index for index, sentence in enumerate(evidence.sentences)}
    first, last = _span_positions(positions, candidate, field=f"topic {candidate.id}")
    words = {word.id: index for index, word in enumerate(evidence.words)}
    first_word = words[word_id(evidence.sentences[first].wordIds[0])]
    last_word = words[word_id(evidence.sentences[last].wordIds[-1])]
    return _Membership(
        first,
        last,
        tuple(evidence.words[first_word : last_word + 1]),
        tuple(evidence.words[:first_word]),
        tuple(evidence.words[last_word + 1 :]),
    )


def topic_execution_proposal(
    evidence: HarnessEvidence, candidate: TopicCandidate
) -> ChapterProposal:
    """Make one keep and outside drops; derive all quote anchors from source words."""
    validate_evidence(evidence)
    _validate_candidate(evidence, candidate)
    membership = _membership(evidence, candidate)
    sections: list[ChapterProposalSection] = []
    for side, first, last in (
        ("before", 0, membership.first - 1),
        ("keep", membership.first, membership.last),
        ("after", membership.last + 1, len(evidence.sentences) - 1),
    ):
        if last < first:
            continue
        keep = side == "keep"
        drop_id = f"outside-{side}"
        if drop_id == candidate.id:
            drop_id += "-context"
        anchors = list(dict.fromkeys((membership.words[0].id, membership.words[-1].id)))
        sections.append(
            ChapterProposalSection(
                firstSentenceId=evidence.sentences[first].id,
                id=candidate.id if keep else drop_id,
                kind=Kind.keep if keep else Kind.drop,
                lastSentenceId=evidence.sentences[last].id,
                quoteWordIds=[QuoteWordId(root=identifier) for identifier in anchors]
                if keep
                else [],
                reason=(
                    candidate.reason
                    if keep
                    else "Outside this video's selected extent; may be used by another topic."
                ),
                title=candidate.title if keep else "Outside selected topic",
            )
        )
    return ChapterProposal(sections=sections, summary=candidate.purpose, version=1)


def _indexes(evidence: HarnessEvidence) -> tuple[_IntervalIndex, _IntervalIndex]:
    return (
        _IntervalIndex.build(
            [(word.startMs, word.endMs) for word in evidence.words], evidence.durationMs
        ),
        _IntervalIndex.build(
            [(row.startMs, row.endMs) for row in evidence.speechCoverage.intervals],
            evidence.durationMs,
        ),
    )


def _boundary_constraints(
    evidence: HarnessEvidence, candidate: TopicCandidate
) -> tuple[list[HarnessBoundaryCandidate], tuple[TopicBoundaryIssue, ...]]:
    membership = _membership(evidence, candidate)
    lexical, detected = _indexes(evidence)
    windows: list[tuple[str, tuple[Fraction, Fraction]]] = []
    if membership.before:
        windows.append(("opening", membership.start_window))
    if membership.after:
        windows.append(("ending", membership.end_window))
    found: set[str] = set()
    candidates: list[HarnessBoundaryCandidate] = []
    for boundary in evidence.boundaries:
        if boundary.kind == Kind2.edge:
            candidates.append(boundary)
            continue
        instant = quantize_time(evidence, boundary.timeMs)
        if lexical.inside(instant) or detected.inside(instant):
            continue
        matches = [name for name, (start, end) in windows if start <= instant <= end]
        if matches:
            found.update(matches)
            candidates.append(boundary)
    issues: list[TopicBoundaryIssue] = []
    for name, _window in windows:
        if name not in found:
            indexes = (
                (membership.first - 1, membership.first)
                if name == "opening"
                else (membership.last, membership.last + 1)
            )
            issues.append(
                TopicBoundaryIssue.model_validate(
                    {
                        "edge": name,
                        "code": "no-safe-cut",
                        "reason": (
                            "No grounded cut on the output grid preserves the selected words "
                            "without crossing aligned or detected speech. Revise this semantic "
                            "extent to include enough neighboring context for a safe edge."
                        ),
                        "supportingSentenceIds": [
                            evidence.sentences[index].id for index in indexes
                        ],
                    }
                )
            )
    return candidates, tuple(issues)


def topic_boundary_issues(
    evidence: HarnessEvidence, candidate: TopicCandidate
) -> tuple[TopicBoundaryIssue, ...]:
    """Expose the compiler's exact edge-admission constraints without a model verdict."""
    validate_evidence(evidence)
    _validate_candidate(evidence, candidate)
    return _boundary_constraints(evidence, candidate)[1]


def _safe_candidates(
    evidence: HarnessEvidence, candidate: TopicCandidate
) -> list[HarnessBoundaryCandidate]:
    candidates, issues = _boundary_constraints(evidence, candidate)
    if issues:
        issue = issues[0]
        _refuse(f"topic {candidate.id}.{issue.edge}: no grounded cut. {issue.reason}")
    return candidates


def _mark_unknown_coverage(evidence: HarnessEvidence, edit: ChapterEditSpec) -> ChapterEditSpec:
    if evidence.speechCoverage.status.value != "unknown":
        return edit
    return edit.model_copy(
        update={
            "boundaries": [
                boundary.model_copy(
                    update={
                        "requiresReview": True,
                        "reasons": sorted({*boundary.reasons, _UNKNOWN_SPEECH_REASON}),
                    }
                )
                for boundary in edit.boundaries
            ]
        }
    )


def compile_topics(
    evidence: HarnessEvidence,
    proposal: TopicProposal,
    *,
    evidence_artifact_id: UUID,
    evidence_sha256: str,
    config: CompilerConfig = _DEFAULT_COMPILER_CONFIG,
) -> TopicEditSpec:
    """Compile independent videos without silently removing their selected speech."""
    validate_topic_proposal(evidence, proposal)
    videos: list[TopicCompiledVideo] = []
    for candidate in proposal.candidates:
        execution = topic_execution_proposal(evidence, candidate)
        filtered = evidence.model_copy(update={"boundaries": _safe_candidates(evidence, candidate)})
        edit = compile_chapters(
            filtered,
            execution,
            evidence_artifact_id=evidence_artifact_id,
            evidence_sha256=evidence_sha256,
            config=config,
        )
        videos.append(
            TopicCompiledVideo(
                candidate=candidate.model_copy(deep=True),
                edit=_mark_unknown_coverage(evidence, edit),
                keptSectionId=candidate.id,
            )
        )
    result = TopicEditSpec(
        compilerVersion=TOPIC_COMPILER_VERSION,
        durationMs=evidence.durationMs,
        evidenceArtifactId=evidence_artifact_id,
        evidenceSha256=evidence_sha256,
        sourceId=evidence.sourceId,
        summary=proposal.summary,
        version=1,
        videos=videos,
    )
    validate_topic_edit(evidence, result, expected_evidence_sha256=evidence_sha256)
    return result


def _validate_video(evidence: HarnessEvidence, video: TopicCompiledVideo) -> None:
    expected = topic_execution_proposal(evidence, video.candidate)
    edit = video.edit
    if len(expected.sections) != len(edit.sections):
        _refuse(f"topic {video.candidate.id}: execution section count changed")
    for planned, compiled in zip(expected.sections, edit.sections, strict=True):
        for field in ("id", "kind", "title", "reason", "quoteWordIds"):
            if getattr(planned, field) != getattr(compiled, field):
                _refuse(f"topic {video.candidate.id}: execution {field} changed")
    kept = [index for index, section in enumerate(edit.sections) if section.kind == Kind.keep]
    if len(kept) != 1 or edit.sections[kept[0]].id != video.keptSectionId:
        _refuse(f"topic {video.candidate.id}: execution must keep one candidate")
    start, end = (rational(edit.boundaries[index].time) for index in (kept[0], kept[0] + 1))
    membership = _membership(evidence, video.candidate)
    if any(
        Fraction(word.startMs, 1000) < start or Fraction(word.endMs, 1000) > end
        for word in membership.words
    ):
        _refuse(f"topic {video.candidate.id}: compiled edge removes selected speech")
    if any(Fraction(word.endMs, 1000) > start for word in membership.before) or any(
        Fraction(word.startMs, 1000) < end for word in membership.after
    ):
        _refuse(f"topic {video.candidate.id}: compiled edge changes word membership")
    _validate_video_boundaries(evidence, video)


def _validate_video_boundaries(evidence: HarnessEvidence, video: TopicCompiledVideo) -> None:
    lexical, detected = _indexes(evidence)
    candidates = {boundary.id: boundary for boundary in evidence.boundaries}
    for boundary in video.edit.boundaries:
        instant = rational(boundary.time)
        original = candidates.get(boundary.candidateId or "")
        if original is None:
            _refuse(f"topic {video.candidate.id}: cut must name evidence candidate")
        expected_instant = (
            Fraction(original.timeMs, 1000)
            if original.kind == Kind2.edge
            else quantize_time(evidence, original.timeMs)
        )
        if instant != expected_instant:
            _refuse(f"topic {video.candidate.id}: cut differs from its candidate")
        if original.kind != Kind2.edge and (lexical.inside(instant) or detected.inside(instant)):
            _refuse(f"topic {video.candidate.id}: compiled edge crosses speech")
        if original.requiresReview and not boundary.requiresReview:
            _refuse(f"topic {video.candidate.id}: source uncertainty was discarded")
        if not set(original.reasons).issubset(boundary.reasons):
            _refuse(f"topic {video.candidate.id}: source cut evidence reasons were discarded")
        if evidence.speechCoverage.status.value == "unknown" and (
            not boundary.requiresReview or _UNKNOWN_SPEECH_REASON not in boundary.reasons
        ):
            _refuse(f"topic {video.candidate.id}: unknown speech evidence hidden")


def validate_topic_edit(
    evidence: HarnessEvidence,
    edit: TopicEditSpec,
    *,
    expected_evidence_sha256: str | None = None,
) -> None:
    """Verify portfolio lineage and every video's independent semantic membership."""
    edit = TopicEditSpec.model_validate(edit.model_dump(), strict=True)
    if edit.compilerVersion != TOPIC_COMPILER_VERSION:
        _refuse("topic portfolio names an unsupported compiler version")
    proposal = TopicProposal(
        candidates=[video.candidate for video in edit.videos], summary=edit.summary, version=1
    )
    validate_topic_proposal(evidence, proposal)
    if edit.sourceId != evidence.sourceId or edit.durationMs != evidence.durationMs:
        _refuse("topic portfolio belongs to different source evidence")
    if expected_evidence_sha256 is not None and edit.evidenceSha256 != expected_evidence_sha256:
        _refuse("topic portfolio names a different evidence hash")
    for video in edit.videos:
        if (
            video.edit.evidenceArtifactId != edit.evidenceArtifactId
            or video.edit.evidenceSha256 != edit.evidenceSha256
        ):
            _refuse(f"topic {video.candidate.id}: execution evidence differs")
        validate_edit(evidence, video.edit, expected_evidence_sha256=edit.evidenceSha256)
        _validate_video(evidence, video)


def topic_source_usage(edit: TopicEditSpec) -> TopicSourceUsage:
    """Count union coverage and repeated playback without requiring a partition."""
    intervals: list[tuple[Fraction, Fraction]] = []
    for video in edit.videos:
        boundaries = {boundary.id: rational(boundary.time) for boundary in video.edit.boundaries}
        section = next(item for item in video.edit.sections if item.id == video.keptSectionId)
        intervals.append((boundaries[section.startBoundaryId], boundaries[section.endBoundaryId]))
    merged: list[tuple[Fraction, Fraction]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    unused: list[tuple[Fraction, Fraction]] = []
    cursor = Fraction(0)
    for start, end in merged:
        if start > cursor:
            unused.append((cursor, start))
        cursor = end
    duration = Fraction(edit.durationMs, 1000)
    if cursor < duration:
        unused.append((cursor, duration))
    used = sum((end - start for start, end in merged), Fraction(0))
    return TopicSourceUsage(
        used_intervals=tuple(merged),
        unused_intervals=tuple(unused),
        used_duration=used,
        unused_duration=duration - used,
        repeated_duration=sum((end - start for start, end in intervals), Fraction(0)) - used,
    )
