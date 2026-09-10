"""Chapter-Llama topic candidates on the same sentence grid as every other row."""

from __future__ import annotations

from typing import TYPE_CHECKING

from temnia_pipeline.chapter_llama.contracts import (
    ChapterLlamaInput,
    InferenceResult,
    InputSentence,
    ModelConfig,
    validate_result,
)
from temnia_pipeline.chapter_llama.inference import TransformersEngine
from temnia_pipeline.substrate.model import BoundaryCandidate, Layers, Provenance

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from temnia_pipeline.chapter_llama.inference import InferenceEngine
    from temnia_pipeline.substrate.grid import GridWord
    from temnia_pipeline.substrate.protocol import Segmenter


def input_from_layers(layers: Layers, *, duration_ms: int | None = None) -> ChapterLlamaInput:
    """Freeze the exact sentence grid so an audition cannot relabel another source."""
    return ChapterLlamaInput(
        duration_ms=duration_ms
        if duration_ms is not None
        else max((word.endMs for word in layers.words), default=1),
        sentences=tuple(
            InputSentence(
                id=str(item.id), start_ms=item.start_ms, end_ms=item.end_ms, text=item.text
            )
            for item in layers.sentences
        ),
    )


class ChapterLlamaSegmenter:
    """An explicit local or recorded candidate; never dispatches remote work."""

    name = "chapter-llama"

    def __init__(  # noqa: PLR0913
        self,
        base: Segmenter,
        *,
        engine: InferenceEngine | None = None,
        result_path: Path | None = None,
        device: str = "cuda",
        config: ModelConfig | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.base = base
        self.engine = engine or TransformersEngine(device=device)
        self.result_path = result_path
        self.config = config or ModelConfig()
        self.duration_ms = duration_ms

    def segment(self, words: Sequence[GridWord], *, shot_times_ms: Sequence[int] = ()) -> Layers:
        """Replace baseline topic hints while retaining its exact sentence inventory."""
        base = self.base.segment(words, shot_times_ms=shot_times_ms)
        request = input_from_layers(base, duration_ms=self.duration_ms).model_copy(
            update={"config": self.config}
        )
        if self.result_path is None:
            result = self.engine.infer(request)
        else:
            result = InferenceResult.model_validate_json(self.result_path.read_bytes())
        validate_result(result, request)
        by_id = {str(sentence.id): sentence for sentence in base.sentences}
        candidates = tuple(
            BoundaryCandidate(
                sentence_id=by_id[item.sentence_id].id,
                ms=item.sentence_start_ms,
                score=1.0,
                kind="topic",
            )
            for item in result.predictions[1:]
        )
        return Layers(
            words=base.words,
            sentences=base.sentences,
            paragraphs=base.paragraphs,
            candidates=candidates,
            provenance=Provenance(
                segmenter=self.name,
                models={
                    **base.provenance.models,
                    "base": f"{result.config.base_repo}@{result.config.base_revision}",
                    "adapter": f"{result.config.adapter_repo}@{result.config.adapter_revision}",
                },
                params={
                    "sentences_from": self.base.name,
                    "mode": "recorded" if self.result_path else "local",
                    "score_semantics": "unranked candidate, not calibrated confidence",
                    "inference": result.model_dump(mode="json"),
                },
                versions={**base.provenance.versions, **result.versions},
            ),
        )
