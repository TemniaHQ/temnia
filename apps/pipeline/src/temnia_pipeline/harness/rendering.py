"""Pure chapter render identities, intervals, captions and descriptor assembly."""

# ruff: noqa: C901, EM101, TRY003

from __future__ import annotations

import asyncio
import html
import math
import shutil
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Any, cast

from temnia_pipeline.contracts import (
    ChapterEditSpec,
    ChapterRender,
    ChapterRenders,
    HarnessEvidence,
    Kind,
)
from temnia_pipeline.harness.artifacts import fingerprint_for
from temnia_pipeline.media.timeline_identity import fraction_json, timeline_identity

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence
    from pathlib import Path
    from uuid import UUID

    from temnia_pipeline.media.chapters import ChapterRenderConfig, MediaTimelineFacts

DISK_MARGIN_BYTES = 1024 * 1024 * 1024
OUTPUT_GROWTH_NUMERATOR = 2
OUTPUT_GROWTH_DENOMINATOR = 1
MAX_CUE_WORDS = 12
MAX_CUE_SECONDS = Fraction(6)
MAX_CUE_GAP_SECONDS = Fraction(1)
MEDIA_FORMAT = "chapter-media/1"
CAPTION_FORMAT = "chapter-captions/1"
CHECKS_FORMAT = "chapter-checks/1"
DESCRIPTOR_FORMAT = "chapter-renders/1"


@dataclass(frozen=True, slots=True)
class RenderSection:
    """One kept source interval; title/review state do not define media bytes."""

    section_id: str
    start: Fraction
    end: Fraction

    @property
    def duration(self) -> Fraction:
        """Exact interval length in seconds."""
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class CaptionDocument:
    """Stable sidecar bytes plus review warnings for cut-crossing words."""

    body: str
    cue_count: int
    warnings: tuple[str, ...]


def as_fraction(value: Any) -> Fraction:  # noqa: ANN401
    """Convert a public RationalTime model to an exact internal Fraction."""
    return Fraction(int(value.numerator), int(value.denominator))


def kept_sections(edit: ChapterEditSpec) -> tuple[RenderSection, ...]:
    """Resolve every keep against shared boundaries, preserving edit order."""
    boundaries = {boundary.id: as_fraction(boundary.time) for boundary in edit.boundaries}
    if len(boundaries) != len(edit.boundaries):
        raise ValueError("chapter edit contains duplicate boundary ids")
    found: list[RenderSection] = []
    previous_end = Fraction(0)
    for section in edit.sections:
        try:
            start = boundaries[section.startBoundaryId]
            end = boundaries[section.endBoundaryId]
        except KeyError as error:
            message = f"section {section.id} names an absent boundary"
            raise ValueError(message) from error
        if start != previous_end or end <= start:
            raise ValueError("chapter sections must form an ordered positive exact cover")
        previous_end = end
        if section.kind == Kind.keep:
            found.append(RenderSection(section.id, start, end))
    if previous_end != Fraction(edit.durationMs, 1000):
        raise ValueError("chapter sections do not cover the source duration")
    if len({section.section_id for section in found}) != len(found):
        raise ValueError("kept section ids must be unique")
    return tuple(found)


def media_fingerprint(
    *,
    source_fingerprint: str,
    section: RenderSection,
    timeline: MediaTimelineFacts,
    config: ChapterRenderConfig,
) -> str:
    """Identify media by source interval and encoder, independent of edit labels."""
    return fingerprint_for(
        kind="chapter_media",
        inputs={
            "end": fraction_json(section.end),
            "sourceFingerprint": source_fingerprint,
            "start": fraction_json(section.start),
            "timeline": timeline_identity(timeline),
        },
        config=asdict(config),
    )


def media_metadata(
    *,
    source_fingerprint: str,
    section: RenderSection,
    timeline: MediaTimelineFacts,
    renderer_version: str,
) -> dict[str, object]:
    """Describe reusable bytes without falsely associating them with one edit."""
    return {
        "audioStreamIndex": timeline.audio_stream_index,
        "end": fraction_json(section.end),
        "format": MEDIA_FORMAT,
        "rendererVersion": renderer_version,
        "sourceFingerprint": source_fingerprint,
        "sourceStart": fraction_json(timeline.source_start),
        "start": fraction_json(section.start),
        "timeline": timeline_identity(timeline),
        "videoStreamIndex": timeline.video_stream_index,
    }


