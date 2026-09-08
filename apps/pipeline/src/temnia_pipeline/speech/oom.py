"""Pure bounded batch downgrade decisions for explicit recognition OOMs."""

from __future__ import annotations

_BATCHES = (16, 8, 4)
_OOM_MARKERS = (
    "cuda out of memory",
    "cuda error: out of memory",
    "cublas_status_alloc_failed",
    "out of memory on device",
)


def is_resource_oom(error: BaseException) -> bool:
    """Recognize only explicit CUDA/CTranslate2 allocation failures."""
    name = type(error).__name__.lower()
    message = str(error).lower()
    explicit_allocator = "failed to allocate" in message and any(
        context in message for context in ("cuda", "gpu", "ctranslate2")
    )
    return (
        "cudaoutofmemory" in name
        or any(marker in message for marker in _OOM_MARKERS)
        or explicit_allocator
    )


def next_batch_size(current: int, error: BaseException) -> int | None:
    """Return 16→8→4 only for recognized OOM; unknown failures never advance."""
    if not is_resource_oom(error):
        return None
    try:
        index = _BATCHES.index(current)
    except ValueError:
        return None
    return _BATCHES[index + 1] if index + 1 < len(_BATCHES) else None
