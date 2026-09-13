"""Shared synthetic run fixtures for the topic workflow and activity tests."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import uuid
from pathlib import Path

from temnia_pipeline.contracts import (
    ChapterRunInput,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    HarnessRunStatus,
    Scope,
)
from temnia_pipeline.harness.routes import RouteSnapshot
from temnia_pipeline.harness.runtime_types import (
    PinnedSource,
    PinnedTranscript,
    RunSnapshot,
    StartRunRequest,
)
from temnia_pipeline.harness.settings import HarnessSettings

FIXTURES = Path(__file__).parent / "fixtures/harness"
SOURCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000111")
RUN_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000222")
EVIDENCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000333")
REQUEST_KEY = uuid.UUID("0192e8a0-0000-7000-8000-000000000444")
TRANSCRIPT_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000555")
SCOPE = Scope(
    organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
    userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
)


def _settings() -> tuple[HarnessSettings, RouteSnapshot]:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    settings = HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_MAX_REPAIRS": "0",
            "HARNESS_RECORDED_FIXTURE_PATH": str(FIXTURES / "topic.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(FIXTURES / "routes.synthetic.json"),
        }
    )
    assert settings.validate_boot() == routes
    return settings, routes


def _evidence() -> HarnessEvidence:
    words: list[dict[str, object]] = []
    sentences: list[dict[str, object]] = []
    for index in range(4):
        words.append(
            {
                "confidence": 0.99,
                "endMs": index * 1000 + 900,
                "id": f"w{index:06d}",
                "lineageIds": [],
                "speaker": "speaker-a",
                "startMs": index * 1000,
                "text": f"word-{index}",
                "timing": "aligned",
                "wordIndex": index,
            }
        )
        sentences.append(
            {
                "endMs": index * 1000 + 900,
                "id": f"s{index:06d}",
                "speakers": ["speaker-a"],
                "startMs": index * 1000,
                "text": f"Original sentence {index}.",
                "wordIds": [f"w{index:06d}"],
            }
        )
    return HarnessEvidence.model_validate(
        {
            "audioSampleRate": None,
            "boundaries": [],
            "config": {},
            "durationMs": 4000,
            "frameRate": None,
            "modelVersions": {"sentence": "fixture"},
            "pauses": [],
            "sentences": sentences,
            "shots": [],
            "sourceFingerprint": "a" * 64,
            "sourceId": str(SOURCE_ID),
            "sourceStart": {"denominator": 1, "numerator": 0},
            "speechCoverage": {
                "detector": "fixture",
                "detectorHash": "b" * 64,
                "detectorRevision": "1",
                "intervals": [],
                "status": "clear",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": str(TRANSCRIPT_ID),
            "transcriptRevision": 1,
            "transcriptSha256": "c" * 64,
            "version": 1,
            "videoTimeBase": None,
            "words": words,
        }
    )


EVIDENCE = _evidence()
EVIDENCE_REF = HarnessArtifactRef(
    fingerprint="d" * 64,
    id=EVIDENCE_ID,
    kind=HarnessArtifactKind.evidence,
    sha256="e" * 64,
    sizeBytes=1,
    storageKey=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/evidence.json",
)


def _snapshot(
    request: StartRunRequest,
    routes: RouteSnapshot,
    *,
    status: HarnessRunStatus = HarnessRunStatus.running,
) -> RunSnapshot:
    return RunSnapshot(
        accepted_revision=None,
        brief=request.request.brief or "",
        budget_micros=request.request.budgetMicros,
        config=request.request.config,
        current_revision=0,
        dispatch_count=0,
        error_message=None,
        evidence_artifact_id=EVIDENCE_ID,
        id=request.request.runId,
        initial_budget_micros=request.request.budgetMicros,
        repair_count=0,
        request_key=request.request.requestKey,
        reserved_micros=0,
        route_snapshot=routes,
        source=PinnedSource(
            duration_ms=4000,
            size_bytes=1,
            storage_key=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/master.mp4",
        ),
        source_id=request.request.sourceId,
        spent_micros=0,
        stage="planning",
        status=status,
        transcript=PinnedTranscript(
            revision=1,
            sha256="c" * 64,
            size_bytes=1,
            storage_key=f"org/{SCOPE.organizationId}/source/{SOURCE_ID}/transcript.json",
            transcript_id=TRANSCRIPT_ID,
        ),
        workflow_id=request.workflow.workflow_id,
        workflow_run_id=request.workflow.workflow_run_id,
    )


def _request() -> ChapterRunInput:
    settings, _ = _settings()
    return ChapterRunInput(
        brief="Preserve every original sentence.",
        budgetMicros=1_000_000,
        config=settings.allowed_config(),
        requestKey=REQUEST_KEY,
        runId=RUN_ID,
        scope=SCOPE,
        sourceId=SOURCE_ID,
    )
