"""Qualification binds all production native shapes and rejects stale or altered evidence."""

# The transport uses invented text and zero external requests, not live qualification.
# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import TYPE_CHECKING

from qualification_fixtures import (
    API_KEY,
    _candidate_file,
    _outputs_v3,
    _paths,
    _qualified,
    _request_transport,
    _three_candidate_lookup_transport,
)
from temnia_pipeline.harness.qualification import (
    QualificationLimits,
    run_qualification,
)
from temnia_pipeline.harness.qualification_topic_selection import (
    TOPIC_SELECTION_V3_SCHEMAS,
    TOPIC_SELECTION_V3_STAGES,
)
from temnia_pipeline.harness.topic_selection import (
    SELECTION_AUTHOR_PROMPT_V3,
)

if TYPE_CHECKING:
    from pathlib import Path


async def test_v3_qualification_runs_all_five_exact_contracts(tmp_path: Path) -> None:
    _, report, requests = await _qualified(tmp_path)
    assert len(requests) == 15
    assert {call["stage"] for call in report["calls"]} == set(TOPIC_SELECTION_V3_STAGES)
    assert all(
        call["schemaVersion"] == TOPIC_SELECTION_V3_SCHEMAS[call["stage"]]
        for call in report["calls"]
    )


async def test_v3_qualification_can_refresh_only_a_changed_stage(tmp_path: Path) -> None:
    request_transport, requests = _request_transport([_outputs_v3()[1]])
    lookup_transport, _ = _three_candidate_lookup_transport(stages_per_candidate=1)
    paths = _paths(tmp_path)
    report = await run_qualification(
        candidate_path=_candidate_file(tmp_path, count=1),
        api_key=API_KEY,
        journal_path=paths["journal_path"],
        receipts_path=paths["receipts_path"],
        report_path=paths["report_path"],
        limits=QualificationLimits(
            suite="topic-selection-v3",
            stages=("topic_author",),
            max_exposure_micros=100_000,
            max_dispatches=1,
            max_output_tokens=256,
            lookup_wait_seconds=0,
        ),
        request_transport=request_transport,
        lookup_transport=lookup_transport,
    )
    assert report["passed"] is True
    assert len(requests) == 1
    assert [(call["stage"], call["promptVersion"]) for call in report["calls"]] == [
        ("topic_author", SELECTION_AUTHOR_PROMPT_V3)
    ]
