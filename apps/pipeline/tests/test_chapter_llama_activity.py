"""Real scoped ledger tests for Chapter-Llama's remote-to-local commit gap."""

from __future__ import annotations

# ruff: noqa: EM101, TRY003, PLR0915, FBT001
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from obstore.store import MemoryStore
from psycopg.types.json import Jsonb

from temnia_pipeline import db
from temnia_pipeline.chapter_llama.candidate import CandidatePayload
from temnia_pipeline.chapter_llama.checkpoints import prepare_execution, publish_outcome
from temnia_pipeline.chapter_llama.client import ChapterLlamaConfig, DeploymentIdentity
from temnia_pipeline.chapter_llama.contracts import (
    ChapterLlamaJob,
    ChapterLlamaOutcome,
    InferenceResult,
    ModelConfig,
    ResourceProfile,
    parse_predictions,
)
from temnia_pipeline.contracts import HarnessArtifactKind, HarnessArtifactRef
from temnia_pipeline.harness import artifacts, chapter_llama_activity, ledger
from temnia_pipeline.harness.chapter_llama_activity import (
    CandidateRequest,
    CandidateRuntime,
    ChapterLlamaActivities,
)
from temnia_pipeline.harness.runtime_types import RunRef
from temnia_pipeline.speech.checkpoints import ObstoreCheckpointStore
from test_harness_persistence import SEEDED, make_case, pipeline_url
from test_proposal_diagnostics_persistence import _evidence  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from obstore.store import S3Store


CONFIG = ChapterLlamaConfig(
    app_name="temnia-chapter-llama",
    environment="staging",
    deployment=DeploymentIdentity(
        build="e" * 64, config=ModelConfig(), resources=ResourceProfile()
    ),
)