def caption_metadata(*, section: RenderSection, evidence_sha256: str) -> dict[str, object]:
    """Describe reusable caption bytes and their exact transcript evidence."""
    return {
        "end": fraction_json(section.end),
        "evidenceSha256": evidence_sha256,
        "format": CAPTION_FORMAT,
        "start": fraction_json(section.start),
    }


def checks_metadata(*, section_id: str, edit_sha256: str) -> dict[str, object]:
    """Associate actual-file checks with the edit and section they qualify."""
    return {
        "editSha256": edit_sha256,
        "format": CHECKS_FORMAT,
        "sectionId": section_id,
    }


def descriptor_metadata(*, run_id: UUID, edit_sha256: str, render_count: int) -> dict[str, object]:
    """Expose the discriminator and current association used by descriptor readers."""
    if render_count < 0:
        raise ValueError("render count cannot be negative")
    return {
        "editSha256": edit_sha256,
        "format": DESCRIPTOR_FORMAT,
        "renderCount": render_count,
        "runId": str(run_id),
    }


def _speaker_labels(evidence: HarnessEvidence) -> dict[str, str]:
    raw = evidence.config.get("speakerIdentities", {})
    if not isinstance(raw, dict):
        return {}
    labels: dict[str, str] = {}
    for speaker, item in cast("dict[object, object]", raw).items():
        if isinstance(speaker, str) and isinstance(item, dict):
            label = cast("dict[str, object]", item).get("label")
            if isinstance(label, str) and label.strip():
                labels[speaker] = label.strip()
    return labels


def caption_fingerprint(
    *,
    evidence: HarnessEvidence,
    evidence_sha256: str,
    section: RenderSection,
) -> str:
    """Identify captions by exact source words/times plus evidence bytes."""
    start_ms = section.start * 1000
    end_ms = section.end * 1000
    words = [
        {
            "endMs": word.endMs,
            "id": word.id,
            "speaker": word.speaker,
            "startMs": word.startMs,
            "text": word.text,
            "timing": str(word.timing),
        }
        for word in evidence.words
        if Fraction(word.endMs) > start_ms and Fraction(word.startMs) < end_ms
    ]
    return fingerprint_for(
        kind="chapter_captions",
        inputs={
            "end": fraction_json(section.end),
            "evidenceSha256": evidence_sha256,
            "start": fraction_json(section.start),
            "words": words,
        },
        config={
            "format": CAPTION_FORMAT,
            "maxCueGapSeconds": fraction_json(MAX_CUE_GAP_SECONDS),
            "maxCueSeconds": fraction_json(MAX_CUE_SECONDS),
            "maxCueWords": MAX_CUE_WORDS,
            "speakerLabels": _speaker_labels(evidence),
        },
    )


