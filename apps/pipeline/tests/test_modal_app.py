"""The deployed app's shape, checked without an account.

Nothing here calls Modal. What it protects is the two things that would fail
silently: an image that stops verifying the ffmpeg it downloads, and an import
that drags Temporal or the database into a container that has neither.
"""

from __future__ import annotations

import subprocess
import sys

from temnia_pipeline import modal_app
from temnia_pipeline.transcode import CONTRACT_VERSION
from temnia_pipeline.transcode.modal_client import LADDER_FUNCTION, VERSION_FUNCTION

BANNED = ("temporalio", "psycopg", "av")

ISOLATION_PROBE = f"""
import sys
import temnia_pipeline.modal_app
loaded = [name for name in {BANNED!r} if name in sys.modules]
print(",".join(loaded))
"""


def test_the_ffmpeg_tarball_is_pinned_and_the_build_checks_it() -> None:
    assert len(modal_app.FFMPEG_SHA256) == 64
    assert modal_app.FFMPEG_URL.endswith(f"{modal_app.FFMPEG_DIR}.tar.xz")
    steps = modal_app.FFMPEG_INSTALL
    verify = next(i for i, step in enumerate(steps) if "sha256sum -c -" in step)
    extract = next(i for i, step in enumerate(steps) if step.startswith("tar -xJf"))
    # A replaced build must fail before the archive is opened, not after.
    assert verify < extract
    assert modal_app.FFMPEG_SHA256 in steps[verify]
    assert steps[-1].startswith("rm -rf")


def test_the_worker_and_the_app_agree_on_the_function_names() -> None:
    """Modal registers a function under its Python name; the client looks it up by string."""
    assert hasattr(modal_app, LADDER_FUNCTION)
    assert hasattr(modal_app, VERSION_FUNCTION)
    assert modal_app.version.local() == CONTRACT_VERSION


def test_the_module_a_container_imports_pulls_in_no_worker_dependencies() -> None:
    """A subprocess, because the rest of this suite has already imported them."""
    finished = subprocess.run(  # noqa: S603
        [sys.executable, "-c", ISOLATION_PROBE], capture_output=True, text=True, check=True
    )
    assert finished.stdout.strip() == ""
