"""Immutable input, model and result identities for Chapter-Llama auditions."""

# ruff: noqa: EM101, TRY003
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Literal, Self
from uuid import UUID  # noqa: TC003  # Pydantic resolves runtime annotations.

from pydantic import BaseModel, ConfigDict, Field, model_validator

BASE_REPO = "meta-llama/Llama-3.1-8B-Instruct"
BASE_REVISION = "0e9e39f249a16976918f6564b8830bc894c89659"
ADAPTER_REPO = "lucas-ventura/chapter-llama"
ADAPTER_REVISION = "a3837e908f05eb3697873acba2870b2370b1e92d"
ADAPTER_PATH = (
    "outputs/chapterize/Meta-Llama-3.1-8B-Instruct/asr/default/"
    "s10k-2_train/default/model_checkpoints"
)
PROTOCOL = "temnia-chapter-llama/1"


def canonical(value: object) -> bytes:
    """Canonical bytes used by both worker and isolated compute."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: object) -> str:
    """Hash a JSON-compatible identity."""
    return hashlib.sha256(canonical(value)).hexdigest()


class WireModel(BaseModel):
    """Strict immutable internal wire object."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConfig(WireModel):
    """The supported ASR-only audition, not a mutable model alias."""

    base_repo: Literal["meta-llama/Llama-3.1-8B-Instruct"] = BASE_REPO
    base_revision: Literal["0e9e39f249a16976918f6564b8830bc894c89659"] = BASE_REVISION
    adapter_repo: Literal["lucas-ventura/chapter-llama"] = ADAPTER_REPO
    adapter_revision: Literal["a3837e908f05eb3697873acba2870b2370b1e92d"] = ADAPTER_REVISION
    adapter_path: str = ADAPTER_PATH
    prompt_version: Literal["chapter-llama-asr/1"] = "chapter-llama-asr/1"
    max_input_tokens: int = Field(default=35_000, gt=0, le=120_000)
    max_new_tokens: int = Field(default=2048, gt=0, le=8192)

    @model_validator(mode="after")
    def validate_adapter(self) -> Self:
        """Never let an input select another local path or unqualified adapter."""
        if self.adapter_path != ADAPTER_PATH:
            raise ValueError("only the pinned ASR-10k adapter is qualified for this protocol")
        return self


class ResourceProfile(WireModel):
    """One measured-audition profile and dated public-rate reservation estimate."""

    gpu: Literal["L40S"] = "L40S"
    cpu: Literal[4] = 4
    memory_mb: Literal[32768] = 32768
    timeout_seconds: Literal[3600] = 3600
    rates_date: Literal["2026-09-10"] = "2026-09-10"
    # Modal pricing, 2026-09-10: GPU542, CPU13.1/core, RAM2.22/GiB microUSD/s.
    rate_nano_usd_per_second: Literal[665440] = 665440

    def estimated_micros(self, seconds: float) -> int:
        """Estimate allocated-resource spend, never represent this as an invoice."""
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("compute duration must be finite and nonnegative")
        return math.ceil(seconds * self.rate_nano_usd_per_second / 1000)

    @property
    def reservation_micros(self) -> int:
        """One full configured execution window; restart overhead remains uncertain."""
        return self.estimated_micros(self.timeout_seconds)


class InputSentence(WireModel):
    """An addressable sentence already produced by Temnia's transcript substrate."""

    id: str = Field(min_length=1, max_length=256)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)


class ChapterLlamaInput(WireModel):
    """Complete timestamped input; no media fetch or retranscription is hidden here."""

    duration_ms: int = Field(gt=0)
    sentences: tuple[InputSentence, ...]
    config: ModelConfig = Field(default_factory=ModelConfig)

    @model_validator(mode="after")
    def validate_sentences(self) -> Self:
        """Refuse corrupt timelines before any inference admission."""
        seen: set[str] = set()
        previous = -1
        for sentence in self.sentences:
            if sentence.id in seen or sentence.start_ms < previous:
                raise ValueError("sentence IDs must be unique and starts lexically ordered")
            if not sentence.start_ms < sentence.end_ms <= self.duration_ms:
                raise ValueError("sentence time lies outside the source duration")
            if not sentence.text.strip():
                raise ValueError("sentence text must not be blank")
            seen.add(sentence.id)
            previous = sentence.start_ms
        return self

    @property
    def sha256(self) -> str:
        """Identity shared by local, remote, replay and candidate consumers."""
        return digest(self.model_dump(mode="json"))


class TopicPrediction(WireModel):
    """A model timestamp suggestion mapped by code to an existing sentence."""

    proposed_ms: int = Field(ge=0)
    title: str = Field(min_length=1)
    sentence_id: str
    sentence_start_ms: int = Field(ge=0)


