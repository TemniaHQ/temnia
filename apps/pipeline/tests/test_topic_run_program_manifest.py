"""Every topic run freezes the prompt-template and native-schema bytes it actually ran.

This lives outside `test_harness_runs.py` on purpose: the programme manifest imports the
qualification prompt fixtures, and that graph reaches `urllib3`, whose exception classes the
Temporal workflow sandbox refuses. Importing it before the sandbox tests in that module
would break them.
"""

from __future__ import annotations

import pytest

from temnia_pipeline import db
from temnia_pipeline.evals.topics import digest
from temnia_pipeline.harness.runs import start_or_refetch_run
from temnia_pipeline.harness.topic_program import current_program
from test_harness_runs import pipeline_url, ready_source, settings, snapshot, start_request


def test_v4_program_manifest_names_the_bounded_inventory_stage() -> None:
    manifest = current_program("standalone-topics/4")

    assert manifest.policy == "standalone-topics/4"
    assert set(manifest.stages) == {
        "topic_inventory_shard",
        "topic_author",
        "topic_cold",
        "topic_source",
        "topic_patch",
    }
    assert (
        manifest.stages["topic_inventory_shard"].prompt_version
        == "topic-opportunity-inventory-shard/1"
    )


def test_v5_program_manifest_names_the_bounded_author_prompt() -> None:
    manifest = current_program("standalone-topics/5")

    assert manifest.policy == "standalone-topics/5"
    assert set(manifest.stages) == {
        "topic_inventory_shard",
        "topic_author",
        "topic_cold",
        "topic_source",
        "topic_patch",
    }
    assert manifest.stages["topic_author"].prompt_version == "topic-selection-author-shard/1"


def test_v6_program_manifest_names_the_bounded_source_review_prompt() -> None:
    manifest = current_program("standalone-topics/6")

    assert manifest.policy == "standalone-topics/6"
    assert set(manifest.stages) == {
        "topic_inventory_shard",
        "topic_author",
        "topic_cold",
        "topic_source",
        "topic_patch",
    }
    assert manifest.stages["topic_source"].prompt_version == "topic-selection-source-shard/1"


def test_v7_program_manifest_names_the_bounded_repair_prompt() -> None:
    manifest = current_program("standalone-topics/7")

    assert manifest.policy == "standalone-topics/7"
    assert set(manifest.stages) == {
        "topic_inventory_shard",
        "topic_author",
        "topic_cold",
        "topic_source",
        "topic_patch",
    }
    assert manifest.stages["topic_patch"].prompt_version == "topic-selection-patch-component/1"


@pytest.mark.parametrize("policy", ["standalone-topics/3"])
async def test_web_started_topic_run_freezes_its_own_program_manifest(policy: str) -> None:
    """A staging bundle binds the same bytes an experiment bundle binds, at no model cost."""
    url = pipeline_url()
    value = snapshot()
    source_id = await ready_source(url)
    start = start_request(source_id, value).model_copy(update={"editorial_policy": policy})
    assert start.evaluation_program is None
    expected = current_program("standalone-topics/3").model_dump(mode="json", by_alias=True)
    try:
        created = await start_or_refetch_run(
            url, start=start, settings=settings(value), route_snapshot=value
        )
        assert created.created is True
        assert created.run.evaluation_program == expected
        assert created.run.evaluation_program_sha256 == digest(expected)
        replayed = await start_or_refetch_run(
            url, start=start, settings=settings(value), route_snapshot=value
        )
        assert replayed.created is False
        assert replayed.run.evaluation_program == expected
    finally:
        await db.close_pool()
