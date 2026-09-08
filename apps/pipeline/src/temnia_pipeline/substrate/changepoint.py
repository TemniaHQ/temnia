"""Topic shifts as change points in a sequence of sentence embeddings.

Layer 3, the chapter candidates. The legacy left this to the LLM: the Director
read a rendering and guessed where the episode turned, and M1 measured 18%
acceptance on the boundaries it guessed. This proposes them instead, with a
score and a rank, so S4 reads evidence rather than the raw text.

The method. Every sentence becomes a normalised embedding; the sequence of
embeddings is a signal; a topic shift is a change in the distribution of that
signal. `ruptures` finds the segmentation of the signal that minimises a
kernel cost, which is the Embed-KCPD line of work, and needs no labels. The
kernel is RBF over unit vectors, so the cost is a function of cosine distance,
which is the similarity a sentence embedding is trained for.

Granularity is a selection problem, not a detection one: the same signal
supports three boundaries an hour or thirty, and which is right depends on what
a chapter is for. `target_per_hour` is that choice, and the harness moves it.

**A departure from the design, and the reason.** The design said to pick a
penalty by bisection until the number of breakpoints lands near the target.
`ruptures.KernelCPD` solves the exact problem directly: `predict(n_bkps=k)`
runs the dynamic program and returns the best segmentation into k+1 pieces, so
the count is exact rather than approached, it is deterministic, and it costs
one solve rather than a dozen. Bisection would be a slower way to get a worse
answer. The penalty formulation is still exposed (`candidates_at(penalty)` on
the analysis object is the raw curve the design wanted), but it is not how the
target is hit.

**Complexity.** Kernel change-point detection builds a dense Gram matrix and is
quadratic in the number of sentences. 2,500 sentences (a two-and-a-half-hour
episode) is a 50 MB matrix and seconds of CPU. The ceiling is 10,000, above
which `backends.KernelSegmentation` refuses with a message that says to
segment in parts: past that size the answer is a hierarchy, not a bigger
matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from temnia_pipeline.substrate.backends import (
    MAX_CHANGE_POINT_UNITS,
    KernelSegmentation,
    encode_sentences,
    library_version,
    load_encoder,
    positive_integer,
    truncated_count,
)
from temnia_pipeline.substrate.model import BoundaryCandidate, Layers, Provenance

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.substrate.grid import GridWord
    from temnia_pipeline.substrate.model import Sentence
    from temnia_pipeline.substrate.protocol import Segmenter

#: Small, fast on a CPU, and the most-used sentence embedding model there is.
#: A bigger one is a name, not a code change.
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

#: Chapters an hour. Six is a 10-minute chapter, which is the granularity the
#: coverage lane cuts at; the harness moves it and the eval runner reports what
#: each value produced.
DEFAULT_TARGET_PER_HOUR = 6.0

MS_PER_HOUR = 3_600_000


@dataclass(frozen=True, slots=True)
class ChangePointAnalysis:
    """One fitted model over one episode's sentences.

    Fitting embeds every sentence and builds the kernel matrix, so a caller
    that wants several granularities asks this object for them rather than
    re-running the segmenter. `candidates_at` is the design's raw penalty
    curve; `candidates` is the target count.
    """

    base: Layers
    target: int
    fitted: KernelSegmentation | None

    def candidates(self) -> tuple[BoundaryCandidate, ...]:
        """The target number of boundaries, exactly, when the signal has room."""
        if self.fitted is None:
            return ()
        return self._score(self.fitted.at_count(self.target))

    def candidates_at(self, penalty: float) -> tuple[BoundaryCandidate, ...]:
        """Whatever a per-boundary penalty produces: the raw curve."""
        if self.fitted is None:
            return ()
        return self._score(self.fitted.at_penalty(penalty))

    def layers(self, candidates: Sequence[BoundaryCandidate] | None = None) -> Layers:
        """The base segmenter's sentences and paragraphs, with these candidates.

        The base's own `turn` and `pause` candidates are dropped rather than
        merged. A chapter hypothesis of six boundaries an hour mixed with a
        thousand turn boundaries would be scored as a thousand-boundary
        hypothesis, and the density metrics exist precisely because that
        distinction is the one that gets lost.
        """
        chosen = tuple(candidates) if candidates is not None else self.candidates()
        return Layers(
            words=self.base.words,
            sentences=self.base.sentences,
            paragraphs=self.base.paragraphs,
            candidates=chosen,
            provenance=self.base.provenance,
        )

    def _score(self, breakpoints: Sequence[int]) -> tuple[BoundaryCandidate, ...]:
        """Rank the breakpoints by how much each one explains.

        A breakpoint's score is the share of its own neighbourhood's cost that
        splitting there removes:

            score(b) = 1 - (cost(left, b) + cost(b, right)) / cost(left, right)

        where `left` and `right` are the neighbouring breakpoints, or the ends
        of the episode. It is in 0..1 by construction (a split never raises the
        cost), it is local, so a strong boundary in a quiet stretch is not
        buried by a busy one elsewhere, and it is comparable between runs of
        the same model on the same episode. It is not comparable with another
        segmenter's scores; `BoundaryCandidate` says so.
        """
        if self.fitted is None or not breakpoints:
            return ()
        bounds = (0, *breakpoints, len(self.base.sentences))
        candidates: list[BoundaryCandidate] = []
        for index, point in enumerate(breakpoints):
            left, right = bounds[index], bounds[index + 2]
            whole = self.fitted.cost(left, right)
            split = self.fitted.cost(left, point) + self.fitted.cost(point, right)
            share = 0.0 if whole <= 0 else max(0.0, min(1.0, 1.0 - split / whole))
            sentence = self.base.sentences[point]
            candidates.append(
                BoundaryCandidate(
                    sentence_id=sentence.id, ms=sentence.start_ms, score=share, kind="topic"
                )
            )
        return tuple(candidates)


def target_count(sentences: Sequence[Sentence], per_hour: float) -> int:
    """How many boundaries `per_hour` asks for over this episode's own span.

    Rounded, and never negative. A forty-second clip asks for none, which is
    the honest answer: six an hour does not mean at least one.
    """
    if not sentences:
        return 0
    span_ms = sentences[-1].end_ms - sentences[0].start_ms
    return max(round(per_hour * span_ms / MS_PER_HOUR), 0)


class EmbeddingChangePointSegmenter:
    """Ranked topic candidates over another segmenter's sentences."""

    name = "changepoint"

    def __init__(
        self,
        sentences_from: Segmenter,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        target_per_hour: float = DEFAULT_TARGET_PER_HOUR,
        min_sentences: int = 4,
    ) -> None:
        min_sentences = positive_integer(min_sentences, name="min_sentences")
        self.sentences_from = sentences_from
        self.embedding_model = embedding_model
        self.target_per_hour = target_per_hour
        self.min_sentences = min_sentences
        self._encoder = None
        self._embedding_revision: str | None = None

    def _provenance(
        self, base: Provenance, target: int, truncated: int | None = None
    ) -> Provenance:
        params: dict[str, object] = {
            "min_sentences": self.min_sentences,
            "sentences_from": base.segmenter,
            "target": target,
            "target_per_hour": self.target_per_hour,
            **{f"{base.segmenter}.{key}": value for key, value in base.params.items()},
        }
        if truncated is not None:
            params["truncated_sentences"] = truncated
        versions = {
            **base.versions,
            "ruptures": library_version("ruptures"),
            "sentence_transformers": library_version("sentence-transformers"),
        }
        if self._embedding_revision is not None:
            versions["embedding_revision"] = self._embedding_revision
        return Provenance(
            segmenter=self.name,
            models={**base.models, "embedding": self.embedding_model},
            params=params,
            versions=versions,
        )

    @staticmethod
    def _with_provenance(base: Layers, provenance: Provenance) -> Layers:
        return Layers(
            words=base.words,
            sentences=base.sentences,
            paragraphs=base.paragraphs,
            candidates=base.candidates,
            provenance=provenance,
        )

    def analyse(
        self, words: Sequence[GridWord], *, shot_times_ms: Sequence[int] = ()
    ) -> ChangePointAnalysis:
        """Embed and fit once; ask the result for as many granularities as wanted."""
        base = self.sentences_from.segment(words, shot_times_ms=shot_times_ms)
        target = target_count(base.sentences, self.target_per_hour)
        # Refused before a single embedding is computed: the kernel matrix is
        # what has the ceiling, but the minute of CPU that precedes it was
        # spent before the refusal (S2 review, I18).
        if len(base.sentences) > MAX_CHANGE_POINT_UNITS:
            msg = (
                f"kernel change-point detection is quadratic in the number of sentences and "
                f"this transcript has {len(base.sentences)}, over the {MAX_CHANGE_POINT_UNITS} "
                "ceiling; segment the episode in parts, or use a hierarchical segmenter"
            )
            raise ValueError(msg)
        if len(base.sentences) < 2 * self.min_sentences:
            provenance = self._provenance(base.provenance, target)
            return ChangePointAnalysis(
                base=self._with_provenance(base, provenance), target=target, fitted=None
            )
        if self._encoder is None:
            loaded = load_encoder(self.embedding_model)
            self._encoder = loaded.value
            self._embedding_revision = loaded.revision
        texts = [sentence.text for sentence in base.sentences]
        truncated = truncated_count(self._encoder, texts)
        matrix = encode_sentences(self._encoder, texts)
        fitted = KernelSegmentation(matrix, min_size=self.min_sentences)
        provenance = self._provenance(base.provenance, target, truncated)
        return ChangePointAnalysis(
            base=self._with_provenance(base, provenance), target=target, fitted=fitted
        )

    def segment(self, words: Sequence[GridWord], *, shot_times_ms: Sequence[int] = ()) -> Layers:
        """The target number of ranked topic candidates over the base's sentences."""
        return self.analyse(words, shot_times_ms=shot_times_ms).layers()
