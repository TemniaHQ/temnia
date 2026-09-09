"""Small Modal adapter for bounded inference-independent progress delivery."""

from __future__ import annotations

from typing import Any, Literal

from temnia_pipeline.speech.progress_transport import (
    CoalescedProgressPublisher,
    SynchronousProgressPublisher,
)


def progress_publisher(  # noqa: PLR0913
    *,
    protocol: str,
    dict_name: str,
    stage: str,
    attempt_id: str,
    call_id: str | None,
    task_id: str | None,
    mode: Literal["coalesced", "synchronous_control"] = "coalesced",
) -> CoalescedProgressPublisher | SynchronousProgressPublisher:
    """Keep Modal I/O inside the publisher, with an explicit benchmark control."""
    notes: Any = None

    def payload(percent: int) -> dict[str, object]:
        return {
            "protocol": protocol,
            "stage": stage,
            "percent": percent,
            "source": "whisperx_callback",
            "modalCallId": call_id,
            "modalTaskId": task_id,
        }

    def get_notes() -> Any:  # noqa: ANN401
        nonlocal notes
        if notes is None:
            import modal  # noqa: PLC0415

            notes = modal.Dict.from_name(dict_name, create_if_missing=True)
        return notes

    async def publish(percent: int) -> None:
        await get_notes().put.aio(attempt_id, payload(percent))

    def publish_control(percent: int) -> None:
        get_notes().put(attempt_id, payload(percent))

    if mode == "synchronous_control":
        return SynchronousProgressPublisher(publish_control)
    return CoalescedProgressPublisher(publish)
