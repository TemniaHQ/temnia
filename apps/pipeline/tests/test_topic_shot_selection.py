"""Frozen detector selection enters only new topic evidence and its immutable identity."""

# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001
from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest

from harness_fixtures import EVIDENCE_REF, SCOPE, _request, _settings, _snapshot
from temnia_pipeline.contracts import HarnessEvidence, HarnessEvidenceShot
from temnia_pipeline.harness import activities as module
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.editorial_policy import TOPIC_SELECTION_POLICY_V3
from temnia_pipeline.harness.runtime_types import (
    BuildEvidenceRequest,
    StartRunRequest,
    WorkflowIdentity,
)
from temnia_pipeline.harness.shot_evidence import SourceShotEvidence
from temnia_pipeline.harness.topic_workflow import TopicRunWorkflow
from temnia_pipeline.substrate.legacy_rules import LegacyRulesSegmenter
from test_harness_compiler import _clear_coverage, _transcript, _word
from test_harness_shot_evidence import TIMELINE

if TYPE_CHECKING:
    from pathlib import Path

SHOT_ID = UUID("10000000-0000-4000-8000-000000000007")
SPEECH_ID = UUID("10000000-0000-4000-8000-000000000008")


async def _no_record(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.mark.parametrize("policy", [TOPIC_SELECTION_POLICY_V3])
async def test_evidence_uses_frozen_detector_and_preserves_historical_fingerprints(  # noqa: C901, PLR0915
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, policy: str
) -> None:
    settings, routes = _settings()
    request = _request()
    transcript = _transcript([_word("First.", 100, 900), _word("Second.", 2100, 2900)], 5000)
    body = transcript.model_dump_json().encode()
    run = _snapshot(
        StartRunRequest(
            request=request, workflow=WorkflowIdentity(workflow_id="test", workflow_run_id="run")
        ),
        routes,
    )
    run = run.model_copy(
        update={
            "editorial_policy": policy,
            "source": run.source.model_copy(update={"duration_ms": 5000}),
            "transcript": run.transcript.model_copy(
                update={"sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
            ),
        }
    )
    source = tmp_path / "master.mp4"
    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            database_url="unused",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
            transcription=SimpleNamespace(speech_vad_model_path=tmp_path / "vad.onnx"),
        ),
        store=None,
    )
    activity = HarnessActivities(cast("Any", ctx), settings, routes)
    selected: list[str] = []
    segmentation: list[dict[str, object]] = []
    publications: list[dict[str, Any]] = []

    async def head(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"size": run.source.size_bytes, "e_tag": "frozen"}

    async def source_path(*_args: object, **_kwargs: object) -> tuple[Path, str]:
        return source, "a" * 64

    async def timeline(*_args: object, **_kwargs: object) -> object:
        return TIMELINE

    class Download:
        async def bytes_async(self) -> bytes:
            return body

    async def download(*_args: object, **_kwargs: object) -> Download:
        return Download()

    async def shots(*_args: object, **kwargs: object) -> SourceShotEvidence:
        detector = str(kwargs["detector"])
        selected.append(detector)
        return SourceShotEvidence(
            shots=(HarnessEvidenceShot(timeMs=2000, score=0.5),),
            artifact_id=SHOT_ID,
            provenance={"status": "measured", "detector": detector},
            status="measured",
        )

    async def speech(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            coverage=_clear_coverage(), provenance={"status": "clear"}, artifact_id=SPEECH_ID
        )

    def segment(_words: object, **kwargs: object) -> object:
        segmentation.append(kwargs)
        return LegacyRulesSegmenter().segment(transcript.words)

    def segmenter(_name: str) -> SimpleNamespace:
        return SimpleNamespace(segment=segment)

    async def publish(*_args: object, **kwargs: object) -> SimpleNamespace:
        publications.append(kwargs)
        identity = cast("Any", kwargs["identity"])
        return SimpleNamespace(
            id=EVIDENCE_REF.id,
            kind="evidence",
            fingerprint=identity.fingerprint,
            sha256=hashlib.sha256(module.artifacts.canonical_json(kwargs["content"])).hexdigest(),
            size_bytes=1,
            storage_key=EVIDENCE_REF.storageKey,
        )

    async def attach(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(module.obs, "head_async", head)
    monkeypatch.setattr(activity, "_source_path", source_path)
    monkeypatch.setattr(module, "inspect_timeline", timeline)
    monkeypatch.setattr(module.obs, "get_async", download)
    monkeypatch.setattr(module, "build_source_shot_evidence", shots)
    # The download-free path is exercised elsewhere; here the stubs are the sensors.
    monkeypatch.setattr(module, "find_source_timeline", _no_record)
    monkeypatch.setattr(module, "find_source_shot_evidence", _no_record)
    monkeypatch.setattr(module, "find_source_speech_coverage", _no_record)
    monkeypatch.setattr(module, "build_source_speech_coverage", speech)
    monkeypatch.setattr(module, "make_segmenter", segmenter)
    monkeypatch.setattr(module.artifacts, "publish_json", publish)
    monkeypatch.setattr(module.runs, "attach_evidence", attach)
    evidence_request = BuildEvidenceRequest(run=TopicRunWorkflow.ref(request))
    for frozen, current in (
        ("pyscenedetect-adaptive", "scdet"),
        ("scdet", "pyscenedetect-adaptive"),
    ):
        activity.harness_settings = replace(settings, topic_shot_detector=cast("Any", current))
        await activity._build_chapter_evidence_locked(
            evidence_request, run.model_copy(update={"topic_shot_detector": frozen}), SCOPE
        )
    first, second = [HarnessEvidence.model_validate(item["content"]) for item in publications]
    fingerprints = [item["identity"].fingerprint for item in publications]
    if policy == TOPIC_SELECTION_POLICY_V3:
        assert selected == ["pyscenedetect-adaptive", "scdet"]
        assert segmentation == [{"shot_times_ms": (2000,)}, {"shot_times_ms": (2000,)}]
        assert first.shots == [HarnessEvidenceShot(timeMs=2000, score=0.5)]
        assert first.config["shotEvidence"]["detector"] == "pyscenedetect-adaptive"
        assert second.config["shotEvidence"]["detector"] == "scdet"
        assert fingerprints[0] != fingerprints[1]
        assert all(SHOT_ID in item["dependency_ids"] for item in publications)
    else:
        assert selected == []
        assert segmentation == [{}, {}]
        assert first == second
        assert first.shots == []
        assert "shotEvidence" not in first.config
        assert fingerprints[0] == fingerprints[1]
        assert all(SHOT_ID not in item["dependency_ids"] for item in publications)
