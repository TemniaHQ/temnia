"""Load exact local Llama and LoRA snapshots; infer once without silent truncation."""

# Heavy libraries are deliberately isolated and lazily loaded in this module.
# ruff: noqa: EM101, EM102, TRY003
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false
from __future__ import annotations

import importlib
import importlib.metadata
import os
import time
from pathlib import Path
from typing import Any, Protocol, cast

from temnia_pipeline.chapter_llama.contracts import (
    ADAPTER_PATH,
    ADAPTER_REPO,
    ADAPTER_REVISION,
    BASE_REPO,
    BASE_REVISION,
    ChapterLlamaInput,
    InferenceResult,
    parse_predictions,
)

BASE_FILES = (
    "LICENSE",
    "USE_POLICY.md",
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "model.safetensors.index.json",
    *(f"model-{index:05}-of-00004.safetensors" for index in range(1, 5)),
)
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")


class InferenceEngine(Protocol):
    """Local compute seam; never hides a remote dispatch in a Segmenter."""

    def infer(self, request: ChapterLlamaInput) -> InferenceResult:
        """Return one complete retained generation or raise a known local failure."""
        ...


class InvalidGenerationError(ValueError):
    """Retain paid output when parsing or output truncation refuses a generation."""

    def __init__(
        self, message: str, raw_output: str, input_tokens: int, output_tokens: int
    ) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def hms(milliseconds: int) -> str:
    """Upstream inference represents transcript times at whole-second resolution."""
    seconds = milliseconds // 1000
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def build_prompt(request: ChapterLlamaInput) -> str:
    """Use the upstream ASR task shape with the complete existing transcript."""
    return (
        f"Given the complete transcript of a video of duration {hms(request.duration_ms)}, "
        "segment the text into distinct chapters based on thematic shifts or changes in topics.\n"
        "Identify the approximate start time of each chapter in the format 'hh:mm:ss - Title'. "
        "Ensure each chapter entry is on a new line. "
        "Focus on significant topic changes that would merit a new chapter in a video, "
        "but do not provide summaries of the chapters.\nHere is the transcript to analyze:\n"
        + "".join(f"{hms(item.start_ms)}: {item.text}\n" for item in request.sentences)
    )


def model_root() -> Path:
    """Use the same cache priority as the existing substrate model loaders."""
    return (
        Path(
            os.environ.get("HF_HOME")
            or os.environ.get("TEMNIA_MODELS_DIR")
            or "~/.cache/temnia-models"
        )
        .expanduser()
        .resolve()
    )


def snapshot(root: Path, repo: str, revision: str, files: tuple[str, ...]) -> Path:
    """Refuse missing model bytes without asking a loader to reach the network."""
    path = root / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots" / revision
    missing = [name for name in files if not (path / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"pinned {repo}@{revision} snapshot missing {', '.join(missing)}; "
            "run scripts/fetch_chapter_llama.py during setup"
        )
    return path


class TransformersEngine:
    """Unquantized pinned ASR model, loaded only when a real audition asks for it."""

    def __init__(self, *, device: str = "cuda", root: Path | None = None) -> None:
        if device not in {"cuda", "cpu"}:
            raise ValueError("Chapter-Llama device must be cuda or cpu")
        self.device = device
        self.root = root or model_root()
        self._loaded: tuple[Any, Any, Any] | None = None
        self.load_seconds = 0.0

    def _load(self) -> tuple[Any, Any, Any]:
        if self._loaded is not None:
            return self._loaded
        base = snapshot(self.root, BASE_REPO, BASE_REVISION, BASE_FILES)
        adapter = (
            snapshot(
                self.root,
                ADAPTER_REPO,
                ADAPTER_REVISION,
                tuple(f"{ADAPTER_PATH}/{name}" for name in ADAPTER_FILES),
            )
            / ADAPTER_PATH
        )
        started = time.monotonic()
        try:
            torch = cast("Any", importlib.import_module("torch"))
            transformers = cast("Any", importlib.import_module("transformers"))
            peft = cast("Any", importlib.import_module("peft"))
        except ImportError as error:
            raise RuntimeError(
                "local Chapter-Llama inference requires the chapter-llama extra"
            ) from error
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is unavailable; use the isolated Modal app or select cpu explicitly"
            )
        model = transformers.AutoModelForCausalLM.from_pretrained(
            str(base),
            local_files_only=True,
            trust_remote_code=False,
            dtype=torch.bfloat16 if self.device == "cuda" else torch.float32,
            attn_implementation="sdpa",
        )
        model = peft.PeftModel.from_pretrained(model, str(adapter), local_files_only=True)
        model.to(self.device)
        model.eval()
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(base),
            local_files_only=True,
            trust_remote_code=False,
        )
        tokenizer.pad_token = tokenizer.eos_token
        self._loaded = (torch, model, tokenizer)
        self.load_seconds = time.monotonic() - started
        return self._loaded

    def infer(self, request: ChapterLlamaInput) -> InferenceResult:
        """Run one greedy decode, retaining token accounting and exact input identity."""
        if not request.sentences:
            return InferenceResult(
                input_sha256=request.sha256,
                config=request.config,
                raw_output="",
                predictions=(),
                input_tokens=0,
                output_tokens=0,
                elapsed_seconds=0,
            )
        torch, model, tokenizer = self._load()
        # Preserve the pinned upstream wrapper rather than a mutable chat template.
        wrapped = (
            "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
            "Cutting Knowledge Date: December 2023\nToday Date: 26 Jul 2024\n\n"
            "<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n"
            + build_prompt(request)
            + "<|eot_id|><|start_header_id|>assistant<|end_header_id|>"
        )
        batch = tokenizer(wrapped, add_special_tokens=False, truncation=False, return_tensors="pt")
        input_tokens = int(batch["input_ids"].shape[-1])
        if input_tokens > request.config.max_input_tokens:
            raise ValueError(
                f"complete input has {input_tokens} tokens, over configured "
                f"{request.config.max_input_tokens}; input was not truncated"
            )
        batch = {name: value.to(self.device) for name, value in batch.items()}
        terminators = [tokenizer.eos_token_id, tokenizer.convert_tokens_to_ids("<|eot_id|>")]
        started = time.monotonic()
        with torch.inference_mode():
            generated = model.generate(
                **batch,
                max_new_tokens=request.config.max_new_tokens,
                do_sample=False,
                use_cache=True,
                eos_token_id=terminators,
                pad_token_id=tokenizer.eos_token_id,
            )
        elapsed = time.monotonic() - started
        output_ids = generated[0][input_tokens:]
        output_tokens = len(output_ids)
        raw = str(tokenizer.decode(output_ids, skip_special_tokens=True)).strip()
        if (
            output_tokens >= request.config.max_new_tokens
            and int(output_ids[-1]) not in terminators
        ):
            raise InvalidGenerationError(
                "Chapter-Llama reached the output token limit", raw, input_tokens, output_tokens
            )
        try:
            predictions = parse_predictions(raw, request)
        except ValueError as error:
            raise InvalidGenerationError(str(error), raw, input_tokens, output_tokens) from error
        return InferenceResult(
            input_sha256=request.sha256,
            config=request.config,
            raw_output=raw,
            predictions=predictions,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_seconds=elapsed,
            model_load_seconds=self.load_seconds,
            versions={
                name: importlib.metadata.version(name) for name in ("torch", "transformers", "peft")
            },
        )
