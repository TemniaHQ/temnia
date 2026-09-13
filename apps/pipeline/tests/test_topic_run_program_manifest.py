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