@pytest.mark.parametrize("remote_failed", [False, True])
async def test_checkpoint_reconciles_unknown_without_redispatch_or_invoice_fabrication(
    monkeypatch: pytest.MonkeyPatch, remote_failed: bool
) -> None:
    url = pipeline_url()
    case = await make_case(url, budget=3_000_000)
    store = cast("S3Store", MemoryStore())
    evidence = _evidence(case.source_id)
    async with db.scoped(url, SEEDED) as conn:
        await conn.execute(
            "INSERT INTO transcript (id, organization_id, source_id, current_revision, status) "
            "VALUES (%s, %s, %s, 1, 'ready')",
            (evidence.transcriptId, SEEDED.organizationId, case.source_id),
        )
        await conn.execute(
            "INSERT INTO transcript_revision (organization_id, transcript_id, revision, kind, "
            "storage_key, size_bytes, word_count) VALUES (%s, %s, 1, 'machine', %s, 1, 1)",
            (
                SEEDED.organizationId,
                evidence.transcriptId,
                f"chapter-llama/{case.source_id}/transcript.json",
            ),
        )
    saved = await artifacts.publish_json(
        url,
        scope=SEEDED,
        source_id=case.source_id,
        store=store,
        identity=artifacts.ArtifactIdentity(
            kind="evidence",
            fingerprint="a" * 64,
            transcript_id=evidence.transcriptId,
            transcript_revision=1,
        ),
        content=evidence.model_dump(mode="json"),
        metadata={},
    )
    ref = HarnessArtifactRef(
        id=saved.id,
        kind=HarnessArtifactKind.evidence,
        fingerprint=saved.fingerprint,
        sha256=saved.sha256,
        storageKey=saved.storage_key,
        sizeBytes=saved.size_bytes,
    )
    async with db.scoped(url, SEEDED) as conn:
        await conn.execute(
            "UPDATE harness_run SET evidence_artifact_id = %s, route_snapshot = %s WHERE id = %s",
            (ref.id, Jsonb({"chapterLlama": CONFIG.model_dump(mode="json")}), case.run_id),
        )

    async def get_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            id=case.run_id,
            source_id=case.source_id,
            evidence_artifact_id=ref.id,
            config=SimpleNamespace(maxDispatches=3),
        )

    class FakeClient:
        spawn_count = 0
        identity_count = 0

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def deployment_identity(self) -> DeploymentIdentity:
            FakeClient.identity_count += 1
            if FakeClient.identity_count > 1:
                raise OSError("old deployment no longer resolves")
            return CONFIG.deployment

        async def spawn(self, job: ChapterLlamaJob) -> str:
            FakeClient.spawn_count += 1
            checkpoints = ObstoreCheckpointStore(store)
            assert (
                await prepare_execution(
                    checkpoints,
                    job,
                    build=job.expected_build,
                    call_id="fc-fixture",
                    task_id="ta-fixture",
                )
                is None
            )
            raw = "00:00:00 - Source topic"
            result = InferenceResult(
                input_sha256=job.input.sha256,
                config=job.input.config,
                raw_output=raw,
                predictions=parse_predictions(raw, job.input),
                input_tokens=120,
                output_tokens=12,
                elapsed_seconds=2.0,
            )
            outcome = ChapterLlamaOutcome(
                status="failed" if remote_failed else "ok",
                job_sha256=job.sha256,
                build=job.expected_build,
                modal_call_id="fc-fixture",
                modal_task_id="ta-fixture",
                compute_seconds=3.0,
                result=None if remote_failed else result,
                error_code="InvalidGenerationError" if remote_failed else None,
                error_message="invalid retained output" if remote_failed else None,
                rejected_output="bad output" if remote_failed else None,
                rejected_input_tokens=120 if remote_failed else None,
                rejected_output_tokens=2 if remote_failed else None,
            )
            await publish_outcome(checkpoints, job, outcome)
            raise OSError("spawn response was lost after immutable remote completion")

    monkeypatch.setattr(chapter_llama_activity.runs, "get_run", get_run)
    monkeypatch.setattr(chapter_llama_activity, "ChapterLlamaModalClient", FakeClient)
    monkeypatch.setattr(
        chapter_llama_activity.activity,
        "info",
        lambda: SimpleNamespace(workflow_run_id="workflow", activity_id="candidate", attempt=1),
    )

    def heartbeat(*_args: object) -> None:
        pass

    monkeypatch.setattr(chapter_llama_activity.activity, "heartbeat", heartbeat)
    request = CandidateRequest(
        run=RunRef(
            scope_organization_id=SEEDED.organizationId,
            scope_user_id=SEEDED.userId,
            source_id=case.source_id,
            run_id=case.run_id,
        ),
        evidence=ref,
        configuration=CONFIG,
    )
    handler = ChapterLlamaActivities(CandidateRuntime(database_url=url, store=store))
    try:
        with pytest.raises(OSError, match="spawn response was lost"):
            await handler.generate(request)
        async with db.scoped(url, SEEDED) as conn:
            unknown = await (
                await conn.execute(
                    "SELECT status, dispatch_count FROM harness_run WHERE id = %s", (case.run_id,)
                )
            ).fetchone()
        assert unknown == {"status": "outcome_unknown", "dispatch_count": 1}

        if remote_failed:
            with pytest.raises(ValueError, match="invalid retained output"):
                await handler.generate(request)
            with pytest.raises(ValueError, match="already failed"):
                await handler.generate(request)
        else:
            result = await handler.generate(request)
            assert "sentence-0" in result.hints
            assert "Source topic" in result.hints
            assert await handler.generate(request) == result
        assert FakeClient.spawn_count == 1
        assert FakeClient.identity_count == 1
        async with db.scoped(url, SEEDED) as conn:
            row = await (
                await conn.execute(
                    "SELECT r.status, r.dispatch_count, r.reserved_micros, r.spent_micros, "
                    "a.state, a.cost_status, a.actual_cost_micros, a.usage "
                    "FROM harness_run r JOIN harness_attempt a ON a.run_id = r.id WHERE r.id = %s",
                    (case.run_id,),
                )
            ).fetchone()
        assert row is not None
        assert row["status"] != "outcome_unknown"
        assert row["dispatch_count"] == 1
        assert row["state"] == ("failed_known" if remote_failed else "succeeded")
        assert row["reserved_micros"] == CONFIG.deployment.resources.reservation_micros
        assert row["spent_micros"] == 0
        assert row["actual_cost_micros"] is None
        assert row["cost_status"] == "unknown"
        assert row["usage"]["invoiceStatus"] == "unavailable"
        assert row["usage"]["estimatedComputeMicros"] == 1997
        if remote_failed:
            retained = await artifacts.read_artifact_json(
                url,
                scope=SEEDED,
                source_id=case.source_id,
                store=store,
                artifact_id=UUID(row["usage"]["candidateArtifactId"]),
            )
            assert CandidatePayload.model_validate(retained).outcome.rejected_output == "bad output"
        wrong = request.model_copy(
            update={"configuration": CONFIG.model_copy(update={"environment": "other"})}
        )
        with pytest.raises(ledger.IdentityConflict, match="not frozen"):
            await handler.generate(wrong)
        assert FakeClient.spawn_count == 1
    finally:
        await db.close_pool()
