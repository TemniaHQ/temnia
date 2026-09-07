"""Build a segmenter by name, the way the transcode and transcription seams do.

The eval runner and, later, the harness name a segmenter and hand it
parameters; nothing outside this module imports an implementation. The heavy
imports are inside the branches, so naming `legacy` never loads torch.

Parameters arrive from a command line, so every one of them is checked here
with a message that says what was given and what was wanted. A typo in a
parameter name is an error rather than a silently ignored key: an eval row
labelled with a target that was never applied is worse than a failed run.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from temnia_pipeline.substrate.protocol import Segmenter

#: Every segmenter `make_segmenter` knows, in the order the runner prints them.
SEGMENTER_NAMES = ("legacy", "sat", "changepoint")


def _unknown(name: str, params: dict[str, object], allowed: tuple[str, ...]) -> None:
    extra = sorted(set(params) - set(allowed))
    if extra:
        msg = (
            f"{name} takes no parameter {', '.join(extra)}; "
            f"it takes {', '.join(allowed) if allowed else 'none'}"
        )
        raise ValueError(msg)


def _text(params: dict[str, object], key: str, default: str | None) -> str | None:
    value = params.get(key, default)
    if value is None or isinstance(value, str):
        return value
    msg = f"{key} must be text, not {type(value).__name__}"
    raise TypeError(msg)


def _number(params: dict[str, object], key: str, default: float | None) -> float | None:
    value = params.get(key, default)
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return None if value is None else float(value)
    msg = f"{key} must be a number, not {type(value).__name__}"
    raise TypeError(msg)


def _whole(params: dict[str, object], key: str, default: int) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        msg = f"{key} must be a whole number, not {value!r}"
        raise TypeError(msg)
    return int(value)


def _flag(params: dict[str, object], key: str, default: bool) -> bool:  # noqa: FBT001
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    msg = f"{key} must be true or false, not {value!r}"
    raise TypeError(msg)


def make_segmenter(name: str, **params: object) -> Segmenter:
    """The segmenter `name` asks for, with its parameters checked.

    `changepoint` takes `sentences_from`, the name of the segmenter whose
    sentences it runs over, and builds it through this same function, so
    `--segmenter changepoint sentences_from=legacy` is a row the runner can
    print beside the others.
    """
    if name == "legacy":
        from temnia_pipeline.substrate.legacy_rules import LegacyRulesSegmenter  # noqa: PLC0415

        _unknown(name, params, ())
        return LegacyRulesSegmenter()

    if name == "sat":
        from temnia_pipeline.substrate.sat import DEFAULT_SAT_MODEL, SaTSegmenter  # noqa: PLC0415

        allowed = ("model", "style_or_domain", "language", "threshold", "paragraphs")
        _unknown(name, params, allowed)
        return SaTSegmenter(
            _text(params, "model", DEFAULT_SAT_MODEL) or DEFAULT_SAT_MODEL,
            style_or_domain=_text(params, "style_or_domain", None),
            language=_text(params, "language", None),
            threshold=_number(params, "threshold", None),
            paragraphs=_flag(params, "paragraphs", default=False),
        )

    if name == "changepoint":
        from temnia_pipeline.substrate.changepoint import (  # noqa: PLC0415
            DEFAULT_EMBEDDING_MODEL,
            DEFAULT_TARGET_PER_HOUR,
            EmbeddingChangePointSegmenter,
        )

        # No `jump`: ruptures' KernelCPD ignores it (its reference says "not
        # considered, set to 1"), and a parameter that is recorded but not
        # applied is worse than none (S2 review, I14).
        allowed = ("sentences_from", "embedding_model", "target_per_hour", "min_sentences")
        _unknown(name, params, allowed)
        base = _text(params, "sentences_from", "sat") or "sat"
        if base == "changepoint":
            msg = "changepoint cannot take its sentences from changepoint"
            raise ValueError(msg)
        # Defaulted only when absent. `or DEFAULT` turned an explicit zero into
        # six an hour and recorded six on the row (S2 review, I14).
        target = _number(params, "target_per_hour", None)
        if target is None:
            target = DEFAULT_TARGET_PER_HOUR
        if not math.isfinite(target) or target <= 0:
            msg = f"target_per_hour must be a positive number, not {target!r}"
            raise ValueError(msg)
        return EmbeddingChangePointSegmenter(
            make_segmenter(base),
            _text(params, "embedding_model", DEFAULT_EMBEDDING_MODEL) or DEFAULT_EMBEDDING_MODEL,
            target,
            _whole(params, "min_sentences", 4),
        )

    msg = f"unknown segmenter {name!r}; it is one of {', '.join(SEGMENTER_NAMES)}"
    raise ValueError(msg)
