"""The deployed side: `temnia-media` on Modal, on L4s.

Two functions, deployed together and versioned together: `ladder` builds the
HLS ladder with an NVENC ffmpeg, and `transcribe` runs WhisperX. They share an
app and a contract version, so the worker's boot probe refuses a half deployed
pair whichever backend it is configured for.

Deploy from `apps/pipeline`:

    uv run modal run --env staging -m temnia_pipeline.modal_app::probe        # the throwaway check
    uv run modal deploy --env staging -m temnia_pipeline.modal_app

The image is a CUDA runtime with BtbN's glibc ffmpeg, because Temnia's worker
ffmpeg (`mwader/static-ffmpeg:8.1.2`) is a static musl build carrying neither
NVENC nor the CUDA decoder the ladder now uses. The tarball is pinned by sha256
against a release tag that BtbN rebuilds in place, so the day the build behind
`latest` moves, this image fails to build loudly instead of quietly encoding
with something else.

Everything this module imports at container start must come from the media,
storage, and transcode packages: no temporalio, no psycopg, and no database.
The function is scope-blind compute. It is handed a prefix the worker already
decided belongs to an organization and it never sees an organization id.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import modal

from temnia_pipeline.media import hls
from temnia_pipeline.media.ffmpeg import FfmpegError
from temnia_pipeline.settings import (
    DEFAULT_MODAL_APP,
    DEFAULT_PROGRESS_DICT,
    DEFAULT_TRANSCRIPT_DICT,
    StorageSettings,
)
from temnia_pipeline.storage import download, make_store, upload_file, upload_tree
from temnia_pipeline.transcode import CONTRACT_VERSION, LadderJob, LadderResult
from temnia_pipeline.transcode.local import verify_ladder
from temnia_pipeline.transcription import TranscribeJob, TranscribeRaw, assert_alignable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# BtbN's `latest` tag is rebuilt in place, so the URL alone pins nothing. The
# checksum is of the tarball downloaded on 2026-09-07; `sha256sum -c` in the
# image build is what turns a silent replacement into a failed deploy. To move
# to a newer build, download it, recompute, and change both lines together.
FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"
)
FFMPEG_SHA256 = "853b3dd1b7a080b6564acbf4a4d3cbb00612703bfeff45d59b63b4f5a190fb79"
FFMPEG_DIR = "ffmpeg-n8.1-latest-linux64-gpl-8.1"
FFMPEG = "/usr/local/bin/ffmpeg"

GPU = "L4"
# The video rungs, on every graph: the intra-only rendition stays on libx264
# whatever decodes, because one frame every two seconds is not worth a GPU.
ENCODER: hls.Encoder = "h264_nvenc"
LADDER_TIMEOUT_SECONDS = 3 * 60 * 60
# WhisperX on an L4 is reported at 20 to 70x realtime for ASR and alignment and
# nearer 10x for diarization, so a 2.5-hour episode is plausibly 15 to 40
# minutes. Four hours is the bound, not the expectation; the first staging run
# is the measurement.
TRANSCRIBE_TIMEOUT_SECONDS = 4 * 60 * 60
PROBE_TIMEOUT_SECONDS = 600
LADDER_CPUS = 16
LADDER_MEMORY_MB = 16384
TRANSCRIBE_CPUS = 4
TRANSCRIBE_MEMORY_MB = 16384
PROGRESS_INTERVAL_SECONDS = 5.0
# The first staging ladder published 3.8 GB at 2 to 3 MB/s with eight puts in
# flight, the VPS's own rate: the publish is per-object latency across tens of
# thousands of segments. A container has the network for far more in flight.
UPLOAD_CONCURRENCY = 64
# The Modal Secret holding a second R2 token, revocable without touching the
# worker's: the name of a secret, not a secret (runbook, Modal).
R2_SECRET = "temnia-r2"  # noqa: S105
# HF_TOKEN for the gated pyannote diarization model. The token exists only
# inside Modal; the worker never holds it.
HF_SECRET = "temnia-hf"  # noqa: S105

WHISPERX_VERSION = "3.8.6"
WHISPER_MODEL = "large-v3"
# float16 is what an L4 has tensor cores for; batch 16 is whisperx's own
# documented default for a 24 GB card and is a calibration knob at S12, not a
# tuning guess to make now.
WHISPER_COMPUTE_TYPE = "float16"
WHISPER_BATCH_SIZE = 16
# whisperx 3.8.6 requires pyannote-audio 4.x, whose default pipeline is this
# gated, CC-BY-4.0 model (commercial use allowed with attribution).
DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"

# Models are downloaded once into a Volume and read from it on every later cold
# start: large-v3 alone is about 3 GB, and paying for that download on every
# call would be most of a short episode's cost.
MODEL_VOLUME = "temnia-models"
MODEL_DIR = "/models"

# The same bounds as `apps/pipeline/pyproject.toml`. The function needs the
# media planner, the object store, and pydantic; PyAV stays on the worker,
# which is the only place a master is probed.
PIP_PACKAGES = ["obstore>=0.11.1,<0.12", "pydantic>=2.13,<3", "boto3>=1.40,<2"]

# whisperx pulls torch, ctranslate2, and pyannote-audio behind it. torch comes
# from PyTorch's own cu126 index, the oldest that carries torch 2.8 (the cu124
# index stops at 2.6). The wheels bundle their own CUDA libraries, so the
# image's 12.4 runtime only has to supply the driver, and pinning the index
# keeps a second copy of those libraries from arriving through PyPI.
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_PACKAGES = ["torch==2.8.0", "torchaudio==2.8.0"]
WHISPERX_PACKAGES = [f"whisperx=={WHISPERX_VERSION}"]

# Download, verify, install, and leave nothing behind. The checksum is checked
# before the archive is opened, so a replaced build never reaches a container.
FFMPEG_INSTALL = (
    f"curl -fsSL -o /tmp/ffmpeg.tar.xz {FFMPEG_URL}",
    f"echo '{FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz' | sha256sum -c -",
    f"tar -xJf /tmp/ffmpeg.tar.xz -C /tmp {FFMPEG_DIR}/bin/ffmpeg {FFMPEG_DIR}/bin/ffprobe",
    f"install -m 0755 /tmp/{FFMPEG_DIR}/bin/ffmpeg /tmp/{FFMPEG_DIR}/bin/ffprobe /usr/local/bin/",
    f"rm -rf /tmp/ffmpeg.tar.xz /tmp/{FFMPEG_DIR}",
)

# The SDK's `from_registry` and `App.function` are typed with `Unknown`
# parameters, which pyright strict reports; the ignores are about the stubs,
# not about these arguments.
image = (
    modal.Image.from_registry(  # pyright: ignore[reportUnknownMemberType]
        "nvidia/cuda:12.4.1-runtime-ubuntu22.04", add_python="3.13"
    )
    .apt_install("xz-utils", "curl", "ca-certificates")
    .run_commands(*FFMPEG_INSTALL)
    .pip_install(*PIP_PACKAGES)
    .pip_install(*TORCH_PACKAGES, index_url=TORCH_INDEX)
    .pip_install(*WHISPERX_PACKAGES)
    # Every model cache the three stages use, on one Volume: HF_HOME covers the
    # alignment and diarization models, and whisperx reads the Whisper weights
    # from the same tree.
    .env({"HF_HOME": MODEL_DIR, "TORCH_HOME": MODEL_DIR})
    .add_local_python_source("temnia_pipeline")
)

app = modal.App(DEFAULT_MODAL_APP, image=image)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

WORK_DIR = Path("/tmp/ladder")  # noqa: S108  # Modal's ephemeral disk, gone with the container


async def _write_note(dict_name: str, stage: str, percent: int) -> None:
    """Leave a note the worker can poll; a failure here never fails the run."""
    call_id = modal.current_function_call_id()
    if call_id is None:
        return
    try:
        notes = modal.Dict.from_name(dict_name, create_if_missing=True)
        await notes.put.aio(call_id, {"stage": stage, "percent": percent})
    except Exception:  # noqa: BLE001, S110
        pass


async def _write_progress(stage: str, percent: int) -> None:
    """The ladder's progress note."""
    await _write_note(os.environ.get("MODAL_PROGRESS_DICT", DEFAULT_PROGRESS_DICT), stage, percent)


