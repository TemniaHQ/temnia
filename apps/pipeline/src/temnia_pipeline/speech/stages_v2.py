"""Protocol-v2 GPU stages over immutable independent checkpoints."""

# ruff: noqa: EM101, EM102, PLR0913, PLR0917, TC001, TRY003

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from temnia_pipeline.speech.checkpoints import CheckpointStore
from temnia_pipeline.speech.checkpoints_v2 import (
    checkpoint_for_v2,
    publish_checkpoint_v2,
    read_input_checkpoint_v2,
    reuse_checkpoint_v2,
)
from temnia_pipeline.speech.contracts import ExecutionIdentity
from temnia_pipeline.speech.contracts_v2 import (
    AlignConfigV2,
    ArtifactRefV2,
    RecognizeConfigV2,
    SpeakerTurnsConfig,
    SpeechStageJobV2,
)
from temnia_pipeline.speech.stages import diarization_rows, observe_model_provenance

if TYPE_CHECKING:
    from temnia_pipeline.speech.telemetry import TelemetryRecorder

_ENGLISH_TORCHAUDIO_MODEL = "WAV2VEC2_ASR_BASE_960H"
_ENGLISH_TORCHAUDIO_FILE = "wav2vec2_fairseq_base_ls960_asr_ls960.pth"


def alignment_model_cache(job: SpeechStageJobV2) -> Path:
    """Return a verified offline cache path before the alignment loader can fetch."""
    root = Path(job.model_manifest.model_root)
    if job.configuration.stage != "align":
        raise TypeError("alignment cache requires an align job")
    config = job.configuration
    if config.language == "en" and config.model in {None, _ENGLISH_TORCHAUDIO_MODEL}:
        cache = root / "hub" / "checkpoints"
        required = cache / _ENGLISH_TORCHAUDIO_FILE
        if not required.is_file():
            raise FileNotFoundError(f"frozen English alignment model is missing: {required}")
        return cache
    # The pinned WhisperX loader applies `model_cache_only` to Hugging Face
    # alignment models. Other torchaudio defaults are refused until their exact
    # asset paths are added to the frozen manifest verifier.
    if config.model is None and config.language in {"fr", "de", "es", "it"}:
        raise FileNotFoundError(
            f"frozen torchaudio alignment asset is not declared for {config.language}"
        )
    if config.model is not None and config.model.startswith("WAV2VEC2_"):
        raise FileNotFoundError("frozen explicit torchaudio alignment asset is not declared")
    return root / "hub"


async def run_recognize_v2(
    engine: Any,  # noqa: ANN401
    store: CheckpointStore,
    job: SpeechStageJobV2,
    audio_file: Path,
    build: str,
    telemetry: TelemetryRecorder,
    execution: ExecutionIdentity | None = None,
) -> ArtifactRefV2:
    """Recognize audio without coupling it to speaker turns."""
    if not isinstance(job.configuration, RecognizeConfigV2):
        raise TypeError("recognize requires RecognizeConfigV2")
    if reused := await reuse_checkpoint_v2(store, job):
        return reused
    with telemetry.phase("modelLoad"):
        model = engine.load_model(
            job.configuration.model,
            device="cuda",
            compute_type=job.configuration.compute_type,
            language=job.configuration.language,
            vad_method=job.configuration.vad_method,
            vad_options=job.configuration.vad_options,
            download_root=str(Path(job.model_manifest.model_root) / "hub"),
            local_files_only=True,
            threads=job.resource_profile.cpu_cores,
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
    checkpoint = checkpoint_for_v2(job, build, payload, execution, provenance)
    with telemetry.phase("checkpointWrite"):
        return await publish_checkpoint_v2(store, job, checkpoint)


async def run_align_v2(
    engine: Any,  # noqa: ANN401
    store: CheckpointStore,
    job: SpeechStageJobV2,
    audio_file: Path,
    build: str,
    telemetry: TelemetryRecorder,
    execution: ExecutionIdentity | None = None,
) -> ArtifactRefV2:
    """Align an accepted recognition checkpoint."""
    if not isinstance(job.configuration, AlignConfigV2):
        raise TypeError("align requires AlignConfigV2")
    if reused := await reuse_checkpoint_v2(store, job):
        return reused
    source = await read_input_checkpoint_v2(store, job)
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
            model_dir=str(alignment_model_cache(job)),
            model_cache_only=True,
        )
        audio = engine.load_audio(str(audio_file))
    with telemetry.phase("inference"):
        result = cast(
            "dict[str, object]",
            engine.align(
                list(segment_objects),
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
        model,
        requested_model=job.configuration.model,
        extra=metadata,
    )
    checkpoint = checkpoint_for_v2(job, build, payload, execution, provenance)
    with telemetry.phase("checkpointWrite"):
        return await publish_checkpoint_v2(store, job, checkpoint)


async def run_speaker_turns_v2(
    engine: Any,  # noqa: ANN401
    store: CheckpointStore,
    job: SpeechStageJobV2,
    audio_file: Path,
    build: str,
    telemetry: TelemetryRecorder,
    token: str,
    execution: ExecutionIdentity | None = None,
) -> ArtifactRefV2:
    """Publish raw speaker turns without waiting for aligned words."""
    if not isinstance(job.configuration, SpeakerTurnsConfig):
        raise TypeError("speaker_turns requires SpeakerTurnsConfig")
    if reused := await reuse_checkpoint_v2(store, job):
        return reused
    with telemetry.phase("modelLoad"):
        diarizer = engine.diarize.DiarizationPipeline(
            model_name=job.configuration.model,
            token=token,
            device="cuda",
            cache_dir=str(Path(job.model_manifest.model_root) / "hub"),
        )
        audio = engine.load_audio(str(audio_file))
    with telemetry.phase("inference"):
        turns = diarizer(
            audio,
            min_speakers=job.configuration.min_speakers,
            max_speakers=job.configuration.max_speakers,
            progress_callback=telemetry.progress_callback,
        )
    payload: dict[str, object] = {"diarization": diarization_rows(turns)}
    provenance = observe_model_provenance(diarizer, requested_model=job.configuration.model)
    checkpoint = checkpoint_for_v2(job, build, payload, execution, provenance)
    with telemetry.phase("checkpointWrite"):
        return await publish_checkpoint_v2(store, job, checkpoint)
