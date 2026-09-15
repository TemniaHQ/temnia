"""Exercise the pinned plugin on the combined chapter/topic worker registration."""

# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from temporalio import activity
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from temnia_pipeline.harness.models import harness_pydantic_ai_plugin
from temnia_pipeline.harness.topic_patch_review import TopicEditorialPatchWorkflow
from temnia_pipeline.harness.topic_review import TopicReviewWorkflow
from temnia_pipeline.harness.topic_selection_workflow import (
    TopicEditorialWorkWorkflow,
    TopicSelectionWorkflow,
    TopicSelectionWorkflowV4,
    TopicSelectionWorkflowV5,
    TopicSelectionWorkflowV6,
    TopicSelectionWorkflowV7,
)
from temnia_pipeline.harness.topic_windows_workflow import TopicSelectionWorkflowV8
from temnia_pipeline.harness.topic_workflow import TopicRunWorkflow

if TYPE_CHECKING:
    from collections.abc import Callable

    from temporalio.worker import WorkerConfig


def test_combined_worker_registers_each_durable_model_activity_once() -> None:
    config = harness_pydantic_ai_plugin().configure_worker(
        cast(
            "WorkerConfig",
            {
                "activities": [],
                "workflow_runner": SandboxedWorkflowRunner(),
                "workflows": [
                    TopicRunWorkflow,
                    TopicReviewWorkflow,
                    TopicEditorialWorkWorkflow,
                    TopicSelectionWorkflow,
                    TopicSelectionWorkflowV4,
                    TopicSelectionWorkflowV5,
                    TopicSelectionWorkflowV6,
                    TopicSelectionWorkflowV7,
                    TopicSelectionWorkflowV8,
                    TopicEditorialPatchWorkflow,
                ],
            },
        )
    )
    assert "activities" in config
    registered = cast("list[Callable[..., object]]", config["activities"])
    names = [
        activity._Definition.must_from_callable(item).name  # pyright: ignore[reportUnknownMemberType]
        for item in registered
    ]
    assert len(names) == len(set(names))
    assert "agent__topic_selection_author_v3__model_request" in names
    assert "agent__topic_opportunity_inventory_v3__model_request" in names
    assert "agent__topic_selection_author_v3__model_request" in names
    assert "agent__topic_selection_cold_v3__model_request" in names
    assert "agent__topic_selection_source_v4__model_request" in names
    assert "agent__topic_selection_patch_v3__model_request" in names
    assert "agent__topic_opportunity_inventory_v4__model_request" in names
    assert "agent__topic_selection_author_v4__model_request" in names
    assert "agent__topic_selection_cold_v4__model_request" in names
    assert "agent__topic_selection_source_v5__model_request" in names
    assert "agent__topic_selection_patch_v4__model_request" in names
    assert "agent__topic_opportunity_inventory_v5__model_request" in names
    assert "agent__topic_selection_author_v5__model_request" in names
    assert "agent__topic_selection_cold_v5__model_request" in names
    assert "agent__topic_selection_source_v6__model_request" in names
    assert "agent__topic_selection_patch_v5__model_request" in names
    assert "agent__topic_opportunity_inventory_v6__model_request" in names
    assert "agent__topic_selection_author_v6__model_request" in names
    assert "agent__topic_selection_cold_v6__model_request" in names
    assert "agent__topic_selection_source_v7__model_request" in names
    assert "agent__topic_selection_patch_v6__model_request" in names
    assert "agent__topic_opportunity_inventory_v7__model_request" in names
    assert "agent__topic_selection_author_v7__model_request" in names
    assert "agent__topic_selection_cold_v7__model_request" in names
    assert "agent__topic_selection_source_v8__model_request" in names
    assert "agent__topic_selection_patch_v7__model_request" in names
