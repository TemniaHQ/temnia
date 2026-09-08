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
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np

from temnia_pipeline.settings import ModelSettings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

#: The kernel change-point cost matrix is dense and quadratic in the number of
#: sentences. 2,500 sentences (a two-and-a-half-hour episode) is 50 MB and
#: seconds; 10,000 is 800 MB, and past that the answer is a hierarchy, not a
#: bigger matrix.
MAX_CHANGE_POINT_UNITS = 10_000

#: Immutable revisions, including SaT's separately loaded tokenizer. The setup
#: script duplicates this list to keep the image's model layer ahead of source.
#: Tests compare both lists. Other audition models must use repo@<full commit>.
DEFAULT_SAT_TOKENIZER = "facebookAI/xlm-roberta-base"
PINNED_REVISIONS: dict[str, str] = {
    "segment-any-text/sat-3l-sm": "137da054051ad9f1eac42025f758db4ac9f22535",
    "sentence-transformers/all-MiniLM-L6-v2": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    DEFAULT_SAT_TOKENIZER: "e73636d4f797dec63c3081bb6ed5c7b0bb3f2089",
}


@dataclass(frozen=True, slots=True)
class LoadedModel[T]:
    """A model and the immutable snapshot identity captured when it was loaded."""

    value: T
    revision: str
    tokenizer_revision: str | None = None


def configure_model_cache(models_dir: Path | None = None) -> Path:
    """Use one effective HF_HOME for setup, loading, and provenance.

    An explicitly configured shared HF_HOME takes precedence. All loaders use
    absolute snapshot paths below this root, independent of libraries caching
    their environment settings at import time.
    """
    root = Path(os.environ.get("HF_HOME") or (models_dir or ModelSettings.from_env().models_dir))
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(root)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return root


def model_snapshot(name: str, root: Path) -> Path:
    """Find a pinned local snapshot, never a mutable branch or a network download.

    Setup fetches defaults; an audition can prefetch another repo at an explicit
    full commit and select it as repo@commit. Missing snapshots fail at load.
    """
    repo, separator, revision = name.partition("@")
    if not separator:
        revision = PINNED_REVISIONS.get(repo, "")
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        msg = f"model {name!r} needs an immutable revision; use repo@<40-character commit>"
        raise ValueError(msg)
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) is None:
        msg = f"invalid model repository {repo!r}; expected owner/name"
        raise ValueError(msg)
    snapshot = root / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots" / revision
    if not snapshot.is_dir():
        msg = (
            f"model snapshot {repo}@{revision} is missing from {root}; "
            "prefetch the immutable snapshot before running the worker "
            "(scripts/fetch_models.py fetches the defaults)"
        )
        raise FileNotFoundError(msg)
    return snapshot


