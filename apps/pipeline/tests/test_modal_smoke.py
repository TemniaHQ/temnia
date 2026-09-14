"""Release smoke targets a deployed app/environment/build, never local function handles."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from temnia_pipeline import modal_smoke
from temnia_pipeline.modal_build import source_build_id
from temnia_pipeline.modal_protocol import CONTRACT_VERSION


@pytest.mark.parametrize(
    "changed",
    [
        "src/temnia_pipeline/code.py",
        "pyproject.toml",
        "uv.lock",
        "Dockerfile",
        "Dockerfile.speech",
        "THIRD_PARTY_NOTICES.md",
        "LICENSES/NLTK-3.10.3.txt",
    ],
)
def test_build_identity_covers_source_and_runtime_build_inputs(
    tmp_path: Path, changed: str
) -> None:
    for name in (
        "src/temnia_pipeline/code.py",
        "pyproject.toml",
        "uv.lock",
        "Dockerfile",
        "Dockerfile.speech",
        "THIRD_PARTY_NOTICES.md",
        "LICENSES/NLTK-3.10.3.txt",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("first")
    before = source_build_id(tmp_path)
    (tmp_path / changed).write_text("second")
    assert source_build_id(tmp_path) != before


@pytest.mark.parametrize("wrong_at", ["before", "render", "speech", "gpu", "after", None])
async def test_smoke_checks_deployed_identity_and_passes_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
    wrong_at: str | None,
) -> None:
    wanted = {"protocol": CONTRACT_VERSION, "build": "expected"}
    wrong = {"protocol": CONTRACT_VERSION, "build": "different"}
    looked_up: list[tuple[str, str, str]] = []
    speech_calls: list[object] = []
    identities = 0
    monkeypatch.setattr(modal_smoke, "source_build_id", lambda: "expected")

    def deployed(name: str, app: str, environment: str) -> SimpleNamespace:
        looked_up.append((name, app, environment))

        async def remote(*args: object) -> dict[str, Any]:
            nonlocal identities
            if name == "deployment_identity":
                identities += 1
                is_wrong = (wrong_at == "before" and identities == 1) or (
                    wrong_at == "after" and identities == 2
                )
                return wrong if is_wrong else wanted
            if name == "render_probe":
                return {
                    "build": "different" if wrong_at == "render" else "expected",
                    "encoder": "h264_nvenc",
                    "nvenc": True,
                }
            speech_calls.append(args)
            assert args[-1] == wanted
            return {
                "identity": wrong if wrong_at == "speech" else wanted,
                "gpuBuild": "different" if wrong_at == "gpu" else "expected",
                "words": 12,
                "missing": [],
            }

        return SimpleNamespace(remote=SimpleNamespace(aio=remote))

    monkeypatch.setattr(modal_smoke, "_deployed", deployed)
    if wrong_at:
        with pytest.raises(
            RuntimeError, match=r"(?:identity|GPU build) mismatch|render probe failed"
        ):
            await modal_smoke.run_smoke("release-app", "release-env")
    else:
        report = await modal_smoke.run_smoke("release-app", "release-env")
        assert report["app"] == "release-app"
        assert report["environment"] == "release-env"
        assert report["render"]["nvenc"] is True
    assert all(app == "release-app" and env == "release-env" for _, app, env in looked_up)
    assert bool(speech_calls) == (wrong_at not in {"before", "render"})
