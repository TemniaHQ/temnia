"""The image fixture traverses every native v2 stage on its retained speech grid."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from temnia_pipeline.contracts import (
    TopicPortfolioReview,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatch,
    TranscriptV1,
)
from temnia_pipeline.harness import topic_selection_workflow as module
from temnia_pipeline.harness.topic_compiler import augment_topic_evidence
from temnia_pipeline.harness.topic_selection_workflow import TopicSelectionWorkflow
from test_harness_compiler import _evidence
from test_harness_hierarchy_workflow import SOURCE_ID
from test_topic_selection_workflow import Program, draft

if TYPE_CHECKING:
    import pytest
    from pydantic import BaseModel

    from temnia_pipeline.harness.models import HarnessModelDeps

import test_topic_selection_workflow as doubles

FIXTURES = Path(__file__).parent / "fixtures"


class RecordedAgent:
    """Use the real prepared fixture payload, preserving the native output contracts."""

    def __init__(self, output_type: type[BaseModel]) -> None:
        self.output_type = output_type
        self.calls: list[HarnessModelDeps] = []

    async def run(self, _prompt: str, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        deps = kwargs["deps"]
        self.calls.append(deps)
        assert deps.synthetic_payload["synthetic"] is True
        output = self.output_type.model_validate(deps.synthetic_payload["output"])
        return SimpleNamespace(output=output)


async def test_recorded_selection_fixture_recovers_one_discussion_through_all_four_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = TranscriptV1.model_validate_json(
        (FIXTURES / "substrate/speech-40s.transcript.json").read_bytes()
    )
    grid = json.loads((FIXTURES / "substrate/speech-40s.grid.json").read_bytes())
    evidence = augment_topic_evidence(
        _evidence(
            transcript,
            [(sentence["startWord"], sentence["endWord"]) for sentence in grid["sentences"]],
        ).model_copy(update={"sourceId": SOURCE_ID})
    )
    monkeypatch.setattr(doubles, "EVIDENCE", evidence)
    run = Program(monkeypatch, initial=draft(selected=False), sources=[], patches=[])
    outputs = json.loads((FIXTURES / "harness/chapter.synthetic.json").read_bytes())["outputs"]

    def payload(stage: str) -> dict[str, object]:
        return cast("dict[str, object]", outputs[stage])

    monkeypatch.setattr(run.activities.owner, "_recorded_output", payload)
    agents = [
        ("topic_selection_author_v2", TopicSelectionDraft),
        ("topic_selection_cold_v2", TopicSelectionColdReview),
        ("topic_selection_source_v2", TopicPortfolioReview),
        ("topic_selection_patch_v2", TopicSelectionPatch),
    ]
    registered: list[RecordedAgent] = []
    for name, output_type in agents:
        agent = RecordedAgent(output_type)
        monkeypatch.setattr(module, name, agent)
        registered.append(agent)
    result = await TopicSelectionWorkflow().program(run.request)
    assert [len(agent.calls) for agent in registered] == [1, 1, 2, 1]
    assert result.revision == 1
    assert run.render_count == 1
    assert run.compiled is not None
    assert run.compiled.edit.videos[0].candidate.id == "timestamp-search"
    assert run.final_context is not None
    assessment = await run.activities.assessment(run.final_context)
    assert assessment is not None
    assert str(assessment.executionStatus) == "needs_review"
    assert assessment.portfolioReview is not None
    assert str(assessment.portfolioReview.selection[0].disposition) == "unresolved"
    assert str(assessment.coldReviews[0].value.deliveredValue.status) == "unknown"
    assert not assessment.findings