async def _write_transcript_progress(stage: str, percent: int) -> None:
    """The transcription's progress note, in its own Dict.

    A second Dict rather than one shared with the ladder: the two functions
    write different shapes, and a reader that guessed from a call id would be a
    bug that only appears when both run at once.
    """
    await _write_note(
        os.environ.get("MODAL_TRANSCRIPT_PROGRESS_DICT", DEFAULT_TRANSCRIPT_DICT), stage, percent
    )


def _throttled(stage: str) -> Any:  # noqa: ANN401
    """A progress callback that writes at most one note every five seconds."""
    last = 0.0

    async def report(done: float, total: float) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last < PROGRESS_INTERVAL_SECONDS:
            return
        last = now
        percent = 100 if total <= 0 else max(0, min(100, int(100 * done / total)))
        await _write_progress(stage, percent)

    return report


async def run_ladder(
    request: LadderJob,
    master: Path,
    out_dir: Path,
    on_encode: Callable[[float], Awaitable[None]],
) -> tuple[list[hls.Rung], hls.Decoder]:
    """Ladder on the GPU decoder where the source allows it, on the CPU where not.

    `cuda_decodable` reads the codec and the pixel format the worker probed and
    keeps everything NVDEC does not read off the GPU path before any time is
    spent. What that check cannot see is a profile or a level this particular
    card refuses, or a driver that will not initialise the decoder at all, and
    the answer to both is the same ffmpeg exit: so a CUDA run that fails is run
    again on the CPU graph, which is the ladder Temnia shipped through S1 and
    the whole of S2 so far.

    Exactly one retry, and only in that direction. A CPU run that fails fails,
    and so does the second attempt, as `TranscodeFailure` through the worker's
    classifier. The cost of the fallback is the wasted GPU minutes of the first
    attempt, which is why `cuda_decodable` is narrow rather than hopeful.

    The retry restarts the encode, so the progress note the worker is reading
    goes back down. That is honest: the work really did start again, and the
    activity's heartbeat cares only that a note keeps arriving.
    """
    decoder: hls.Decoder = "cuda" if hls.cuda_decodable(request.video) else "cpu"
    try:
        rungs = await hls.transcode_ladder(
            FFMPEG,
            master,
            out_dir,
            request.video,
            has_audio=request.has_audio,
            expected_seconds=request.expected_seconds,
            on_progress=on_encode,
            encoder=ENCODER,
            decoder=decoder,
        )
    except FfmpegError as error:
        if decoder == "cpu":
            raise
        print(f"the CUDA ladder failed, encoding again on the CPU graph: {error}")  # noqa: T201
        await _write_progress("hls", 0)
        rungs = await hls.transcode_ladder(
            FFMPEG,
            master,
            out_dir,
            request.video,
            has_audio=request.has_audio,
            expected_seconds=request.expected_seconds,
            on_progress=on_encode,
            encoder=ENCODER,
            decoder="cpu",
        )
        decoder = "cpu"
    return rungs, decoder