def _timestamp(value: Fraction) -> str:
    milliseconds = max(0, math.floor(value * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


@dataclass(frozen=True, slots=True)
class _CueWord:
    text: str
    speaker: str | None
    start: Fraction
    end: Fraction


def build_captions(evidence: HarnessEvidence, section: RenderSection) -> CaptionDocument:
    """Create bounded readable WebVTT cues from words intersecting the source cut."""
    duration = section.duration
    labels = _speaker_labels(evidence)
    words: list[_CueWord] = []
    warnings: list[str] = []
    for word in evidence.words:
        absolute_start = Fraction(word.startMs, 1000)
        absolute_end = Fraction(word.endMs, 1000)
        if absolute_end <= section.start or absolute_start >= section.end:
            continue
        if absolute_start < section.start or absolute_end > section.end:
            warnings.append(f"word {word.id} crosses a chapter cut and its cue was clipped")
        local_start = max(Fraction(0), absolute_start - section.start)
        local_end = min(duration, absolute_end - section.start)
        if local_end <= local_start:
            continue
        words.append(
            _CueWord(
                text=html.escape(" ".join(word.text.replace("-->", "→").split()), quote=False),
                speaker=word.speaker,
                start=local_start,
                end=local_end,
            )
        )
    cues: list[list[_CueWord]] = []
    for word in words:
        if not cues:
            cues.append([word])
            continue
        current = cues[-1]
        starts_new = (
            word.speaker != current[-1].speaker
            or len(current) >= MAX_CUE_WORDS
            or word.start - current[-1].end > MAX_CUE_GAP_SECONDS
            or word.end - current[0].start > MAX_CUE_SECONDS
        )
        if starts_new:
            cues.append([word])
        else:
            current.append(word)
    lines = ["WEBVTT", ""]
    for index, cue in enumerate(cues, start=1):
        start = max(Fraction(0), cue[0].start)
        end = min(duration, max(word.end for word in cue))
        if end <= start:
            continue
        text = " ".join(word.text for word in cue)
        speaker = cue[0].speaker
        if speaker:
            label = " ".join(labels.get(speaker, speaker).replace("-->", "→").split())
            text = f"{html.escape(label, quote=False)}: {text}"
        lines.extend([str(index), f"{_timestamp(start)} --> {_timestamp(end)}", text, ""])
    return CaptionDocument(
        body="\n".join(lines),
        cue_count=len(cues),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def write_captions(
    path: Path, evidence: HarnessEvidence, section: RenderSection
) -> CaptionDocument:
    """Write stable UTF-8 WebVTT bytes, including the required terminal newline."""
    document = build_captions(evidence, section)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.body, encoding="utf-8")
    return document


def required_disk_bytes(
    *, source_size_bytes: int, source_duration: Fraction, sections: Sequence[RenderSection]
) -> int:
    """Conservative source + 2x proportional outputs + fixed scratch margin."""
    if source_size_bytes <= 0 or source_duration <= 0:
        raise ValueError("source size and duration must be positive")
    kept = sum((section.duration for section in sections), start=Fraction(0))
    proportional = Fraction(source_size_bytes) * kept / source_duration
    output_bound = math.ceil(proportional * OUTPUT_GROWTH_NUMERATOR / OUTPUT_GROWTH_DENOMINATOR)
    return source_size_bytes + output_bound + DISK_MARGIN_BYTES


def preflight_disk(path: Path, required_bytes: int) -> int:
    """Refuse before download/render when the workspace cannot hold bounded work."""
    if required_bytes <= 0:
        raise ValueError("required disk bytes must be positive")
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < required_bytes:
        message = f"workspace operation needs {required_bytes} bytes; {free} bytes are free"
        raise OSError(message)
    return free


async def run_render_batch[T](
    jobs: Sequence[Callable[[], Coroutine[Any, Any, T]]],
) -> tuple[T, ...]:
    """Cancel and drain every sibling before surfacing the first render failure."""
    tasks: list[asyncio.Task[T]] = []
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(job()) for job in jobs]
    return tuple(task.result() for task in tasks)


def build_render_descriptor(
    *, run_id: UUID, edit_sha256: str, renders: Sequence[ChapterRender]
) -> ChapterRenders:
    """Stamp every current keep with the current edit, including reused media."""
    if any(render.editSha256 != edit_sha256 for render in renders):
        raise ValueError("descriptor render association does not match its current edit")
    section_ids = [render.sectionId for render in renders]
    if len(set(section_ids)) != len(section_ids):
        raise ValueError("render descriptor contains duplicate section ids")
    return ChapterRenders(
        editSha256=edit_sha256,
        format="chapter-renders/1",
        renders=list(renders),
        runId=run_id,
    )


def descriptor_fingerprint(
    *, edit_sha256: str, renders: Sequence[ChapterRender], renderer_version: str
) -> str:
    """Bind a descriptor to the edit and all actual component identities."""
    components = [
        {
            "captions": render.captions.model_dump(mode="json") if render.captions else None,
            "checks": render.checks.model_dump(mode="json") if render.checks else None,
            "media": render.media.model_dump(mode="json"),
            "sectionId": render.sectionId,
        }
        for render in renders
    ]
    return fingerprint_for(
        kind="chapter_renders",
        inputs={"components": components, "editSha256": edit_sha256},
        config={"rendererVersion": renderer_version},
    )
