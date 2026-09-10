"""Budgeted, durable optional topic candidate stage before gateway chapter planning."""

# Pydantic resolves the imported runtime types; refusal messages belong at the fence.
# ruff: noqa: EM101, EM102, TRY003, TC001, TC002, TC003
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from uuid import UUID

from obstore.store import S3Store
from pydantic import BaseModel, ConfigDict, Field
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.chapter_llama.candidate import (
    CandidatePayload,
    candidate_hints,
    input_from_evidence,
)
from temnia_pipeline.chapter_llama.client import ChapterLlamaConfig, ChapterLlamaModalClient
from temnia_pipeline.chapter_llama.contracts import (
    ChapterLlamaJob,
    ChapterLlamaOutcome,
    digest,
    validate_result,
)
from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
)
from temnia_pipeline.harness import artifacts, ledger, runs
from temnia_pipeline.harness.runtime_types import RunRef
from temnia_pipeline.speech.checkpoints import ObstoreCheckpointStore

PROVIDER = "modal"
FAMILY = "llama"
MODEL = "meta-llama/Llama-3.1-8B-Instruct+chapter-llama/asr-10k"


class CandidateRequest(BaseModel):
    """Only a frozen run configuration and its own immutable evidence may dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    run: RunRef
    evidence: HarnessArtifactRef
    configuration: ChapterLlamaConfig


class CandidateResult(BaseModel):
    """A compact artifact reference; callers load hints through verified reads."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: HarnessArtifactRef
    hints: str = Field(max_length=524288)


@dataclass(frozen=True, slots=True)
class CandidateRuntime:
    """Process dependencies only; serialized requests carry immutable identities."""

    database_url: str
    store: S3Store


