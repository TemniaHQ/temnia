"""Independent WhisperX stage engines over immutable checkpoint storage."""

# ruff: noqa: C901, EM101, PLR0913, PLR0917, S112, TRY003

from __future__ import annotations

import copy
import math
import re
from importlib import metadata as package_metadata
from typing import TYPE_CHECKING, Any, cast

from temnia_pipeline.speech.checkpoints import (
    CheckpointStore,
    checkpoint_for,
    publish_checkpoint,
    read_input_checkpoint,
    reuse_checkpoint,
)
from temnia_pipeline.speech.contracts import (
    AlignConfig,
    DiarizeConfig,
    ExecutionIdentity,
    RecognizeConfig,
    SpeechStageJob,
    StageModelProvenance,
)

if TYPE_CHECKING:
    from pathlib import Path

    from temnia_pipeline.speech.telemetry import TelemetryRecorder

_HF_SNAPSHOT = re.compile(
    r"(?:^|/)models--(?P<organization>[^/]+)--(?P<repository>[^/]+)/"
    r"snapshots/(?P<revision>[0-9a-f]{40,64})(?:/|$)"
)
_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_MODEL_ATTRIBUTES = ("model_name_or_path", "name_or_path", "model_path", "model_id")
_NESTED_MODEL_ATTRIBUTES = ("model", "pipeline", "config")
_PACKAGES = (
    "whisperx",
    "torch",
    "torchaudio",
    "ctranslate2",
    "faster-whisper",
    "pyannote-audio",
    "transformers",
)


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in _PACKAGES:
        try:
            versions[package] = package_metadata.version(package)
        except package_metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _model_candidates(model: object, extra: object | None) -> list[object]:
    """Inspect only a small allowlist of already-loaded model objects."""
    candidates = [model]
    if extra is not None:
        candidates.append(extra)
    for _depth in range(2):
        for candidate in tuple(candidates):
            if isinstance(candidate, dict):
                mapping = cast("dict[str, object]", candidate)
                for name in _NESTED_MODEL_ATTRIBUTES:
                    nested = mapping.get(name)
                    if nested is not None and all(nested is not item for item in candidates):
                        candidates.append(nested)
                continue
            for name in _NESTED_MODEL_ATTRIBUTES:
                try:
                    nested = getattr(candidate, name, None)
                except Exception:  # noqa: BLE001
                    continue
                if nested is not None and all(nested is not item for item in candidates):
                    candidates.append(nested)
    return candidates


def observe_model_provenance(
    model: object, *, requested_model: str | None, extra: object | None = None
) -> StageModelProvenance:
    """Record loaded-object facts without inventing a model revision or weight hash."""
    resolved_model: str | None = None
    resolved_revision: str | None = None
    for candidate in _model_candidates(model, extra):
        values: list[str] = []
        if isinstance(candidate, dict):
            mapping = cast("dict[str, object]", candidate)
            for name in (*_MODEL_ATTRIBUTES, "_commit_hash", "commit_hash", "revision"):
                value = mapping.get(name)
                if isinstance(value, str) and value:
                    values.append(value)
        else:
            for name in (*_MODEL_ATTRIBUTES, "_commit_hash", "commit_hash", "revision"):
                try:
                    value = getattr(candidate, name, None)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(value, str) and value:
                    values.append(value)
        for value in values:
            match = _HF_SNAPSHOT.search(value)
            if match:
                resolved_model = f"{match.group('organization')}/{match.group('repository')}"
                resolved_revision = match.group("revision")
            elif _REVISION.fullmatch(value):
                resolved_revision = value
            elif "/" in value and not value.startswith("/"):
                resolved_model = value
    return StageModelProvenance(
        requested_model=requested_model,
        loaded_class=f"{type(model).__module__}.{type(model).__qualname__}",
        resolved_model=resolved_model,
        resolved_revision=resolved_revision,
        weight_sha256=None,
        library_versions=_package_versions(),
        # Current configs request mutable aliases and do not pass an exact revision
        # into WhisperX loaders. Observations are audit evidence, not cache identity.
        identity_status="observed_unpinned",
        reuse_scope="run",
    )


def diarization_rows(value: Any) -> list[dict[str, object]]:  # noqa: ANN401
    """Preserve independent turn intervals without retaining a pandas object."""
    found = value.to_dict("records")
    if not isinstance(found, list):
        raise TypeError("diarization rows are not a list")
    records = cast("list[object]", found)
    rows: list[dict[str, object]] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            message = f"diarization row {index} is not an object"
            raise TypeError(message)
        record = cast("dict[str, object]", item)
        speaker = record.get("speaker")
        start = record.get("start")
        end = record.get("end")
        if (
            not isinstance(speaker, str)
            or not speaker.strip()
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int | float)
            or not isinstance(end, int | float)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(end) <= float(start)
        ):
            message = f"diarization row {index} has invalid interval identity"
            raise ValueError(message)
        rows.append({"start": float(start), "end": float(end), "speaker": speaker})
    return rows


