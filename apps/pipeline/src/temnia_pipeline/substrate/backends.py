"""The untyped model libraries, each behind a small typed surface.

`wtpsplit`, `sentence-transformers` and `ruptures` ship no type information,
and the pipeline is on pyright strict. Rather than scatter ignores through the
segmenters, every call into those three libraries happens in this module, is
imported inside the function that needs it, and comes back as one of the
protocols declared here. A segmenter therefore reads as ordinary typed Python,
and the reasoned suppressions are all in one file where they can be reviewed.

The imports are deferred for a second reason: `wtpsplit` and
`sentence-transformers` pull in transformers and torch, seconds of import time
and hundreds of megabytes of memory, and a worker that runs the legacy rules
should pay neither.

`configure_model_cache` is called before any of those imports, because
`huggingface_hub` reads `HF_HOME` once at import and every downstream library
takes its cache location from there.
"""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np

from temnia_pipeline.settings import ModelSettings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from numpy.typing import NDArray

#: The kernel change-point cost matrix is dense and quadratic in the number of
#: sentences. 2,500 sentences (a two-and-a-half-hour episode) is 50 MB and
#: seconds; 10,000 is 800 MB, and past that the answer is a hierarchy, not a
#: bigger matrix.
MAX_CHANGE_POINT_UNITS = 10_000


def configure_model_cache(models_dir: Path | None = None) -> Path:
    """Point `HF_HOME` at the model cache and return it.

    Idempotent, and it never overrides an `HF_HOME` the caller already set: the
    pipeline image bakes the weights in and sets it, and a developer may point
    at a shared cache. `TOKENIZERS_PARALLELISM` is set because the tokenizers
    library warns on every process fork otherwise, which under pytest is pages
    of noise about a decision this code does not make.
    """
    import os  # noqa: PLC0415

    root = models_dir or ModelSettings.from_env().models_dir
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(root))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return root


def prime_skops() -> None:
    """Import `skops.io` before anything imports `transformers`.

    `skops` enumerates the trusted types of every imported module when it is
    first imported. If `transformers` is already in the process it walks that
    too and touches its lazy vision submodules, one of which imports
    `torchvision` at module level — a package the pipeline deliberately does
    not install to run two text models. wtpsplit guards against this by
    importing skops before transformers in its own `__init__`, which only
    works when wtpsplit gets there first: loading `sentence-transformers` and
    then wtpsplit in one process raises `ModuleNotFoundError: torchvision`
    (verified 2026-09-07 on wtpsplit 2.2.1, transformers 5.16.1, skops 0.14).
    The eval runner loads both, in whichever order the rows are given, so
    every loader here primes skops first and the order stops mattering.
    """
    import skops.io  # noqa: F401, PLC0415  # pyright: ignore[reportMissingTypeStubs, reportUnusedImport]


def library_version(name: str) -> str:
    """The installed version of a distribution, for provenance.

    `unknown` rather than an exception: a missing version must not fail a run
    whose model loaded fine.
    """
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - install shape
        return "unknown"


class SaTModel(Protocol):
    """`wtpsplit.SaT`, as much of it as the sentence layer uses."""

    def split(
        self,
        text_or_texts: str,
        threshold: float | None = ...,
        *,
        do_paragraph_segmentation: bool = ...,
        strip_whitespace: bool = ...,
    ) -> object:
        """Segments that concatenate back to the input."""
        ...


def load_sat(
    model: str,
    *,
    style_or_domain: str | None = None,
    language: str | None = None,
    models_dir: Path | None = None,
) -> SaTModel:
    """Load a Segment-any-Text model, with its LoRA adapter when one is asked for.

    `style_or_domain` and `language` select an adapter (`ted` plus a language
    code is the transcribed-speech one); wtpsplit wants both or neither, which
    is checked here rather than inside the model's own error.
    """
    configure_model_cache(models_dir)
    prime_skops()
    if (style_or_domain is None) != (language is None):
        msg = (
            "a Segment-any-Text adapter needs style_or_domain and language together; "
            f"got style_or_domain={style_or_domain!r}, language={language!r}"
        )
        raise ValueError(msg)
    from wtpsplit import SaT  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

    # Passed as a pair or not at all: wtpsplit annotates both as `str` with a
    # None default, so handing it two Nones is a type error for no gain.
    loaded = (  # pyright: ignore[reportUnknownVariableType]
        SaT(model, style_or_domain=style_or_domain, language=language)
        if style_or_domain is not None and language is not None
        else SaT(model)
    )
    return cast("SaTModel", loaded)


def sat_segments(segments: object) -> list[str]:
    """One `SaT.split` answer as a flat list of strings.

    `split` returns a list of strings, or a list of lists of strings in
    paragraph mode. Both arrive here as `object` because the library is
    untyped, and this is the one place that decides what came back.
    """
    if not isinstance(segments, list):
        msg = f"wtpsplit returned {type(segments).__name__}, not a list"
        raise TypeError(msg)
    flat: list[str] = []
    for item in cast("list[object]", segments):
        if isinstance(item, str):
            flat.append(item)
        elif isinstance(item, list):
            flat.extend(sat_segments(cast("object", item)))
        else:
            msg = f"wtpsplit returned a {type(item).__name__} among its segments"
            raise TypeError(msg)
    return flat


