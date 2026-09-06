"""The deployed side: `temnia-media` on Modal, with an NVENC ffmpeg on an L4.

Deploy from `apps/pipeline`:

    uv run modal run temnia_pipeline.modal_app::probe        # the throwaway check
    uv run modal deploy temnia_pipeline.modal_app --env staging

The image is a CUDA runtime with BtbN's glibc ffmpeg, because Temnia's worker
ffmpeg (`mwader/static-ffmpeg:8.1.2`) is a static musl build with no NVENC.
The tarball is pinned by sha256 against a release tag that BtbN rebuilds in
place, so the day the build behind `latest` moves, this image fails to build
loudly instead of quietly encoding with something else.

Everything this module imports at container start must come from the media,
storage, and transcode packages: no temporalio, no psycopg, and no database.
The function is scope-blind compute. It is handed a prefix the worker already
decided belongs to an organization and it never sees an organization id.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import modal

from temnia_pipeline.media import hls
from temnia_pipeline.settings import DEFAULT_MODAL_APP, DEFAULT_PROGRESS_DICT, StorageSettings
from temnia_pipeline.storage import download, make_store, upload_file, upload_tree
from temnia_pipeline.transcode import CONTRACT_VERSION, LadderJob, LadderResult
from temnia_pipeline.transcode.local import verify_ladder

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
LADDER_TIMEOUT_SECONDS = 3 * 60 * 60
PROBE_TIMEOUT_SECONDS = 600
LADDER_CPUS = 4
LADDER_MEMORY_MB = 8192
PROGRESS_INTERVAL_SECONDS = 5.0
# The Modal Secret holding a second R2 token, revocable without touching the
# worker's: the name of a secret, not a secret (runbook, Modal).
R2_SECRET = "temnia-r2"  # noqa: S105

# The same bounds as `apps/pipeline/pyproject.toml`. The function needs the
# media planner, the object store, and pydantic; PyAV stays on the worker,
# which is the only place a master is probed.
PIP_PACKAGES = ["obstore>=0.11.1,<0.12", "pydantic>=2.13,<3", "boto3>=1.40,<2"]

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
    .add_local_python_source("temnia_pipeline")
)

app = modal.App(DEFAULT_MODAL_APP, image=image)

WORK_DIR = Path("/tmp/ladder")  # noqa: S108  # Modal's ephemeral disk, gone with the container


async def _write_progress(stage: str, percent: int) -> None:
    """Leave a note the worker can poll; a failure here never fails the ladder."""
    call_id = modal.current_function_call_id()
    if call_id is None:
        return
    name = os.environ.get("MODAL_PROGRESS_DICT", DEFAULT_PROGRESS_DICT)
    try:
        notes = modal.Dict.from_name(name, create_if_missing=True)
        await notes.put.aio(call_id, {"stage": stage, "percent": percent})
    except Exception:  # noqa: BLE001, S110
        pass


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


@app.function(  # pyright: ignore[reportUnknownMemberType]
    gpu=GPU,
    cpu=LADDER_CPUS,
    memory=LADDER_MEMORY_MB,
    timeout=LADDER_TIMEOUT_SECONDS,
    secrets=[modal.Secret.from_name(R2_SECRET)],
)
async def ladder(job: dict[str, Any]) -> dict[str, Any]:
    """Build the HLS ladder on the GPU and publish it under the job's prefix.

    Decode and scale run on the four CPUs: NVENC has no ProRes decoder, and a
    hybrid graph is the whole reason this is an L4 and not an A100. Every
    playlist is verified before a byte is uploaded, and the manifest goes up
    last, so the worker's completion check cannot pass over a half published
    prefix.
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

    rungs = await hls.transcode_ladder(
        FFMPEG,
        master,
        out_dir,
        request.video,
        has_audio=request.has_audio,
        expected_seconds=request.expected_seconds,
        on_progress=on_encode,
        encoder="h264_nvenc",
    )
    renditions = verify_ladder(out_dir, rungs, request)

    publish = _throttled("publish")

    async def on_publish(done: int, total_bytes: int) -> None:
        await publish(done, total_bytes)

    await _write_progress("publish", 0)
    total = await upload_tree(store, request.hls_prefix, out_dir, on_progress=on_publish)
    manifest = hls.LadderManifest(
        renditions=renditions,
        iframes=request.video is not None,
        segment_seconds=hls.SEGMENT_SECONDS,
        total_bytes=total,
        encoder="h264_nvenc",
        produced_by="modal",
        call_id=modal.current_function_call_id(),
    )
    await upload_file(store, request.manifest_key, hls.write_manifest(out_dir, manifest))
    await _write_progress("publish", 100)
    result = LadderResult(
        renditions=renditions,
        total_bytes=total,
        manifest_key=request.manifest_key,
        encoder="h264_nvenc",
        call_id=modal.current_function_call_id(),
    )
    return result.model_dump(mode="json", by_alias=True)


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
    """`uv run modal run temnia_pipeline.modal_app::probe`."""
    print(nvenc_probe.remote())  # noqa: T201
