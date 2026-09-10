"""Internal prompt identities for durable dispatch and retained editorial responses."""

from __future__ import annotations

from temnia_pipeline.harness.validators import HarnessValidationError

LOCAL_TIMING_EVIDENCE_GENERATION = 3


def editorial_prompt_generation(version: object, *, repair: bool = False) -> int:
    """Accept only the exact supported identity for the requested editorial seat."""
    prefix = "chapter-editorial-repair-v" if repair else "chapter-editorial-assess-v"
    supported = (1, 2, 3, 4) if repair else (1, 2, 3)
    for generation in supported:
        if version == f"{prefix}{generation}":
            return generation
    message = "editorial response has an unsupported prompt version"
    raise HarnessValidationError(message)


def editorial_dispatch_version(version: str | None, *, repair: bool = False) -> str:
    """Old prepared plans omitted this field and dispatched the recorded v2 generation."""
    if version is None:
        return "chapter-editorial-repair-v2" if repair else "chapter-editorial-assess-v2"
    editorial_prompt_generation(version, repair=repair)
    return version