async def run_recognize(
    engine: Any,  # noqa: ANN401
    store: CheckpointStore,
    job: SpeechStageJob,
    audio_file: Path,
    build: str,
    telemetry: TelemetryRecorder,
    execution: ExecutionIdentity | None = None,
) -> Any:  # noqa: ANN401
    """Recognize one source, returning a ref and never an array payload."""
    if not isinstance(job.configuration, RecognizeConfig):
        raise TypeError("recognize requires RecognizeConfig")
    if reused := await reuse_checkpoint(store, job):
        return reused
    with telemetry.phase("modelLoad"):
        model = engine.load_model(
            job.configuration.model,
            device="cuda",
            compute_type=job.configuration.compute_type,
            language=job.configuration.language,
            vad_method=job.configuration.vad_method,
            vad_options=job.configuration.vad_options,
        )
        audio = engine.load_audio(str(audio_file))
    with telemetry.phase("inference"):
        result = cast(
            "dict[str, object]",
            model.transcribe(
                audio,
                batch_size=job.configuration.batch_size,
                progress_callback=telemetry.progress_callback,
            ),
        )
    payload = {
        "language": str(result.get("language") or "und"),
        "segments": result.get("segments", []),
    }
    provenance = observe_model_provenance(model, requested_model=job.configuration.model)
    checkpoint = checkpoint_for(job, build, payload, execution, provenance)
    with telemetry.phase("checkpointWrite"):
        return await publish_checkpoint(store, job, checkpoint)


async def run_align(
    engine: Any,  # noqa: ANN401
    store: CheckpointStore,
    job: SpeechStageJob,
    audio_file: Path,
    build: str,
    telemetry: TelemetryRecorder,
    execution: ExecutionIdentity | None = None,
) -> Any:  # noqa: ANN401
    """Align an accepted recognition checkpoint without rerunning recognition."""
    if not isinstance(job.configuration, AlignConfig):
        raise TypeError("align requires AlignConfig")
    if reused := await reuse_checkpoint(store, job):
        return reused
    source = await read_input_checkpoint(store, job)
    if source is None:
        raise ValueError("align input checkpoint is required")
    segments = source.payload.get("segments")
    if not isinstance(segments, list):
        raise TypeError("recognition checkpoint has no segment list")
    segment_objects = cast("list[object]", segments)
    with telemetry.phase("modelLoad"):
        model, metadata = engine.load_align_model(
            language_code=job.configuration.language,
            device="cuda",
            model_name=job.configuration.model,
        )
        audio = engine.load_audio(str(audio_file))
    with telemetry.phase("inference"):
        result = cast(
            "dict[str, object]",
            engine.align(
                copy.deepcopy(segment_objects),
                model,
                metadata,
                audio,
                "cuda",
                return_char_alignments=False,
                progress_callback=telemetry.progress_callback,
            ),
        )
    payload = {
        "language": job.configuration.language,
        "segments": result.get("segments", []),
        "word_segments": result.get("word_segments", []),
    }
    provenance = observe_model_provenance(
        model, requested_model=job.configuration.model, extra=metadata
    )
    checkpoint = checkpoint_for(job, build, payload, execution, provenance)
    with telemetry.phase("checkpointWrite"):
        return await publish_checkpoint(store, job, checkpoint)


async def run_diarize(
    engine: Any,  # noqa: ANN401
    store: CheckpointStore,
    job: SpeechStageJob,
    audio_file: Path,
    build: str,
    telemetry: TelemetryRecorder,
    token: str,
    execution: ExecutionIdentity | None = None,
) -> Any:  # noqa: ANN401
    """Diarize accepted alignment and retain both final raw and independent turns."""
    if not isinstance(job.configuration, DiarizeConfig):
        raise TypeError("diarize requires DiarizeConfig")
    if reused := await reuse_checkpoint(store, job):
        return reused
    source = await read_input_checkpoint(store, job)
    if source is None:
        raise ValueError("diarize input checkpoint is required")
    raw = copy.deepcopy(source.payload)
    with telemetry.phase("modelLoad"):
        diarizer = engine.diarize.DiarizationPipeline(
            model_name=job.configuration.model, token=token, device="cuda"
        )
        audio = engine.load_audio(str(audio_file))
    with telemetry.phase("inference"):
        turns = diarizer(
            audio,
            min_speakers=job.configuration.min_speakers,
            max_speakers=job.configuration.max_speakers,
            progress_callback=telemetry.progress_callback,
        )
        assigned = cast(
            "dict[str, object]",
            engine.assign_word_speakers(turns, raw, fill_nearest=job.configuration.fill_nearest),
        )
    assigned["language"] = str(source.payload.get("language") or "und")
    payload: dict[str, object] = {
        "raw": assigned,
        "diarization": diarization_rows(turns),
    }
    provenance = observe_model_provenance(diarizer, requested_model=job.configuration.model)
    checkpoint = checkpoint_for(job, build, payload, execution, provenance)
    with telemetry.phase("checkpointWrite"):
        return await publish_checkpoint(store, job, checkpoint)
