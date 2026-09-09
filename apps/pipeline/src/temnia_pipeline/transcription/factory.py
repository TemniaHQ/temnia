"""Pick the transcription provider from settings, once, when the worker boots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from temnia_pipeline.transcription.recorded import RecordedProvider
from temnia_pipeline.transcription.runner import (
    POLL_SECONDS,
    RECORDED_POLL_SECONDS,
    TranscriptionRunner,
)

if TYPE_CHECKING:
    from obstore.store import S3Store

    from temnia_pipeline.settings import PipelineSettings


def make_transcription(settings: PipelineSettings, store: S3Store) -> TranscriptionRunner:
    """The provider `TRANSCRIPTION_PROVIDER` asks for, wrapped in its poll loop.

    The Modal modules are imported only when they are the chosen provider, so a
    worker replaying recordings never loads the SDK.
    """
    if settings.transcription.provider in {"modal", "modal-checkpointed"}:
        from temnia_pipeline.transcription.modal_whisperx import (  # noqa: PLC0415
            ModalWhisperXProvider,
        )

        # Checkpointed new histories use SpeechActivities. Keeping the v4
        # runner here lets pre-patch workflow history reattach to temnia-media.
        return TranscriptionRunner(
            ModalWhisperXProvider(settings.transcription), poll_seconds=POLL_SECONDS
        )
    return TranscriptionRunner(
        RecordedProvider(settings.transcription, store), poll_seconds=RECORDED_POLL_SECONDS
    )
