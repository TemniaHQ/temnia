"""Real storage lineage fences reject unrelated preserved inputs before compilation."""

# ruff: noqa: SLF001, F811, F401
# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from temnia_pipeline.contracts import ChapterProposal
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.runtime_types import CompileProposalRequest
from temnia_pipeline.harness.validators import HarnessValidationError
from test_harness_editorial_integration import (
    editorial_integration,  # pyright: ignore[reportUnusedImport]
)
from test_harness_runs import SEEDED

if TYPE_CHECKING:
    from test_harness_editorial_integration import EditorialIntegration


@pytest.mark.parametrize(
    "corruption",
    ["reference_hash", "prior_run", "proposal_link", "dependency_link", "body_evidence"],
)
async def test_preservation_activity_checks_accepted_prior_lineage(
    editorial_integration: EditorialIntegration, corruption: str
) -> None:
    case = editorial_integration
    context = case.context
    owner = case.owner
    db_url = owner.ctx.settings.database_url
    proposal_raw = await artifacts.read_artifact_json(
        db_url,
        scope=SEEDED,
        source_id=context.run.source_id,
        store=owner.ctx.store,
        artifact_id=context.compiled.proposal_artifact.id,
    )
    prior_ref = context.compiled.edit_artifact
    if corruption == "reference_hash":
        prior_ref = prior_ref.model_copy(update={"sha256": "f" * 64})
    else:
        original = await artifacts._artifact_for_read(
            db_url, scope=SEEDED, source_id=context.run.source_id, artifact_id=prior_ref.id
        )
        body = await artifacts.read_artifact_json(
            db_url,
            scope=SEEDED,
            source_id=context.run.source_id,
            store=owner.ctx.store,
            artifact_id=prior_ref.id,
        )
        assert isinstance(body, dict)
        metadata = dict(original.metadata)
        deps = original.dependency_ids
        if corruption == "prior_run":
            metadata["runId"] = str(uuid4())
        elif corruption == "proposal_link":
            metadata["proposalArtifactId"] = str(uuid4())
        elif corruption == "dependency_link":
            deps = tuple(i for i in deps if i != context.compiled.proposal_artifact.id)
        else:
            body["evidenceSha256"] = "f" * 64
        changed = await artifacts.publish_json(
            db_url,
            scope=SEEDED,
            source_id=context.run.source_id,
            store=owner.ctx.store,
            identity=artifacts.ArtifactIdentity(
                kind="edit",
                fingerprint=artifacts.fingerprint_for(
                    kind="edit", inputs={"fixture": str(uuid4())}, config={}
                ),
            ),
            content=cast("dict[str, object]", body),
            metadata=metadata,
            dependency_ids=deps,
        )
        prior_ref = owner._artifact_ref(changed)
    request = CompileProposalRequest(
        run=context.run,
        evidence=context.evidence,
        proposal=ChapterProposal.model_validate(proposal_raw),
        generator_family="fixture",
        model_stage="unpaid-invalid-lineage-test",
        prior_proposal_artifact=context.compiled.proposal_artifact,
        prior_edit_artifact=prior_ref,
    )
    if corruption == "body_evidence":
        result = await owner.compile_chapter_proposal(request)
        assert result.refusal == "edit names a different evidence artifact hash"
    else:
        with pytest.raises(HarnessValidationError, match="prior"):
            await owner.compile_chapter_proposal(request)
    assert case.calls == []