@app.function(  # pyright: ignore[reportUnknownMemberType]
    gpu=GPU,
    cpu=LADDER_CPUS,
    memory=LADDER_MEMORY_MB,
    timeout=LADDER_TIMEOUT_SECONDS,
    secrets=[modal.Secret.from_name(R2_SECRET)],
)
async def ladder(job: dict[str, Any]) -> dict[str, Any]:
    """Build the HLS ladder on the GPU and publish it under the job's prefix.

    Decode, scale, and the video rungs all run on the card when the source is
    one NVDEC reads, and on the CPU otherwise; `run_ladder` decides and falls
    back. Every playlist is verified before a byte is uploaded, and the
    manifest goes up last, so the worker's completion check cannot pass over a
    half published prefix.
    """
    # First, before the job is even parsed: a secret that is absent or missing
    # a key must fail here, by name. Falling back to the dev Garage defaults
    # would point this container at its own localhost and report it as a
    # connection error deep inside the download, on a GPU that is already
    # billing.
    settings = StorageSettings.require_env(f"the Modal Secret {R2_SECRET!r}")
    request = LadderJob.model_validate(job)
    store = make_store(settings)
    out_dir = WORK_DIR / request.scratch_name / "hls"
    master = WORK_DIR / request.scratch_name / ("master" + Path(request.master_key).suffix)

    await _write_progress("hls", 0)
    await download(store, request.master_key, master, expected_size=request.size_bytes)

    encode = _throttled("hls")

    async def on_encode(seconds: float) -> None:
        await encode(seconds, request.expected_seconds)

    rungs, decoder = await run_ladder(request, master, out_dir, on_encode)
    renditions = verify_ladder(out_dir, rungs, request)

    publish = _throttled("publish")

    async def on_publish(done: int, total_bytes: int) -> None:
        await publish(done, total_bytes)

    await _write_progress("publish", 0)
    total = await upload_tree(
        store,
        request.hls_prefix,
        out_dir,
        on_progress=on_publish,
        concurrency=UPLOAD_CONCURRENCY,
    )
    manifest = hls.LadderManifest(
        renditions=renditions,
        iframes=request.video is not None,
        segment_seconds=hls.SEGMENT_SECONDS,
        total_bytes=total,
        encoder=ENCODER,
        produced_by="modal",
        decoder=decoder,
        call_id=modal.current_function_call_id(),
    )
    await upload_file(store, request.manifest_key, hls.write_manifest(out_dir, manifest))
    await _write_progress("publish", 100)
    result = LadderResult(
        renditions=renditions,
        total_bytes=total,
        manifest_key=request.manifest_key,
        encoder=ENCODER,
        decoder=decoder,
        call_id=modal.current_function_call_id(),
    )
    return result.model_dump(mode="json", by_alias=True)


