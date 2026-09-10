"""Exercise the pinned plugin on the combined chapter/topic worker registration."""

# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from temporalio import activity
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from temnia_pipeline.harness.models import harness_pydantic_ai_plugin
from temnia_pipeline.harness.topic_review import TopicReviewWorkflow
from temnia_pipeline.harness.topic_workflow import TopicRunWorkflow
from temnia_pipeline.harness.workflows import ChapterReviewWorkflow, ChapterRunWorkflow

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
                    ChapterRunWorkflow,
                    ChapterReviewWorkflow,
                    TopicRunWorkflow,
                    TopicReviewWorkflow,
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
    assert "agent__chapter_propose_v1__model_request" in names
    assert "agent__topic_propose_v1__model_request" in names
    assert "agent__topic_cold_review_v1__model_request" in names
    assert "agent__topic_source_review_v1__model_request" in names