def sat_paragraph_lengths(segments: object) -> list[int]:
    """How many sentences each paragraph holds, from a paragraph-mode answer."""
    if not isinstance(segments, list):
        msg = f"wtpsplit returned {type(segments).__name__}, not a list"
        raise TypeError(msg)
    return [len(sat_segments(paragraph)) for paragraph in cast("list[object]", segments)]


class TextEncoder(Protocol):
    """`sentence_transformers.SentenceTransformer`, as much as layer 3 uses."""

    def encode(
        self, sentences: list[str], *, normalize_embeddings: bool = ..., batch_size: int = ...
    ) -> object:
        """One row per sentence."""
        ...


def load_encoder(name: str, *, models_dir: Path | None = None) -> TextEncoder:
    """Load a sentence-embedding model onto the CPU."""
    configure_model_cache(models_dir)
    prime_skops()
    from sentence_transformers import (  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]
        SentenceTransformer,
    )

    loaded = SentenceTransformer(name)  # pyright: ignore[reportUnknownVariableType]
    return cast("TextEncoder", loaded)


def encode_sentences(encoder: TextEncoder, texts: Sequence[str]) -> NDArray[np.float64]:
    """Normalised embeddings as a float64 matrix, one row per text.

    float64 because ruptures computes a Gram matrix in double precision and
    would otherwise copy the float32 the encoder returns; normalised because an
    RBF kernel over unit vectors is a function of the cosine distance, which is
    the similarity a sentence embedding is trained for.
    """
    encoded = encoder.encode(list(texts), normalize_embeddings=True)
    matrix = np.asarray(encoded, dtype=np.float64)
    try:
        rows, _ = matrix.shape
    except ValueError as error:
        msg = f"the encoder returned a {matrix.ndim}-dimensional array, not a matrix"
        raise ValueError(msg) from error
    if rows != len(texts):
        msg = f"the encoder returned {rows} rows for {len(texts)} sentences"
        raise ValueError(msg)
    return matrix


class _KernelCost(Protocol):
    """`ruptures.costs.CostRbf`, as much of it as the scores use."""

    def error(self, start: int, end: int) -> float:
        """The cost of treating one run of units as a single segment."""
        ...


class _KernelCPD(Protocol):
    """`ruptures.KernelCPD` after `fit`."""

    cost: _KernelCost

    def predict(self, n_bkps: int | None = ..., pen: float | None = ...) -> object:
        """Breakpoint indices, ending with the length of the signal."""
        ...


class KernelSegmentation:
    """A fitted RBF kernel change-point model over one embedding matrix.

    `ruptures.KernelCPD` solves two different problems from one fit: the exact
    best segmentation into a given number of pieces (dynamic programming), and
    the best segmentation under a per-boundary penalty. Both are wanted here —
    the first to hit a target granularity, the second to expose the raw curve —
    so both are on this façade, along with the segment cost the candidate
    scores are computed from.
    """

    def __init__(self, matrix: NDArray[np.float64], *, min_size: int = 4, jump: int = 1) -> None:
        if matrix.shape[0] > MAX_CHANGE_POINT_UNITS:
            msg = (
                f"kernel change-point detection is quadratic in the number of units and this "
                f"run has {matrix.shape[0]}, over the {MAX_CHANGE_POINT_UNITS} ceiling; "
                f"segment the episode in parts, or use a hierarchical segmenter"
            )
            raise ValueError(msg)
        import ruptures  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

        self.units = int(matrix.shape[0])
        self.min_size = min_size
        self._algo = cast(
            "_KernelCPD",
            ruptures.KernelCPD(  # pyright: ignore[reportUnknownMemberType]
                kernel="rbf", min_size=min_size, jump=jump
            ).fit(matrix),  # pyright: ignore[reportUnknownMemberType]
        )

    @property
    def max_count(self) -> int:
        """The most breakpoints `min_size` leaves room for."""
        return max(self.units // self.min_size - 1, 0)

    def _predict(
        self, *, count: int | None = None, penalty: float | None = None
    ) -> tuple[int, ...]:
        predicted = self._algo.predict(n_bkps=count, pen=penalty)
        if not isinstance(predicted, list):  # pragma: no cover - library contract
            msg = f"ruptures returned {type(predicted).__name__}, not a list"
            raise TypeError(msg)
        # The last element is the length of the signal, not a breakpoint, and
        # the rest arrive as numpy integers on the penalty path.
        return tuple(int(index) for index in cast("list[int]", predicted)[:-1])

    def at_count(self, count: int) -> tuple[int, ...]:
        """The exact best segmentation into `count + 1` pieces.

        Empty when the signal has no room for one, which is the honest answer
        for a five-sentence transcript rather than an exception.
        """
        wanted = min(max(count, 0), self.max_count)
        if wanted == 0:
            return ()
        return self._predict(count=wanted)

    def at_penalty(self, penalty: float) -> tuple[int, ...]:
        """The best segmentation under a per-boundary penalty: the raw curve."""
        return self._predict(penalty=penalty)

    def cost(self, start: int, end: int) -> float:
        """The RBF cost of treating units `[start, end)` as one segment."""
        return float(self._algo.cost.error(start, end))