# whisperx is installed into the Modal image and nowhere else: it is not a
# dependency of this repository's uv project, because nothing outside a
# container ever runs it. It is therefore unresolvable to the type checker here
# by construction, so it is reached through these two `Any` helpers and the
# rest of the module stays checked. Same shape as the Modal SDK helpers in
# `transcode/modal_client.py`, for the same reason.
def _whisperx() -> Any:  # noqa: ANN401
    """The engine, imported inside the container that has it."""
    return cast("Any", import_module("whisperx"))


def alignable_languages() -> set[str]:
    """Every language the deployed whisperx can align, asked of whisperx itself.

    Never a list copied into this repository: the alignment table changes
    between releases, and a stale copy would refuse a language the deployed
    version handles perfectly well, or promise one it does not.
    """
    alignment = cast("Any", import_module("whisperx.alignment"))
    torch_models = cast("dict[str, Any]", alignment.DEFAULT_ALIGN_MODELS_TORCH)
    hf_models = cast("dict[str, Any]", alignment.DEFAULT_ALIGN_MODELS_HF)
    return set(torch_models) | set(hf_models)


@app.function(  # pyright: ignore[reportUnknownMemberType]
    gpu=GPU,
    cpu=TRANSCRIBE_CPUS,
    memory=TRANSCRIBE_MEMORY_MB,
    timeout=TRANSCRIBE_TIMEOUT_SECONDS,
    secrets=[modal.Secret.from_name(R2_SECRET), modal.Secret.from_name(HF_SECRET)],
    volumes={MODEL_DIR: models},
)
async def transcribe(job: dict[str, Any]) -> dict[str, Any]:
    """Transcribe, align, and diarize one audio extract on the GPU.

    Reads the 96k extract ingest already made rather than the master: the
    master is tens of gigabytes and every stage here wants audio.

    The response is written to storage as well as returned. The object is what
    a fixture is cut from and what S12's calibration round reads back; the
    return value is what the worker normalises without a second download.
    """
    # First, before the job is parsed: a secret that is absent or missing a key
    # must fail here, by name, not deep inside the download on a GPU that is
    # already billing.
    settings = StorageSettings.require_env(f"the Modal Secret {R2_SECRET!r}")
    token = os.environ.get("HF_TOKEN")
    if not token:
        msg = (
            f"HF_TOKEN is missing or empty. The Modal Secret {HF_SECRET!r} sets it, and "
            f"{DIARIZATION_MODEL} is gated: accept its terms on Hugging Face with the "
            "account the token belongs to."
        )
        raise RuntimeError(msg)

    engine = _whisperx()
    request = TranscribeJob.model_validate(job)
    store = make_store(settings)
    started = time.monotonic()

    await _write_transcript_progress("download", 0)
    audio_file = WORK_DIR / "audio" / Path(request.audio_key).name
    await download(store, request.audio_key, audio_file, expected_size=None)

    await _write_transcript_progress("model", 0)
    model = engine.load_model(WHISPER_MODEL, device="cuda", compute_type=WHISPER_COMPUTE_TYPE)
    audio = engine.load_audio(str(audio_file))

    await _write_transcript_progress("transcribe", 0)
    result: dict[str, Any] = model.transcribe(audio, batch_size=WHISPER_BATCH_SIZE)
    language = str(result.get("language") or "und")

    # Before the alignment model is fetched, not after: an unsupported language
    # is deterministic, and the worker turns this into a terminal failure that
    # shows the code rather than three more attempts at GPU prices.
    assert_alignable(language, alignable_languages())

    await _write_transcript_progress("align", 0)
    align_model, metadata = engine.load_align_model(language_code=language, device="cuda")
    result = engine.align(
        result["segments"], align_model, metadata, audio, "cuda", return_char_alignments=False
    )

    await _write_transcript_progress("diarize", 0)
    diarize = engine.diarize.DiarizationPipeline(
        model_name=DIARIZATION_MODEL, use_auth_token=token, device="cuda"
    )
    # fill_nearest gives a word with no diarization overlap the nearest turn
    # rather than nothing; the normaliser still tolerates a null speaker,
    # because a recording with no turns at all produces them.
    result = engine.assign_word_speakers(diarize(audio), result, fill_nearest=True)
    result["language"] = language

    await _write_transcript_progress("write", 0)
    raw_file = WORK_DIR / "raw.json"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    # Python's json writes the bare NaN a whisperx alignment score can be. It is
    # kept, not cleaned: this object is the record of what the engine said, and
    # the normaliser is the only place a score is interpreted.
    raw_file.write_text(json.dumps(result))
    await upload_file(store, request.raw_key, raw_file)

    await _write_transcript_progress("write", 100)
    return TranscribeRaw(
        raw=result,
        raw_key=request.raw_key,
        language=language,
        gpu_seconds=round(time.monotonic() - started, 3),
        gpu=GPU,
    ).model_dump(mode="json", by_alias=True)


