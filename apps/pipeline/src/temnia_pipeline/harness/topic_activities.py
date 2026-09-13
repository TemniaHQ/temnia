"""Immutable topic plans, grounded judgments and independent execution artifacts."""

# Activity DTOs need runtime annotations; adjacent refusal messages are intentional.
# ruff: noqa: EM101, TRY003, TC001, SLF001
# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from pydantic_ai import TextPart
from temporalio import activity

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    HarnessArtifactRef,
    HarnessEvidence,
    Scope,
    Status2,
    TopicAssessment,
    TopicAssessmentCandidate,
    TopicCompiledVideo,
    TopicEditSpec,
    TopicProposal,
)
from temnia_pipeline.harness import artifacts, runs
from temnia_pipeline.harness.cassettes import MODEL_RESPONSE_ADAPTER
from temnia_pipeline.harness.editorial_policy import TOPIC_POLICY, is_topic_policy
from temnia_pipeline.harness.models import (
    TOPIC_ID_NORMALIZATION_VERSION,
    normalize_initial_topic_response,
)
from temnia_pipeline.harness.routes import estimate_cost
from temnia_pipeline.harness.runtime_types import RunSnapshot
from temnia_pipeline.harness.topic_compiler import (
    TOPIC_COMPILER_VERSION,
    compile_topics,
    topic_boundary_issues,
    validate_topic_edit,
    validate_topic_proposal,
)
from temnia_pipeline.harness.topic_editorial import (
    TOPIC_PROGRAM,
    TOPIC_PROMPT,
    cold_prompt,
    cold_review_key,
    criteria,
    editorial_routes,
    ground_review,
    plan_prompt,
    source_prompt,
    validate_assessment,
    validate_preserved_candidates,
)
from temnia_pipeline.harness.topic_runtime import (
    SaveTopicAssessment,
    SaveTopicProposal,
    TopicAssessmentResult,
    TopicCallPlan,
    TopicCompilation,
    TopicContext,
    TopicProposalResult,
    TopicProposalValidation,
)
from temnia_pipeline.harness.validators import HarnessValidationError, validate_evidence

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import BaseModel

    from temnia_pipeline.harness.activities import HarnessActivities


class TopicActivities:
    """Ground and persist the new editorial program beside historical chapters."""

    def __init__(self, owner: HarnessActivities) -> None:
        self.owner = owner

    @staticmethod
    def scope(context: TopicContext) -> Scope:
        """Recover the scoped run identity from an activity input."""
        return Scope(
            organizationId=context.run.scope_organization_id, userId=context.run.scope_user_id
        )

    async def read(self, context: TopicContext, ref: HarnessArtifactRef) -> object:
        """Read and hash-check an exact scoped artifact reference."""
        accepted = await artifacts._artifact_for_read(
            self.owner.ctx.settings.database_url,
            scope=self.scope(context),
            source_id=context.run.source_id,
            artifact_id=ref.id,
        )
        if self.owner._artifact_ref(accepted) != ref:
            raise HarnessValidationError("topic reference differs from accepted artifact identity")
        raw = await artifacts.read_artifact_json(
            self.owner.ctx.settings.database_url,
            scope=self.scope(context),
            source_id=context.run.source_id,
            store=self.owner.ctx.store,
            artifact_id=ref.id,
        )
        if hashlib.sha256(artifacts.canonical_json(raw)).hexdigest() != ref.sha256:
            raise HarnessValidationError("topic input differs from its retained hash")
        return raw

    async def load_evidence(self, context: TopicContext) -> tuple[RunSnapshot, HarnessEvidence]:
        """Share immutable media evidence across topic generations, never authoring semantics."""
        self.owner._require_enabled()
        run = await runs.get_run(
            self.owner.ctx.settings.database_url,
            scope=self.scope(context),
            source_id=context.run.source_id,
            run_id=context.run.run_id,
        )
        if (
            not is_topic_policy(run.editorial_policy)
            or run.evidence_artifact_id != context.evidence.id
        ):
            raise HarnessValidationError("topic media requires the run's accepted source evidence")
        evidence = HarnessEvidence.model_validate(await self.read(context, context.evidence))
        if evidence.sourceId != run.source_id:
            raise HarnessValidationError("topic evidence belongs to another source")
        return run, evidence

    async def publish(  # noqa: PLR0913
        self,
        context: TopicContext,
        *,
        kind: str,
        format_name: str,
        content: BaseModel,
        dependencies: Sequence[HarnessArtifactRef],
        metadata: dict[str, Any] | None = None,
    ) -> HarnessArtifactRef:
        """Publish a content-addressed result with immutable input dependencies."""
        fingerprint = artifacts.fingerprint_for(
            kind=kind,
            inputs={
                "runId": str(context.run.run_id),
                "contentSha256": hashlib.sha256(
                    artifacts.canonical_json(content.model_dump(mode="json"))
                ).hexdigest(),
                "dependencies": [{"id": str(ref.id), "sha256": ref.sha256} for ref in dependencies],
            },
            config={"format": format_name},
        )
        accepted = await artifacts.publish_json(
            self.owner.ctx.settings.database_url,
            scope=self.scope(context),
            source_id=context.run.source_id,
            store=self.owner.ctx.store,
            identity=artifacts.ArtifactIdentity(kind=kind, fingerprint=fingerprint),
            content=content.model_dump(mode="json"),
            metadata={"format": format_name, "runId": str(context.run.run_id), **(metadata or {})},
            dependency_ids=tuple(dict.fromkeys(ref.id for ref in dependencies)),
        )
        return self.owner._artifact_ref(accepted)