class ChapterLlamaActivities:
    """Register this additive activity only beside the existing harness activities."""

    def __init__(self, runtime: CandidateRuntime) -> None:
        self.runtime = runtime

    async def _reference(
        self, scope: Scope, source_id: UUID, artifact_id: UUID
    ) -> HarnessArtifactRef:
        async with db.scoped(self.runtime.database_url, scope) as connection:
            row = await (
                await connection.execute(
                    "SELECT id, kind, fingerprint, storage_key, sha256, size_bytes "
                    "FROM harness_artifact WHERE id = %s AND source_id = %s",
                    (artifact_id, source_id),
                )
            ).fetchone()
        if row is None:
            raise ledger.IdentityConflict("candidate artifact is absent from the source scope")
        return HarnessArtifactRef(
            id=row["id"],
            kind=HarnessArtifactKind(row["kind"]),
            fingerprint=row["fingerprint"],
            storageKey=row["storage_key"],
            sha256=row["sha256"],
            sizeBytes=row["size_bytes"],
        )

    async def _evidence(self, request: CandidateRequest, scope: Scope) -> HarnessEvidence:
        if request.evidence.kind != HarnessArtifactKind.evidence:
            raise ledger.IdentityConflict("Chapter-Llama input must be an evidence artifact")
        body = await artifacts.read_artifact_bytes(
            self.runtime.database_url,
            scope=scope,
            source_id=request.run.source_id,
            store=self.runtime.store,
            artifact_id=request.evidence.id,
            max_bytes=artifacts.MAX_JSON_BYTES,
        )
        if hashlib.sha256(body).hexdigest() != request.evidence.sha256:
            raise ledger.IdentityConflict(
                "candidate request evidence digest differs from stored bytes"
            )
        evidence = HarnessEvidence.model_validate_json(body)
        if evidence.sourceId != request.run.source_id:
            raise ledger.IdentityConflict("evidence belongs to another source")
        return evidence

    async def _persist_unknown(
        self,
        request: CandidateRequest,
        scope: Scope,
        attempt: ledger.Attempt,
        message: str,
    ) -> None:
        if attempt.state == "outcome_unknown":
            return
        await ledger.fail_attempt(
            self.runtime.database_url,
            scope=scope,
            source_id=request.run.source_id,
            run_id=request.run.run_id,
            operation_id=attempt.operation_id,
            attempt_id=attempt.id,
            owner_token=attempt.owner_token,
            outcome_known=False,
            actual_cost_micros=None,
            usage={},
            error_code="ChapterLlamaOutcomeUnknown",
            error_message=message,
        )

    async def _outcome(
        self,
        request: CandidateRequest,
        scope: Scope,
        attempt: ledger.Attempt,
        client: ChapterLlamaModalClient,
        job: ChapterLlamaJob,
    ) -> ChapterLlamaOutcome:
        store = ObstoreCheckpointStore(self.runtime.store)
        # A crash after spawn but before storing its handle can still recover a committed result.
        body = await store.read(job.checkpoint_key)
        if body is not None:
            outcome = ChapterLlamaOutcome.model_validate_json(body)
            if outcome.job_sha256 != job.sha256 or outcome.build != job.expected_build:
                raise ledger.IdentityConflict("remote Chapter-Llama checkpoint identity mismatch")
            return outcome
        if attempt.remote_handle is None:
            raise ledger.OutcomeUnknown(
                "Chapter-Llama dispatch has no retained handle or checkpoint"
            )
        deadline = (
            time.monotonic() + request.configuration.deployment.resources.timeout_seconds + 120
        )
        while time.monotonic() < deadline:
            activity.heartbeat({"stage": "chapter-llama", "attemptId": str(attempt.id)})
            observed = await client.status(attempt.remote_handle, job=job)
            if observed.outcome is not None:
                return observed.outcome
            if observed.status != "running":
                raise ledger.OutcomeUnknown(f"Chapter-Llama handle is {observed.status}")
            if attempt.state != "outcome_unknown":
                await ledger.heartbeat_attempt(
                    self.runtime.database_url,
                    scope=scope,
                    source_id=request.run.source_id,
                    operation_id=attempt.operation_id,
                    attempt_id=attempt.id,
                    owner_token=attempt.owner_token,
                )
            await asyncio.sleep(2)
        raise ledger.OutcomeUnknown("Chapter-Llama result exceeded its observation window")

    @activity.defn(name="generate_chapter_llama_candidate")
    async def generate(self, request: CandidateRequest) -> CandidateResult:  # noqa: C901, PLR0912, PLR0915
        """Dispatch exactly once after reservation; reuse complete physical outcomes."""
        scope = Scope(
            organizationId=request.run.scope_organization_id, userId=request.run.scope_user_id
        )
        run = await runs.get_run(
            self.runtime.database_url,
            scope=scope,
            source_id=request.run.source_id,
            run_id=request.run.run_id,
        )
        async with db.scoped(self.runtime.database_url, scope) as connection:
            row = await (
                await connection.execute(
                    "SELECT route_snapshot->'chapterLlama' AS configuration FROM harness_run "
                    "WHERE id = %s AND source_id = %s",
                    (run.id, run.source_id),
                )
            ).fetchone()
        if row is None or row["configuration"] != request.configuration.model_dump(mode="json"):
            raise ledger.IdentityConflict("Chapter-Llama configuration is not frozen on this run")
        if run.evidence_artifact_id != request.evidence.id:
            raise ledger.IdentityConflict("candidate does not consume the run's attached evidence")
        evidence = await self._evidence(request, scope)
        model_input = input_from_evidence(evidence, request.configuration)
        route = request.configuration.model_dump(mode="json")
        inputs = {
            "evidenceId": str(request.evidence.id),
            "evidenceSha256": request.evidence.sha256,
            "inputSha256": model_input.sha256,
        }
        request_hash = digest({"inputs": inputs, "configuration": route})
        acquisition = await ledger.acquire_operation(
            self.runtime.database_url,
            scope=scope,
            source_id=run.source_id,
            run_id=run.id,
            kind=ledger.OperationKind.MODEL,
            stage="chapter-llama-topics",
            inputs=inputs,
            config=route,
        )
        if acquisition.accepted:
            if acquisition.operation.result_artifact_id is None:
                raise ledger.IdentityConflict("accepted Chapter-Llama operation has no artifact")
            ref = await self._reference(
                scope, run.source_id, acquisition.operation.result_artifact_id
            )
            stored = await artifacts.read_artifact_json(
                self.runtime.database_url,
                scope=scope,
                source_id=run.source_id,
                store=self.runtime.store,
                artifact_id=ref.id,
            )
            payload = CandidatePayload.model_validate(stored)
            if payload.configuration != request.configuration:
                raise ledger.IdentityConflict(
                    "accepted candidate configuration differs from its run"
                )
            return CandidateResult(
                artifact=ref,
                hints=candidate_hints(payload, evidence, request.evidence),
            )
        if acquisition.operation.status == "failed":
            raise ValueError(
                "Chapter-Llama already failed; retained output requires explicit review"
            )
        config = request.configuration
        client = ChapterLlamaModalClient(app_name=config.app_name, environment=config.environment)
        info = activity.info()
        owner = f"{info.workflow_run_id}/{info.activity_id}/{info.attempt}"
        estimate = config.deployment.resources.reservation_micros
        attempt = await ledger.find_recoverable_attempt(
            self.runtime.database_url,
            scope=scope,
            source_id=run.source_id,
            run_id=run.id,
            operation_id=acquisition.operation.id,
            provider=PROVIDER,
            model=MODEL,
            family=FAMILY,
            route=route,
            request_hash=request_hash,
            estimated_cost_micros=estimate,
        )
        if attempt is None:
            if await client.deployment_identity() != config.deployment:
                raise ledger.IdentityConflict(
                    "deployed Chapter-Llama identity differs from frozen run config"
                )
            attempt = await ledger.reserve_attempt(
                self.runtime.database_url,
                scope=scope,
                source_id=run.source_id,
                run_id=run.id,
                operation_id=acquisition.operation.id,
                owner_token=owner,
                provider=PROVIDER,
                model=MODEL,
                family=FAMILY,
                route=route,
                request_hash=request_hash,
                estimated_cost_micros=estimate,
                dispatch_limit=run.config.maxDispatches,
            )
        job = ChapterLlamaJob(
            organization_id=scope.organizationId,
            source_id=run.source_id,
            operation_id=attempt.operation_id,
            attempt_id=attempt.id,
            expected_build=config.deployment.build,
            input=model_input,
        )
        try:
            if attempt.state == "reserved":
                won = await ledger.mark_dispatched(
                    self.runtime.database_url,
                    scope=scope,
                    source_id=run.source_id,
                    run_id=run.id,
                    operation_id=attempt.operation_id,
                    attempt_id=attempt.id,
                    owner_token=attempt.owner_token,
                    dispatch_limit=run.config.maxDispatches,
                )
                if not won:
                    raise ledger.OutcomeUnknown("another activity won Chapter-Llama dispatch")  # noqa: TRY301
                handle = await client.spawn(job)
                await ledger.attach_remote_handle(
                    self.runtime.database_url,
                    scope=scope,
                    source_id=run.source_id,
                    operation_id=attempt.operation_id,
                    attempt_id=attempt.id,
                    owner_token=attempt.owner_token,
                    remote_handle=handle,
                )
                from dataclasses import replace  # noqa: PLC0415

                attempt = replace(attempt, state="dispatching", remote_handle=handle)
            outcome = await self._outcome(request, scope, attempt, client, job)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._persist_unknown(
                    request, scope, attempt, "Chapter-Llama observation interrupted"
                )
            )
            raise
        except Exception as error:
            await self._persist_unknown(request, scope, attempt, str(error))
            raise
        if outcome.status == "outcome_unknown":
            await self._persist_unknown(
                request, scope, attempt, outcome.error_message or "remote admission uncertain"
            )
            raise ledger.OutcomeUnknown("Chapter-Llama admission has no committed result")
        payload = CandidatePayload(
            evidence_id=request.evidence.id,
            evidence_sha256=request.evidence.sha256,
            input_sha256=model_input.sha256,
            configuration=config,
            job=job,
            outcome=outcome,
        )
        if outcome.result is not None:
            validate_result(outcome.result, model_input)
        accepted = await artifacts.publish_json(
            self.runtime.database_url,
            scope=scope,
            source_id=run.source_id,
            store=self.runtime.store,
            identity=artifacts.ArtifactIdentity(
                kind="model_response",
                fingerprint=artifacts.fingerprint_for(
                    kind="model_response",
                    inputs={"attemptId": str(attempt.id), "requestHash": request_hash},
                    config={"schema": "chapter-llama-candidate/1", "deployment": route},
                ),
            ),
            content=payload.model_dump(mode="json"),
            metadata={
                "schemaVersion": "chapter-llama-candidate/1",
                "promptVersion": config.deployment.config.prompt_version,
                "operationId": str(attempt.operation_id),
                "runId": str(run.id),
            },
            dependency_ids=(request.evidence.id,),
        )
        usage = {
            "protocol": job.protocol,
            "candidateArtifactId": str(accepted.id),
            "checkpointKey": job.checkpoint_key,
            "computeSeconds": outcome.compute_seconds,
            "estimatedComputeMicros": config.deployment.resources.estimated_micros(
                outcome.compute_seconds
            ),
            "inputTokens": outcome.result.input_tokens
            if outcome.result
            else outcome.rejected_input_tokens,
            "outputTokens": outcome.result.output_tokens
            if outcome.result
            else outcome.rejected_output_tokens,
            "invoiceStatus": "unavailable",
            "resources": config.deployment.resources.model_dump(mode="json"),
        }
        if outcome.status != "ok":
            await ledger.fail_attempt(
                self.runtime.database_url,
                scope=scope,
                source_id=run.source_id,
                run_id=run.id,
                operation_id=attempt.operation_id,
                attempt_id=attempt.id,
                owner_token=attempt.owner_token,
                outcome_known=True,
                reconcile_unknown=True,
                actual_cost_micros=None,
                usage=usage,
                error_code=outcome.error_code,
                error_message=outcome.error_message or "Chapter-Llama computation failed",
            )
            raise ValueError(outcome.error_message or "Chapter-Llama computation failed")
        await ledger.complete_attempt(
            self.runtime.database_url,
            scope=scope,
            source_id=run.source_id,
            run_id=run.id,
            operation_id=attempt.operation_id,
            attempt_id=attempt.id,
            owner_token=attempt.owner_token,
            result_artifact_id=accepted.id,
            usage=usage,
            actual_cost_micros=None,
        )
        return CandidateResult(
            artifact=await self._reference(scope, run.source_id, accepted.id),
            hints=candidate_hints(payload, evidence, request.evidence),
        )
