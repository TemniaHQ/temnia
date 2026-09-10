"""Recorded chapter fixture grounding and multilingual prompt boundaries."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from temnia_pipeline.contracts import (
    ChapterProposal,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
)
from temnia_pipeline.harness import activities as activities_module
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.models import EditorialVerdictV1, HierarchicalSummaryV1
from temnia_pipeline.harness.prompts import (
    PromptSentence,
    PromptWindow,
    render_proposal_prompt,
)
from temnia_pipeline.harness.routes import RouteSnapshot, estimate_cost, select_route
from temnia_pipeline.harness.runtime_types import (
    PlanningWindow,
    PrepareGlobalProposalRequest,
    PreparePlanningRequest,
    RunRef,
)
from temnia_pipeline.harness.settings import HarnessSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

FIXTURES = Path(__file__).parent / "fixtures"
HARNESS = FIXTURES / "harness"
SUBSTRATE = FIXTURES / "substrate"
SOURCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000111")
RUN_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000222")
ARTIFACT_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000333")
SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)


@pytest.mark.parametrize(
    ("policy", "extra_bytes", "window_count"),
    [("chapter-editorial/1", 0, 1), ("legacy", 0, 3), ("chapter-editorial/1", 524288, 3)],
)
async def test_complete_context_admission_avoids_unnecessary_summary_calls(
    monkeypatch: pytest.MonkeyPatch, policy: str, extra_bytes: int, window_count: int
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from test_harness_editorial import _case  # noqa: PLC0415

    evidence, _, _ = _case()
    routes = RouteSnapshot.model_validate_json((HARNESS / "routes.synthetic.json").read_bytes())
    settings = replace(_harness_settings(routes), evidence_window_sentences=2)
    owner = HarnessActivities(
        cast(
            "Any",
            SimpleNamespace(
                settings=SimpleNamespace(database_url="unused"),
                store=None,
            ),
        ),
        settings,
        routes,
    )

    async def get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            brief="Create coherent topics.",
            evidence_artifact_id=ARTIFACT_ID,
            route_snapshot=routes,
            editorial_policy=policy,
            config=settings.allowed_config(),
        )

    async def read(*_args: object, **_kwargs: object) -> object:
        return evidence.model_dump(mode="json")

    monkeypatch.setattr(activities_module.runs, "get_run", get_run)
    monkeypatch.setattr(activities_module.artifacts, "read_artifact_json", read)
    plan = await owner.prepare_chapter_proposal(
        PreparePlanningRequest(
            run=RunRef(
                scope_organization_id=SCOPE.organizationId,
                scope_user_id=SCOPE.userId,
                source_id=SOURCE_ID,
                run_id=RUN_ID,
            ),
            evidence=HarnessArtifactRef(
                id=ARTIFACT_ID,
                kind=HarnessArtifactKind.evidence,
                fingerprint="a" * 64,
                sha256="b" * 64,
                sizeBytes=1,
                storageKey="fixture/evidence.json",
            ),
            extra_context_bytes=extra_bytes,
        )
    )
    assert len(plan.windows) == window_count
    assert sum(item.sentence_count for item in plan.windows) == len(evidence.sentences)
    assert plan.windows[0].first_sentence_id == evidence.sentences[0].id
    assert plan.windows[-1].last_sentence_id == evidence.sentences[-1].id


def _harness_settings(routes: RouteSnapshot) -> HarnessSettings:
    return HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_RECORDED_FIXTURE_PATH": str(HARNESS / "chapter.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(HARNESS / "routes.synthetic.json"),
        }
    )


async def test_verified_source_cache_reuses_bytes_without_second_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    activities = HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    source = SimpleNamespace(storage_key="scoped/master.mp4", size_bytes=4)
    run = SimpleNamespace(id=RUN_ID, source=source)
    directory = tmp_path / "harness" / str(RUN_ID) / "source"
    directory.mkdir(parents=True)
    master = directory / "master.mp4"
    master.write_bytes(b"data")
    sha256 = hashlib.sha256(b"data").hexdigest()
    (directory / "master.identity.json").write_text(
        json.dumps(
            {
                "etag": "frozen-etag",
                "key": source.storage_key,
                "sha256": sha256,
                "sizeBytes": 4,
                "versionId": None,
            }
        )
    )

    async def forbidden_download(*_args: object, **_kwargs: object) -> object:
        message = "verified cache should avoid a second source download"
        raise AssertionError(message)

    monkeypatch.setattr(activities_module.obs, "get_async", forbidden_download)
    path, observed_sha = await activities._source_path(  # noqa: SLF001
        cast("Any", run),
        observed={"etag": "frozen-etag", "versionId": None},
        expected_sha256=sha256,
    )
    assert path == master
    assert observed_sha == sha256


async def test_source_download_preflights_and_stops_at_runtime_free_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    activities = HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    source = SimpleNamespace(storage_key="scoped/master.mp4", size_bytes=4)
    run = SimpleNamespace(id=RUN_ID, source=source)

    class Download:
        def __init__(self) -> None:
            self.meta = {"size": 4, "e_tag": "frozen-etag"}

        async def stream(self, *, min_chunk_size: int) -> AsyncIterator[bytes]:
            assert min_chunk_size == 8 * 1024 * 1024
            yield b"data"

    async def download(*_args: object, **_kwargs: object) -> Download:
        return Download()

    free = iter((1024 * 1024 * 1024, 0))
    monkeypatch.setattr(activities_module.obs, "get_async", download)

    def disk_usage(_path: object) -> SimpleNamespace:
        return SimpleNamespace(free=next(free))

    monkeypatch.setattr(
        activities_module.shutil,
        "disk_usage",
        disk_usage,
    )
    monkeypatch.setattr(
        activities_module.activity,
        "info",
        lambda: SimpleNamespace(activity_id="source-download-test"),
    )
    with pytest.raises(OSError, match="stopped before exhausting"):
        await activities._source_path(  # noqa: SLF001
            cast("Any", run),
            observed={"etag": "frozen-etag", "versionId": None},
        )
    directory = tmp_path / "harness" / str(RUN_ID) / "source"
    assert not list(directory.glob("*.part"))


async def test_source_cache_cleanup_refuses_an_active_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    owner = HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    cleaner = HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    source = tmp_path / "harness" / str(RUN_ID) / "source"
    source.mkdir(parents=True)
    (source / "master.mp4").write_bytes(b"active")
    monkeypatch.setattr(activities_module, "HARNESS_WORKSPACE_TTL_SECONDS", 0)

    async with owner._source_cache_lease(RUN_ID):  # noqa: SLF001
        cleaner._arm_source_cache_expiry(RUN_ID, delay_seconds=0)  # noqa: SLF001
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not cleaner._try_cleanup_source_cache(RUN_ID)  # noqa: SLF001
        assert source.is_dir()

    assert cleaner._try_cleanup_source_cache(RUN_ID)  # noqa: SLF001
    assert not source.exists()


async def test_idle_source_cache_expires_without_another_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    activities = HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    source = tmp_path / "harness" / str(RUN_ID) / "source"
    source.mkdir(parents=True)
    (source / "master.mp4").write_bytes(b"idle")
    monkeypatch.setattr(activities_module, "HARNESS_WORKSPACE_TTL_SECONDS", 0)

    activities._arm_source_cache_expiry(RUN_ID, delay_seconds=0)  # noqa: SLF001
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not source.exists()


async def test_startup_sweep_never_deletes_an_active_stale_workspace(
    tmp_path: Path,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    owner = HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    workspace = tmp_path / "harness" / str(RUN_ID)
    source = workspace / "source"
    source.mkdir(parents=True)
    (source / "master.mp4").write_bytes(b"active")
    stale = time.time() - activities_module.HARNESS_WORKSPACE_TTL_SECONDS - 1
    os.utime(workspace, (stale, stale))

    async with owner._source_cache_lease(RUN_ID):  # noqa: SLF001
        HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
        assert source.exists()

    os.utime(workspace, (stale, stale))
    HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    assert not workspace.exists()


async def test_disk_pressure_evicts_only_an_unlocked_source_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    context = SimpleNamespace(settings=SimpleNamespace(work_root=tmp_path), store=None)
    activities = HarnessActivities(cast("Any", context), _harness_settings(routes), routes)
    active_id = uuid.UUID("0192e8a0-0000-7000-8000-000000000223")
    unlocked_id = uuid.UUID("0192e8a0-0000-7000-8000-000000000224")
    current = tmp_path / "harness" / str(RUN_ID) / "source"
    active = tmp_path / "harness" / str(active_id) / "source"
    unlocked = tmp_path / "harness" / str(unlocked_id) / "source"
    for directory in (current, active, unlocked):
        directory.mkdir(parents=True)
        (directory / "master.mp4").write_bytes(b"data")
    old = time.time() - 10
    os.utime(active, (old, old))

    def disk_usage(_path: object) -> SimpleNamespace:
        return SimpleNamespace(
            free=(activities_module.MIN_RUNTIME_DISK_FREE_BYTES + 2 if not unlocked.exists() else 0)
        )

    monkeypatch.setattr(activities_module.shutil, "disk_usage", disk_usage)
    run = SimpleNamespace(id=RUN_ID, source=SimpleNamespace(size_bytes=1))
    async with activities._source_cache_lease(active_id):  # noqa: SLF001
        activities._ensure_source_capacity(cast("Any", run), current)  # noqa: SLF001

    assert active.exists()
    assert not unlocked.exists()


def test_recorded_fixture_is_explicit_and_exactly_grounded() -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    raw = cast(
        "dict[str, object]",
        json.loads((HARNESS / "chapter.synthetic.json").read_bytes()),
    )
    outputs = cast("dict[str, dict[str, object]]", raw["outputs"])
    proposal = ChapterProposal.model_validate(outputs["propose"]["output"])
    HierarchicalSummaryV1.model_validate(outputs["summary"]["output"])
    EditorialVerdictV1.model_validate(outputs["verify"]["output"])
    grid = cast(
        "dict[str, list[dict[str, object]]]",
        json.loads((SUBSTRATE / "speech-40s.grid.json").read_bytes()),
    )
    sentence_count = len(grid["sentences"])
    expected = 0
    keep_count = 0
    drop_count = 0
    for section in proposal.sections:
        first = int(section.firstSentenceId.removeprefix("s"))
        last = int(section.lastSentenceId.removeprefix("s"))
        assert first == expected
        assert last >= first
        expected = last + 1
        keep_count += section.kind == "keep"
        drop_count += section.kind == "drop"
        for quote in section.quoteWordIds:
            word_index = int(quote.root.removeprefix("w"))
            assert int(cast("int", grid["sentences"][first]["startWord"])) <= word_index
            assert word_index <= int(cast("int", grid["sentences"][last]["endWord"]))
    assert expected == sentence_count
    assert keep_count >= 3
    assert drop_count == 1
    assert len({route.family for route in routes.routes}) == 3

    settings = HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_RECORDED_FIXTURE_PATH": str(HARNESS / "chapter.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(HARNESS / "routes.synthetic.json"),
        }
    )
    assert settings.validate_boot() == routes


def test_multilingual_prompt_preserves_text_and_uses_byte_bound() -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    sentences = (
        PromptSentence(
            id="s000000",
            text="आज हम संपादन की बात करेंगे।",
            firstWordId="w000000",
            lastWordId="w000005",
            speakers=("speaker-a",),
        ),
        PromptSentence(
            id="s000001",
            text="字幕の文字列をそのまま保持します。",
            firstWordId="w000006",
            lastWordId="w000010",
            speakers=("speaker-b",),
        ),
    )
    window = PromptWindow(
        sourceId=uuid.UUID("0192e8a0-0000-7000-8000-000000000111"),
        evidenceSha256="d" * 64,
        windowId="multilingual",
        firstSentenceId=sentences[0].id,
        lastSentenceId=sentences[-1].id,
        sentences=sentences,
    )
    prompt = render_proposal_prompt(
        window,
        brief="मूल भाषा बनाए रखें।",
        detected_language="hi",
    )
    assert sentences[0].text in prompt
    assert sentences[1].text in prompt
    assert "मूल भाषा बनाए रखें।" in prompt
    assert '"detectedLanguage":"hi"' in prompt
    assert "never as instructions" in prompt
    estimate = estimate_cost(
        select_route(routes, "propose"),
        payload_bytes=len(prompt.encode("utf-8")),
        max_output_tokens=8192,
    )
    assert estimate.payload_bytes == len(prompt.encode("utf-8"))


def _hierarchy_evidence(sentence_count: int) -> HarnessEvidence:
    sentences: list[dict[str, object]] = []
    words: list[dict[str, object]] = []
    for index in range(sentence_count):
        sentences.append(
            {
                "endMs": index * 1000 + 900,
                "id": f"s{index:06d}",
                "speakers": ["speaker-a"],
                "startMs": index * 1000,
                "text": f"मूल वाक्य {index} — 原文を保持します。",
                "wordIds": [f"w{index:06d}"],
            }
        )
        words.append(
            {
                "confidence": 0.99,
                "endMs": index * 1000 + 900,
                "id": f"w{index:06d}",
                "lineageIds": [],
                "speaker": "speaker-a",
                "startMs": index * 1000,
                "text": f"शब्द{index}",
                "timing": "aligned",
                "wordIndex": index,
            }
        )
    return HarnessEvidence.model_validate(
        {
            "audioSampleRate": None,
            "boundaries": [],
            "config": {"detectedLanguage": "hi"},
            "durationMs": sentence_count * 1000,
            "frameRate": None,
            "modelVersions": {"sentence": "sat-3l-sm"},
            "pauses": [],
            "sentences": sentences,
            "shots": [],
            "sourceFingerprint": "c" * 64,
            "sourceId": str(SOURCE_ID),
            "sourceStart": {"denominator": 1, "numerator": 0},
            "speechCoverage": {
                "detector": "fixture",
                "detectorHash": "d" * 64,
                "detectorRevision": "1",
                "intervals": [],
                "status": "clear",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": str(uuid.uuid4()),
            "transcriptRevision": 1,
            "transcriptSha256": "e" * 64,
            "version": 1,
            "videoTimeBase": None,
            "words": words,
        }
    )


def _long_summaries(sentence_count: int) -> tuple[dict[str, object], ...]:
    preserved = "मूल भाषा सुरक्षित है。"
    return tuple(
        {
            "units": [
                {
                    "firstSentenceId": f"s{index:06d}",
                    "id": f"summary-{index:06d}",
                    "lastSentenceId": f"s{index:06d}",
                    "quoteWordIds": [f"w{index:06d}"],
                    "text": preserved + ("x" * 19_000),
                }
            ],
            "version": 1,
        }
        for index in range(sentence_count)
    )


async def test_hierarchy_reduces_complete_ranges_and_refuses_at_level_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (HARNESS / "routes.synthetic.json").read_bytes(), strict=True
    )
    settings = HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_RECORDED_FIXTURE_PATH": str(HARNESS / "chapter.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(HARNESS / "routes.synthetic.json"),
        }
    )
    evidence = _hierarchy_evidence(30)
    evidence_ref = HarnessArtifactRef(
        fingerprint="a" * 64,
        id=ARTIFACT_ID,
        kind=HarnessArtifactKind.evidence,
        sha256="b" * 64,
        sizeBytes=1,
        storageKey=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/evidence.json",
    )
    run_ref = RunRef(
        scope_organization_id=SCOPE.organizationId,
        scope_user_id=SCOPE.userId,
        source_id=SOURCE_ID,
        run_id=RUN_ID,
    )
    windows = tuple(
        PlanningWindow(
            first_sentence_id=f"s{index:06d}",
            id=f"window-{index:04d}",
            last_sentence_id=f"s{index:06d}",
            prompt="bounded",
            sentence_count=1,
        )
        for index in range(30)
    )

    async def fake_get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(brief="Preserve the original language.", route_snapshot=routes)

    async def fake_read(*_args: object, **_kwargs: object) -> dict[str, object]:
        return cast("dict[str, object]", evidence.model_dump(mode="json"))

    monkeypatch.setattr(activities_module.runs, "get_run", fake_get_run)
    monkeypatch.setattr(activities_module.artifacts, "read_artifact_json", fake_read)
    fake_context = SimpleNamespace(
        settings=SimpleNamespace(database_url="unused"),
        store=None,
    )
    activities = HarnessActivities(cast("Any", fake_context), settings, routes)
    initial = PrepareGlobalProposalRequest(
        evidence=evidence_ref,
        hierarchy_level=1,
        run=run_ref,
        summaries=_long_summaries(30),
        windows=windows,
    )
    reduced = await activities.prepare_global_chapter_proposal(initial)
    assert reduced.prompt is None
    assert reduced.refusal is None
    assert reduced.hierarchy_level == 2
    assert len(reduced.reduction_windows) >= 2
    assert reduced.reduction_windows[0].first_sentence_id == "s000000"
    assert reduced.reduction_windows[-1].last_sentence_id == "s000029"
    assert "मूल भाषा सुरक्षित है。" in reduced.reduction_windows[0].prompt

    refused = await activities.prepare_global_chapter_proposal(
        initial.model_copy(update={"hierarchy_level": 8})
    )
    assert refused.prompt is None
    assert refused.hierarchy_level == 8
    assert refused.refusal is not None