def prime_skops() -> None:
    """Import `skops.io` before anything imports `transformers`.

    `skops` enumerates the trusted types of every imported module when it is
    first imported. If `transformers` is already in the process it walks that
    too and touches its lazy vision submodules, one of which imports
    `torchvision` at module level, a package the pipeline deliberately does
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
) -> LoadedModel[SaTModel]:
    """Load SaT, its tokenizer, and any adapter from immutable local snapshots.

    SaT's from_pretrained_kwargs apply to weights but not its tokenizer or hub
    adapter downloads. Local paths cover all three and keep loading offline.
    """
    if (style_or_domain is None) != (language is None):
        msg = (
            "a Segment-any-Text adapter needs style_or_domain and language together; "
            f"got style_or_domain={style_or_domain!r}, language={language!r}"
        )
        raise ValueError(msg)
    root = configure_model_cache(models_dir)
    snapshot = model_snapshot(model if "/" in model else f"segment-any-text/{model}", root)
    tokenizer = model_snapshot(DEFAULT_SAT_TOKENIZER, root)
    adapter = None
    if style_or_domain is not None and language is not None:
        for component in (style_or_domain, language):
            if re.fullmatch(r"[\w-]+", component) is None:
                msg = f"invalid adapter path component {component!r}"
                raise ValueError(msg)
        adapter = snapshot / "loras" / style_or_domain / language
        if not adapter.is_dir():
            msg = f"adapter is missing from the pinned model snapshot: {adapter}"
            raise FileNotFoundError(msg)
    prime_skops()
    from wtpsplit import SaT  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

    if adapter is not None and style_or_domain is not None and language is not None:
        loaded = SaT(  # pyright: ignore[reportUnknownVariableType]
            str(snapshot),
            tokenizer_name_or_path=str(tokenizer),
            from_pretrained_kwargs={"local_files_only": True},
            style_or_domain=style_or_domain,
            language=language,
            lora_path=str(adapter),
        )
    else:
        loaded = SaT(  # pyright: ignore[reportUnknownVariableType]
            str(snapshot),
            tokenizer_name_or_path=str(tokenizer),
            from_pretrained_kwargs={"local_files_only": True},
        )
    return LoadedModel(cast("SaTModel", loaded), snapshot.name, tokenizer.name)


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


def load_encoder(name: str, *, models_dir: Path | None = None) -> LoadedModel[TextEncoder]:
    """Load one immutable local sentence-embedding snapshot onto the CPU."""
    root = configure_model_cache(models_dir)
    snapshot = model_snapshot(name, root)
    prime_skops()
    from sentence_transformers import (  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]
        SentenceTransformer,
    )

    loaded = SentenceTransformer(  # pyright: ignore[reportUnknownVariableType]
        str(snapshot), local_files_only=True, device="cpu"
    )
    return LoadedModel(cast("TextEncoder", loaded), snapshot.name)


def truncated_count(encoder: TextEncoder, texts: Sequence[str]) -> int | None:
    """How many texts the encoder will cut short, or None when it cannot say.

    all-MiniLM-L6-v2 reads 256 wordpieces and drops the rest without a word
    (its model card says so), so a long sentence is embedded by its first
    half. The count goes into the provenance, which is what makes it visible
    on an eval row (S2 review, I18). The tokenizer is reached through the
    loaded model; a test double without one answers None rather than a guess.
    """
    tokenizer = cast("Any", getattr(encoder, "tokenizer", None))
    limit = getattr(encoder, "max_seq_length", None)
    if tokenizer is None or not isinstance(limit, int) or limit <= 0:
        return None
    try:
        encoded = tokenizer(list(texts), add_special_tokens=True, truncation=False)
        lengths = [len(ids) for ids in cast("list[list[int]]", encoded["input_ids"])]
    except Exception:  # noqa: BLE001 - provenance, never a gate
        return None
    return sum(length > limit for length in lengths)


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


def positive_integer(value: object, *, name: str) -> int:
    """Reject invalid segment sizes before a model is loaded or a kernel is fitted."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        msg = f"{name} must be a positive whole number, not {value!r}"
        raise ValueError(msg)
    return value


class KernelSegmentation:
    """A fitted RBF kernel change-point model over one embedding matrix.

    `ruptures.KernelCPD` solves two different problems from one fit: the exact
    best segmentation into a given number of pieces (dynamic programming), and
    the best segmentation under a per-boundary penalty. Both are wanted here:
    the first to hit a target granularity and the second to expose the raw
    curve, so both are on this façade, along with the segment cost the candidate
    scores are computed from.
    """

    def __init__(self, matrix: NDArray[np.float64], *, min_size: int = 4) -> None:
        min_size = positive_integer(min_size, name="min_size")
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
        # `jump=1` is the only value KernelCPD honours; its reference says the
        # parameter is "not considered, set to 1", so it is not offered.
        self._algo = cast(
            "_KernelCPD",
            ruptures.KernelCPD(  # pyright: ignore[reportUnknownMemberType]
                kernel="rbf", min_size=min_size, jump=1
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