class InferenceResult(WireModel):
    """Complete retained output and measured inference metadata."""

    schema_version: Literal["chapter-llama-result/1"] = "chapter-llama-result/1"
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: ModelConfig
    raw_output: str
    predictions: tuple[TopicPrediction, ...]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    model_load_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    versions: dict[str, str] = Field(default_factory=dict)


class ChapterLlamaJob(WireModel):
    """Caller-authorized source prefix and one physical inference identity."""

    protocol: Literal["temnia-chapter-llama/1"] = PROTOCOL
    organization_id: UUID
    source_id: UUID
    operation_id: UUID
    attempt_id: UUID
    expected_build: str = Field(pattern=r"^[0-9a-f]{64}$")
    input: ChapterLlamaInput

    @property
    def prefix(self) -> str:
        """Scope-blind compute can write only the supplied source's audition keys."""
        return f"org/{self.organization_id}/source/{self.source_id}/chapter-llama/"

    @property
    def checkpoint_key(self) -> str:
        """One immutable outcome per physical attempt."""
        return f"{self.prefix}checkpoints/{self.attempt_id}.json"

    @property
    def admission_key(self) -> str:
        """One immutable execution admission per physical attempt."""
        return f"{self.prefix}admissions/{self.attempt_id}.json"

    @property
    def sha256(self) -> str:
        """Full identity, including source scope, input and deployment."""
        return digest(self.model_dump(mode="json"))


class ChapterLlamaOutcome(WireModel):
    """Explicit computation envelope; transport uncertainty is a client state."""

    protocol: Literal["temnia-chapter-llama/1"] = PROTOCOL
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build: str = Field(pattern=r"^[0-9a-f]{64}$")
    modal_call_id: str = Field(min_length=1, max_length=256)
    modal_task_id: str = Field(min_length=1, max_length=256)
    compute_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    status: Literal["ok", "failed", "outcome_unknown"]
    result: InferenceResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    rejected_output: str | None = None
    rejected_input_tokens: int | None = Field(default=None, ge=0)
    rejected_output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        """A result cannot coexist with a failed computation."""
        if self.status == "ok":
            if self.result is None or self.error_code is not None or self.error_message is not None:
                raise ValueError("ok outcome requires result and no error")
        elif self.result is not None or self.error_code is None or self.error_message is None:
            raise ValueError("non-ok outcome requires an error and no result")
        return self


def parse_predictions(  # noqa: C901
    raw: str, request: ChapterLlamaInput
) -> tuple[TopicPrediction, ...]:
    """Parse every nonblank output line; do not silently clean model mistakes."""
    if not request.sentences:
        if raw.strip():
            raise ValueError("empty transcript cannot have topic predictions")
        return ()
    predictions: list[TopicPrediction] = []
    previous_time = -1
    previous_sentence = -1
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*(\d{2,}):([0-5]\d):([0-5]\d)\s*-\s*(\S.*?)\s*", line)
        if match is None:
            raise ValueError("Chapter-Llama output must contain only hh:mm:ss - Title lines")
        hours, minutes, seconds = (int(match[index]) for index in (1, 2, 3))
        proposed = (hours * 3600 + minutes * 60 + seconds) * 1000
        if proposed <= previous_time or proposed >= request.duration_ms:
            raise ValueError("chapter timestamps must be ordered, unique and inside the source")
        nearest = min(
            range(len(request.sentences)),
            key=lambda index: (abs(request.sentences[index].start_ms - proposed), index),
        )
        if not predictions:
            # Coverage begins at source zero; a model omitting the beginning is refused.
            if proposed != 0:
                raise ValueError("first chapter must begin at 00:00:00")
            nearest = 0
        if nearest <= previous_sentence:
            raise ValueError("distinct topic predictions collapse onto the same sentence")
        sentence = request.sentences[nearest]
        predictions.append(
            TopicPrediction(
                proposed_ms=proposed,
                title=match[4],
                sentence_id=sentence.id,
                sentence_start_ms=sentence.start_ms,
            )
        )
        previous_time, previous_sentence = proposed, nearest
    if not predictions:
        raise ValueError("nonempty transcript requires at least one chapter")
    return tuple(predictions)


def validate_result(result: InferenceResult, request: ChapterLlamaInput) -> None:
    """Never accept a saved response for another input, model or mapping policy."""
    if result.input_sha256 != request.sha256 or result.config != request.config:
        raise ValueError("Chapter-Llama result identity differs from requested input")
    if parse_predictions(result.raw_output, request) != result.predictions:
        raise ValueError("Chapter-Llama result mappings differ from retained raw output")
