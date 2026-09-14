"""Exercise an already deployed Modal build, without creating an ephemeral app.

Run from the exact deploy checkout:
python -m temnia_pipeline.modal_smoke --app temnia-media --environment staging
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, cast

from temnia_pipeline.modal_build import source_build_id
from temnia_pipeline.modal_protocol import CONTRACT_VERSION

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "speech" / "smoke.m4a"


def _deployed(name: str, app_name: str, environment: str) -> Any:  # noqa: ANN401
    import modal  # noqa: PLC0415

    return modal.Function.from_name(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        app_name, name, environment_name=environment
    )


def _assert_identity(actual: object, expected: dict[str, str]) -> None:
    if actual != expected:
        msg = f"deployed Modal identity mismatch: expected {expected!r}, received {actual!r}"
        raise RuntimeError(msg)


async def run_smoke(app_name: str, environment: str) -> dict[str, Any]:
    """Resolve only deployed handles, prove the render path, then spend on speech."""
    expected = {"protocol": CONTRACT_VERSION, "build": source_build_id()}
    identity = _deployed("deployment_identity", app_name, environment)
    speech = _deployed("smoke_transcribe", app_name, environment)
    async with asyncio.timeout(30):
        _assert_identity(await identity.remote.aio(), expected)
    # The render path first: it is cheap, and a deploy whose render function cannot start
    # must fail here rather than on a user's run.
    probe = _deployed("render_probe", app_name, environment)
    async with asyncio.timeout(15 * 60):
        render = cast("dict[str, Any]", await probe.remote.aio())
    if render.get("build") != expected["build"] or not render.get("nvenc"):
        msg = f"deployed render probe failed: {render!r}"
        raise RuntimeError(msg)
    meta = json.loads(FIXTURE.with_suffix(".json").read_text())
    async with asyncio.timeout(35 * 60):
        report = cast(
            "dict[str, Any]",
            await speech.remote.aio(
                FIXTURE.read_bytes(), int(meta["durationMs"]), list(meta["expect"]), expected
            ),
        )
    _assert_identity(report.get("identity"), expected)
    if report.get("gpuBuild") != expected["build"]:
        msg = (
            f"speech GPU build mismatch: expected {expected['build']}, got {report.get('gpuBuild')}"
        )
        raise RuntimeError(msg)
    # Fresh lookup catches a deployment that changed while the sample was running.
    async with asyncio.timeout(30):
        _assert_identity(
            await _deployed("deployment_identity", app_name, environment).remote.aio(), expected
        )
    if not report.get("words") or report.get("missing"):
        msg = f"deployed speech smoke failed: {report!r}"
        raise RuntimeError(msg)
    return {"app": app_name, "environment": environment, "render": render, **report}


def main() -> None:
    """Require explicit app/environment so a release cannot silently smoke the wrong target."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--environment", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_smoke(args.app, args.environment)), indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