@app.function()  # pyright: ignore[reportUnknownMemberType]
def version() -> str:
    """The contract this deployment speaks; the worker refuses to boot on a mismatch."""
    return CONTRACT_VERSION


@app.function(gpu=GPU, timeout=PROBE_TIMEOUT_SECONDS)  # pyright: ignore[reportUnknownMemberType]
def nvenc_probe() -> str:
    """Throwaway: does this image have NVENC, and how fast is it on this GPU.

    Run before anything depends on the answer. Deleted once the ladder has
    run on staging.
    """
    listed = subprocess.run(  # noqa: S603
        [FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
    ).stdout
    lines = [line.strip() for line in listed.splitlines() if "nvenc" in line]
    started = time.monotonic()
    subprocess.run(  # noqa: S603
        [
            FFMPEG,
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1920x1080:rate=25:duration=10",
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    elapsed = time.monotonic() - started
    encoders = "\n".join(lines) or "no nvenc encoder is listed"
    return f"{encoders}\n10 s of 1080p25 testsrc encoded with h264_nvenc in {elapsed:.2f} s"


@app.local_entrypoint()
def probe() -> None:
    """`uv run modal run --env staging -m temnia_pipeline.modal_app::probe`."""
    print(nvenc_probe.remote())  # noqa: T201
