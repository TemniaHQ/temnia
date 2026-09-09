"""Deterministic summary grounding and immutable identity regressions."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic_ai import ModelResponse, TextPart

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
)
from temnia_pipeline.harness import activities as activities_module
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.activities import HarnessActivities
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.models import HierarchicalSummaryV1
from temnia_pipeline.harness.prompts import (
    PromptSentence,
    PromptWindow,
    render_summary_prompt,
    render_summary_reduction_prompt,
)
from temnia_pipeline.harness.routes import RouteSnapshot
from temnia_pipeline.harness.runtime_types import PlanningWindow, RunRef, ValidateSummaryRequest
from temnia_pipeline.harness.settings import HarnessSettings
from temnia_pipeline.harness.summary_grounding import (
    SummaryGroundingRefusal,
    SummaryGroundingReport,
    allowed_anchors_from_exact_prompt,
    ground_summary,
    grounding_artifact_fingerprint,
    normalized_summary_from_response,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

FIXTURES = Path(__file__).parent / "fixtures/harness"

SOURCE_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000111")
RUN_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000222")
TRANSCRIPT_ID = uuid.UUID("0192e8a0-0000-7000-8000-000000000333")


def _evidence() -> HarnessEvidence:
    words: list[dict[str, object]] = []
    sentences: list[dict[str, object]] = []
    for sentence_index in range(4):
        word_ids: list[str] = []
        for offset in range(2):
            word_index = sentence_index * 2 + offset
            word_id = f"word-{word_index}"
            word_ids.append(word_id)
            words.append(
                {
                    "confidence": 1.0,
                    "endMs": word_index * 500 + 450,
                    "id": word_id,
                    "lineageIds": [],
                    "speaker": "speaker-a",
                    "startMs": word_index * 500,
                    "text": f"token-{word_index}",
                    "timing": "aligned",
                    "wordIndex": word_index,
                }
            )
        sentences.append(
            {
                "endMs": sentence_index * 1000 + 900,
                "id": f"sentence-{sentence_index}",
                "speakers": ["speaker-a"],
                "startMs": sentence_index * 1000,
                "text": f"Exact source sentence {sentence_index}.",
                "wordIds": word_ids,
            }
        )
    return HarnessEvidence.model_validate(
        {
            "audioSampleRate": None,
            "boundaries": [],
            "config": {},
            "durationMs": 4000,
            "frameRate": None,
            "modelVersions": {},
            "pauses": [],
            "sentences": sentences,
            "shots": [],
            "sourceFingerprint": "a" * 64,
            "sourceId": str(SOURCE_ID),
            "sourceStart": {"denominator": 1, "numerator": 0},
            "speechCoverage": {
                "detector": None,
                "detectorHash": None,
                "detectorRevision": None,
                "intervals": [],
                "status": "unknown",
                "uncoveredSpeechMs": 0,
                "uncoveredTailMs": 0,
                "warnings": [],
            },
            "transcriptId": str(TRANSCRIPT_ID),
            "transcriptRevision": 1,
            "transcriptSha256": "b" * 64,
            "version": 1,
            "videoTimeBase": None,
            "words": words,
        }
    )


def _summary(*, bad_quote: str = "word-4") -> HierarchicalSummaryV1:
    return HierarchicalSummaryV1.model_validate(
        {
            "version": 1,
            "units": [
                {
                    "id": "first",
                    "firstSentenceId": "sentence-0",
                    "lastSentenceId": "sentence-1",
                    "quoteWordIds": ["word-0", bad_quote],
                    "text": "Generated first unit.",
                },
                {
                    "id": "second",
                    "firstSentenceId": "sentence-2",
                    "lastSentenceId": "sentence-3",
                    "quoteWordIds": ["word-4", "word-7"],
                    "text": "Generated second unit.",
                },
            ],
        }
    )


def _ref(kind: HarnessArtifactKind, value: int) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        fingerprint=f"{value:x}" * 64,
        id=uuid.UUID(f"0192e8a0-0000-7000-8000-{value:012d}"),
        kind=kind,
        sha256=f"{value + 1:x}" * 64,
        sizeBytes=100,
        storageKey=f"org/test/source/{SOURCE_ID}/{value}.json",
    )


def _summary_prompt(evidence: HarnessEvidence) -> str:
    return render_summary_prompt(
        PromptWindow(
            sourceId=SOURCE_ID,
            evidenceSha256=_ref(HarnessArtifactKind.evidence, 1).sha256,
            windowId="window-0000",
            firstSentenceId="sentence-0",
            lastSentenceId="sentence-3",
            sentences=tuple(
                PromptSentence(
                    id=sentence.id,
                    text=sentence.text,
                    firstWordId=sentence.wordIds[0].root,
                    lastWordId=sentence.wordIds[-1].root,
                    speakers=tuple(sentence.speakers),
                )
                for sentence in evidence.sentences
            ),
        )
    )


def test_neighbour_anchor_replaces_complete_unit_and_preserves_valid_unit() -> None:
    evidence = _evidence()
    supplied = _summary()
    result = ground_summary(
        evidence=evidence,
        window_first_sentence_id="sentence-0",
        window_last_sentence_id="sentence-3",
        window_sentence_count=4,
        summary=supplied,
        allowed_model_anchors=frozenset(
            {"word-0", "word-1", "word-2", "word-3", "word-4", "word-5", "word-6", "word-7"}
        ),
    )

    assert result.summary.units[0].text == ("Exact source sentence 0. Exact source sentence 1.")
    assert result.summary.units[0].quoteWordIds == ["word-0", "word-3"]
    assert result.summary.units[1] == supplied.units[1]
    assert result.fallbacks[0].rejectedQuoteWordIds == ("word-4",)
    assert result.fallbacks[0].replacementQuoteWordIds == ("word-0", "word-3")


@pytest.mark.parametrize("bad_quote", ["word-1", "foreign-word"])
def test_non_prompt_or_foreign_anchor_is_never_repaired(bad_quote: str) -> None:
    with pytest.raises(SummaryGroundingRefusal, match="not an anchor present"):
        ground_summary(
            evidence=_evidence(),
            window_first_sentence_id="sentence-0",
            window_last_sentence_id="sentence-3",
            window_sentence_count=4,
            summary=_summary(bad_quote=bad_quote),
            allowed_model_anchors=frozenset({"word-0", "word-3", "word-4", "word-7"}),
        )


def test_gap_or_overlap_is_never_repaired() -> None:
    supplied = _summary().model_copy(
        update={
            "units": [
                _summary().units[0].model_copy(update={"lastSentenceId": "sentence-0"}),
                _summary().units[1],
            ]
        }
    )
    with pytest.raises(SummaryGroundingRefusal, match="exactly cover"):
        ground_summary(
            evidence=_evidence(),
            window_first_sentence_id="sentence-0",
            window_last_sentence_id="sentence-3",
            window_sentence_count=4,
            summary=supplied,
            allowed_model_anchors=frozenset({"word-0", "word-3", "word-4", "word-7"}),
        )


@pytest.mark.parametrize("text_size", [0, 20_001])
def test_empty_or_oversized_extractive_unit_refuses(text_size: int) -> None:
    evidence = _evidence().model_copy(deep=True)
    evidence.sentences[0].text = "x" * text_size
    evidence.sentences[1].text = ""
    with pytest.raises(SummaryGroundingRefusal, match="empty or exceeds"):
        ground_summary(
            evidence=evidence,
            window_first_sentence_id="sentence-0",
            window_last_sentence_id="sentence-3",
            window_sentence_count=4,
            summary=_summary(),
            allowed_model_anchors=frozenset({"word-0", "word-3", "word-4", "word-7"}),
        )


def test_reduction_uses_exact_supplied_anchors_but_fallback_derives_source_endpoints() -> None:
    evidence = _evidence()
    prior_summary = _summary().model_copy(
        update={
            "units": [
                _summary().units[0].model_copy(update={"quoteWordIds": ["word-0"]}),
                _summary().units[1],
            ]
        }
    )
    report = SummaryGroundingReport(
        runId=RUN_ID,
        hierarchyLevel=1,
        modelStage="summary:window-0000",
        windowId="window-0000",
        firstSentenceId="sentence-0",
        lastSentenceId="sentence-3",
        windowSentenceCount=4,
        windowPromptSha256="c" * 64,
        evidence=_ref(HarnessArtifactKind.evidence, 1),
        rawResponse=_ref(HarnessArtifactKind.model_response, 2),
        sourceSummarySha256="d" * 64,
        normalizedSummary=prior_summary,
    )
    prompt = render_summary_reduction_prompt(
        source_id=SOURCE_ID,
        evidence_sha256=report.evidence.sha256,
        summaries=[prior_summary.model_dump(mode="json")],
        hierarchy_level=2,
    )
    allowed = allowed_anchors_from_exact_prompt(
        evidence=evidence,
        evidence_sha256=report.evidence.sha256,
        source_id=SOURCE_ID,
        window_id="hierarchy-2-0000",
        window_first_sentence_id="sentence-0",
        window_last_sentence_id="sentence-3",
        window_sentence_count=4,
        prompt=prompt,
        hierarchy_level=2,
        input_reports=(report,),
    )
    result = ground_summary(
        evidence=evidence,
        window_first_sentence_id="sentence-0",
        window_last_sentence_id="sentence-3",
        window_sentence_count=4,
        summary=_summary(),
        allowed_model_anchors=allowed,
    )

    assert "word-3" not in allowed
    assert result.summary.units[0].quoteWordIds == ["word-0", "word-3"]


def test_retained_response_applies_same_cosmetic_label_normalization() -> None:
    body = _summary().model_dump(mode="json")
    body["units"][0]["id"] = ""
    response = ModelResponse(parts=[TextPart(json.dumps(body))], model_name="summary")
    stored = MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json", by_alias=True)

    normalized = normalized_summary_from_response(stored)

    assert normalized.units[0].id.startswith("summary-0000-")
    assert normalized.units[1].id == "second"


def test_grounding_fingerprint_is_portable_and_sensitive_to_lineage() -> None:
    report = SummaryGroundingReport(
        runId=RUN_ID,
        hierarchyLevel=1,
        modelStage="summary:window-0000",
        windowId="window-0000",
        firstSentenceId="sentence-0",
        lastSentenceId="sentence-3",
        windowSentenceCount=4,
        windowPromptSha256="c" * 64,
        evidence=_ref(HarnessArtifactKind.evidence, 1),
        rawResponse=_ref(HarnessArtifactKind.model_response, 2),
        sourceSummarySha256="d" * 64,
        normalizedSummary=_summary(),
    )
    fingerprint = grounding_artifact_fingerprint(report)

    assert len(fingerprint) == 64
    assert fingerprint == grounding_artifact_fingerprint(report)
    assert fingerprint != grounding_artifact_fingerprint(
        report.model_copy(update={"windowPromptSha256": "e" * 64})
    )


async def test_validation_activity_binds_raw_response_and_publishes_exact_dependencies(  # noqa: C901
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = RouteSnapshot.model_validate_json(
        (FIXTURES / "routes.synthetic.json").read_bytes(), strict=True
    )
    settings = HarnessSettings.from_env(
        {
            "HARNESS_ALLOW_RECORDED": "1",
            "HARNESS_BACKEND": "recorded",
            "HARNESS_ENABLED": "1",
            "HARNESS_RECORDED_FIXTURE_PATH": str(FIXTURES / "chapter.synthetic.json"),
            "HARNESS_ROUTE_SNAPSHOT_ID": routes.snapshot_id,
            "HARNESS_ROUTE_SNAPSHOT_PATH": str(FIXTURES / "routes.synthetic.json"),
        }
    )
    scope = Scope(
        organizationId=uuid.UUID("0192e8a0-0000-7000-8000-000000000001"),
        userId=uuid.UUID("0192e8a0-0000-7000-8000-000000000002"),
    )
    evidence_ref = _ref(HarnessArtifactKind.evidence, 1)
    raw_ref = _ref(HarnessArtifactKind.model_response, 2)
    evidence = _evidence()
    supplied = _summary()
    response = ModelResponse(
        parts=[TextPart(json.dumps(supplied.model_dump(mode="json")))], model_name="summary"
    )
    stored_response = MODEL_RESPONSE_ADAPTER.dump_python(response, mode="json", by_alias=True)
    evidence_artifact = artifacts.HarnessArtifact(
        id=evidence_ref.id,
        organization_id=scope.organizationId,
        source_id=SOURCE_ID,
        kind="evidence",
        fingerprint=evidence_ref.fingerprint,
        storage_key=evidence_ref.storageKey,
        sha256=evidence_ref.sha256,
        size_bytes=evidence_ref.sizeBytes,
        metadata={"format": "harness-evidence/1"},
        transcript_id=evidence.transcriptId,
        transcript_revision=evidence.transcriptRevision,
        dependency_ids=(),
    )
    operation_id = uuid.UUID("0192e8a0-0000-7000-8000-000000000777")
    raw_artifact = artifacts.HarnessArtifact(
        id=raw_ref.id,
        organization_id=scope.organizationId,
        source_id=SOURCE_ID,
        kind="model_response",
        fingerprint=raw_ref.fingerprint,
        storage_key=raw_ref.storageKey,
        sha256=raw_ref.sha256,
        size_bytes=raw_ref.sizeBytes,
        metadata={
            "operationId": str(operation_id),
            "promptVersion": "chapter-summarize-v3",
            "runId": str(RUN_ID),
            "schemaVersion": "hierarchical-summary/1",
        },
        transcript_id=None,
        transcript_revision=None,
        dependency_ids=(evidence_ref.id,),
    )

    class Cursor:
        async def fetchall(self) -> list[dict[str, object]]:
            return [{"operation_id": operation_id, "fingerprint": raw_ref.fingerprint}]

    class Connection:
        async def execute(self, _query: object, _parameters: object) -> Cursor:
            return Cursor()

    @asynccontextmanager
    async def scoped(*_args: object, **_kwargs: object) -> AsyncGenerator[Connection]:
        yield Connection()

    async def get_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(evidence_artifact_id=evidence_ref.id)

    async def read_json(*_args: object, artifact_id: uuid.UUID, **_kwargs: object) -> object:
        if artifact_id == evidence_ref.id:
            return evidence.model_dump(mode="json")
        if artifact_id == raw_ref.id:
            return stored_response
        message = "unexpected artifact read"
        raise AssertionError(message)

    async def find_artifact(
        *_args: object, identity: artifacts.ArtifactIdentity, **_kwargs: object
    ) -> artifacts.HarnessArtifact | None:
        if identity.kind == "evidence":
            return evidence_artifact
        if identity.kind == "model_response":
            return raw_artifact
        message = "unexpected artifact identity"
        raise AssertionError(message)

    published: dict[str, object] = {}

    async def publish_json(*_args: object, **kwargs: object) -> artifacts.HarnessArtifact:
        published.update(kwargs)
        identity = cast("artifacts.ArtifactIdentity", kwargs["identity"])
        content = kwargs["content"]
        body = artifacts.canonical_json(content)
        return artifacts.HarnessArtifact(
            id=uuid.UUID("0192e8a0-0000-7000-8000-000000000888"),
            organization_id=scope.organizationId,
            source_id=SOURCE_ID,
            kind=identity.kind,
            fingerprint=identity.fingerprint,
            storage_key=f"org/{scope.organizationId}/source/{SOURCE_ID}/grounding.json",
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            metadata=cast("dict[str, object]", kwargs["metadata"]),
            transcript_id=None,
            transcript_revision=None,
            dependency_ids=tuple(cast("tuple[uuid.UUID, ...]", kwargs["dependency_ids"])),
        )

    monkeypatch.setattr(activities_module.db, "scoped", scoped)
    monkeypatch.setattr(activities_module.runs, "get_run", get_run)
    monkeypatch.setattr(activities_module.artifacts, "read_artifact_json", read_json)
    monkeypatch.setattr(activities_module.artifacts, "find_artifact", find_artifact)
    monkeypatch.setattr(activities_module.artifacts, "publish_json", publish_json)
    instance = HarnessActivities(
        cast("Any", SimpleNamespace(settings=SimpleNamespace(database_url="unused"), store=None)),
        settings,
        routes,
    )
    result = await instance.validate_chapter_summary(
        ValidateSummaryRequest(
            run=RunRef(
                scope_organization_id=scope.organizationId,
                scope_user_id=scope.userId,
                source_id=SOURCE_ID,
                run_id=RUN_ID,
            ),
            evidence=evidence_ref,
            window=PlanningWindow(
                id="window-0000",
                first_sentence_id="sentence-0",
                last_sentence_id="sentence-3",
                sentence_count=4,
                prompt=_summary_prompt(evidence),
            ),
            summary=supplied.model_dump(mode="json"),
            model_stage="summary:window-0000",
        )
    )

    assert result.refusal is None
    assert result.artifact is not None
    assert cast("tuple[uuid.UUID, ...]", published["dependency_ids"]) == (
        evidence_ref.id,
        raw_ref.id,
    )
    report = SummaryGroundingReport.model_validate_json(
        artifacts.canonical_json(published["content"])
    )
    assert report.rawResponse == raw_ref
    assert report.fallbacks[0].unitId == "first"
    assert report.normalizedSummary.model_dump(mode="json") == result.summary
    assert cast("dict[str, object]", published["metadata"])["fallbackUnitCount"] == 1
