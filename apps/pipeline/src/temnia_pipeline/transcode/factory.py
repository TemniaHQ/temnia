"""Pick the backend from settings, once, when the worker boots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from temnia_pipeline.transcode.local import LocalTranscoder

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store

    from temnia_pipeline.settings import PipelineSettings
    from temnia_pipeline.transcode import Transcoder


def make_transcoder(settings: PipelineSettings, store: S3Store, work_root: Path) -> Transcoder:
    """The transcoder `TRANSCODE_BACKEND` asks for.

    The Modal modules are imported only when they are the chosen backend, so a
    worker running the local ladder never loads the SDK.
    """
    if settings.transcode.backend == "modal":
        from temnia_pipeline.transcode.modal import ModalTranscoder  # noqa: PLC0415
        from temnia_pipeline.transcode.modal_client import RealModalClient  # noqa: PLC0415

        return ModalTranscoder(RealModalClient(settings.transcode), store)
    return LocalTranscoder(settings, store, work_root)
