"""Bounded windows: every planned decision fits one request, and assembly tolerates gaps."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from temnia_pipeline.contracts import (
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    TopicSelectionDraft,
    TopicSourceIndex,
)
from temnia_pipeline.harness.routes import RouteEligibility, RouteEntry, RoutePrices
from temnia_pipeline.harness.source_index import build_topic_source_index, read_topic_source
from temnia_pipeline.harness.topic_author_packaging import build_author_plan
from temnia_pipeline.harness.topic_inventory import build_inventory_plan
from temnia_pipeline.harness.topic_selection import make_rubric
from temnia_pipeline.harness.topic_source_review import build_source_review_plan
from temnia_pipeline.harness.topic_windows import (
    MAX_PROMPT_CHARACTERS,
    CoverageGap,
    assemble_author_v8,
    assemble_inventory_v8,
    author_window_prompt,
    inventory_window_prompt,
    neighbour_windows,
    node_window,
    project_run,
    read_sentence_ids,
    review_window_prompt,
    validate_claims,
)
from temnia_pipeline.harness.validators import HarnessValidationError

sys.path.insert(0, str(Path(__file__).parent))
from test_topic_source_index import (  # pyright: ignore[reportPrivateUsage]
    FixtureEncoder,
    _long_evidence,
)

INDEX_SHA = "c" * 64
FOUR_HOURS = 2_400


def _index(sentence_count: int = FOUR_HOURS) -> tuple[HarnessEvidence, TopicSourceIndex]:
    evidence = _long_evidence(sentence_count)
    index = build_topic_source_index(
        evidence,
        evidence_sha256="e" * 64,
        encoder=FixtureEncoder(),
        embedding_revision="fixture",
        embedding_model="test/encoder",
    )
    return evidence, index


def _route(family: str, *, input_price: int = 750_000, output_price: int = 3_750_000) -> RouteEntry:
    return RouteEntry(
        id=f"fixture-{family}",
        gateway_model=f"{family}/model",
        family=family,
        provider="synthetic",
        open_weight=True,
        context_tokens=1_000_000,
        max_output_tokens=65_536,
        eligibility=RouteEligibility(
            zero_data_retention=True,
            strict_json_schema=True,
            probe_artifact_sha256="a" * 64,
            probed_at=date(2026, 9, 11),
        ),
        prices=RoutePrices(input=input_price, output=output_price),
    )


def _ref(suffix: str) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        id=UUID(f"0192e8a0-0000-7000-8000-00000000{suffix:>04}"),
        kind=HarnessArtifactKind.checks,
        sha256=f"{int(suffix):064x}",
        fingerprint=f"{int(suffix):064x}",
        sizeBytes=1,
        storageKey=f"checks/{suffix}",
    )


def test_every_planned_inventory_prompt_fits_one_request() -> None:
    """The plan-time fit proof: no section of a four-hour source needs a second request."""
    _, index = _index()
    plan = build_inventory_plan(index, index_sha256=INDEX_SHA)
    rubric = make_rubric("Preserve every original sentence.")
    assert len(plan.sections) == 10
    sizes: list[int] = []
    for section in plan.sections:
        prompt, delivered = inventory_window_prompt(index, rubric, section, index_sha256=INDEX_SHA)
        sizes.append(len(prompt))
        assert len(prompt) <= MAX_PROMPT_CHARACTERS
        window = node_window(index, section.sectionId)
        assert window.sentence_ids <= delivered
        assert len(window.sentences) in {256, 96}
    # 256 sentences of about 100 characters plus row overhead, context and the rubric.
    assert max(sizes) < 64_000, sizes


def test_neighbour_context_is_bounded_and_absent_at_the_edges() -> None:
    _, index = _index(400)
    first = node_window(index, "section-0001")
    before, after = neighbour_windows(index, first.firstSentenceId, first.lastSentenceId)
    assert before is None
    assert after is not None
    assert after.characters <= 4_000 + 200
    last_section = f"section-{(len(index.sentences) // 256) + 1:04d}"
    last = node_window(index, last_section)
    before, after = neighbour_windows(index, last.firstSentenceId, last.lastSentenceId)
    assert before is not None
    assert after is None


def test_review_and_author_prompts_fit_with_inline_candidates() -> None:
    evidence, index = _index(800)
    rubric = make_rubric("")
    sentences = index.sentences
    draft = TopicSelectionDraft.model_validate(
        {
            "proposal": {
                "version": 1,
                "summary": "fixture",
                "candidates": [
                    {
                        "id": "section-0001:author-0001:candidate:one",
                        "title": "One",
                        "purpose": "p",
                        "reason": "r",
                        "firstSentenceId": sentences[10].id,
                        "lastSentenceId": sentences[300].id,
                        "coreSpans": [
                            {
                                "firstSentenceId": sentences[10].id,
                                "lastSentenceId": sentences[300].id,
                            }
                        ],
                        "completionSpans": [
                            {
                                "firstSentenceId": sentences[300].id,
                                "lastSentenceId": sentences[300].id,
                            }
                        ],
                        "requiredContextSpans": [],
                        "meaningChangingFollowups": [],
                    }
                ],
            },
            "opportunities": [
                {
                    "id": "section-0001:one",
                    "viewerPurpose": "p",
                    "coreSpans": [
                        {"firstSentenceId": sentences[10].id, "lastSentenceId": sentences[300].id}
                    ],
                    "completionSpans": [],
                    "requiredContextSpans": [],
                    "meaningChangingFollowups": [],
                    "valueEvidenceSpans": [
                        {"firstSentenceId": sentences[10].id, "lastSentenceId": sentences[300].id}
                    ],
                    "candidateIds": ["section-0001:author-0001:candidate:one"],
                    "disposition": "proposed",
                    "dispositionReason": "fixture",
                }
            ],
        }
    )
    plan = build_source_review_plan(
        evidence,
        index,
        draft,
        index_sha256=INDEX_SHA,
        selection_sha256="d" * 64,
        paged_context=True,
    )
    kinds = {item.kind.value for item in plan.workItems}
    assert kinds == {"local", "omission"}
    for item in plan.workItems:
        prompt, delivered = review_window_prompt(index, rubric, draft, item, index_sha256=INDEX_SHA)
        assert len(prompt) <= MAX_PROMPT_CHARACTERS
        if item.kind.value == "local" and item.candidateIds:
            # The candidate crosses the section edge; its whole speech is still inline.
            assert {sentences[offset].id for offset in range(10, 301)} <= delivered
    inventory_plan = build_inventory_plan(index, index_sha256=INDEX_SHA)
    inventory = TopicSelectionDraft.model_validate(
        {
            "proposal": {"version": 1, "summary": "inventory", "candidates": []},
            "opportunities": [
                {
                    **draft.opportunities[0].model_dump(mode="json"),
                    "candidateIds": [],
                    "disposition": "needs_evidence",
                }
            ],
        }
    )
    author_plan = build_author_plan(
        inventory_plan, inventory, index_sha256=INDEX_SHA, inventory_sha256="f" * 64
    )
    assert len(author_plan.workItems) == 1
    prompt, delivered = author_window_prompt(
        index, rubric, author_plan.workItems[0], inventory, index_sha256=INDEX_SHA
    )
    assert len(prompt) <= MAX_PROMPT_CHARACTERS
    assert node_window(index, "section-0001").sentence_ids <= delivered


def test_claims_accept_inline_and_read_sentences_and_name_missing_ones() -> None:
    evidence, index = _index(300)
    page = read_topic_source(
        index,
        index_sha256=INDEX_SHA,
        first_sentence_id=index.sentences[250].id,
        last_sentence_id=index.sentences[260].id,
        limit=80,
    )
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name="read_source", args={}, tool_call_id="c1")]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="read_source", content=page, tool_call_id="c1")]
        ),
        ModelResponse(parts=[TextPart("{}")]),
    ]
    read = read_sentence_ids(messages, index_sha256=INDEX_SHA)
    assert read == {sentence.id for sentence in index.sentences[250:261]}
    inline = node_window(index, "section-0001").sentence_ids
    draft = TopicSelectionDraft.model_validate(
        {
            "proposal": {"version": 1, "summary": "s", "candidates": []},
            "opportunities": [
                {
                    "id": "section-0001:x",
                    "viewerPurpose": "p",
                    "coreSpans": [
                        {
                            "firstSentenceId": index.sentences[5].id,
                            "lastSentenceId": index.sentences[258].id,
                        }
                    ],
                    "completionSpans": [],
                    "requiredContextSpans": [],
                    "meaningChangingFollowups": [],
                    "valueEvidenceSpans": [
                        {
                            "firstSentenceId": index.sentences[5].id,
                            "lastSentenceId": index.sentences[8].id,
                        }
                    ],
                    "candidateIds": [],
                    "disposition": "needs_evidence",
                    "dispositionReason": "r",
                }
            ],
        }
    )
    validate_claims(evidence, delivered=inline | read, outputs=[draft])
    with pytest.raises(HarnessValidationError, match="neither supplied inline nor read"):
        validate_claims(evidence, delivered=inline, outputs=[draft])


def test_inventory_and_author_assembly_record_gaps_instead_of_failing() -> None:
    evidence, index = _index(600)
    plan = build_inventory_plan(index, index_sha256=INDEX_SHA)
    section_ids = [section.sectionId for section in plan.sections]
    assert len(section_ids) == 3
    empty = TopicSelectionDraft.model_validate(
        {"proposal": {"version": 1, "summary": "empty", "candidates": []}, "opportunities": []}
    )
    first = index.sentences[0].id
    third = index.sentences[3].id
    one = TopicSelectionDraft.model_validate(
        {
            "proposal": {"version": 1, "summary": "one", "candidates": []},
            "opportunities": [
                {
                    "id": "section-0001:topic",
                    "viewerPurpose": "p",
                    "coreSpans": [{"firstSentenceId": first, "lastSentenceId": third}],
                    "completionSpans": [],
                    "requiredContextSpans": [],
                    "meaningChangingFollowups": [],
                    "valueEvidenceSpans": [{"firstSentenceId": first, "lastSentenceId": third}],
                    "candidateIds": [],
                    "disposition": "needs_evidence",
                    "dispositionReason": "r",
                }
            ],
        }
    )
    gap = CoverageGap(
        kind="inventory", itemId=section_ids[1], reason="two corrections exhausted", stage="s"
    )
    manifest = assemble_inventory_v8(
        evidence,
        plan,
        index_sha256=INDEX_SHA,
        plan_sha256="b" * 64,
        shards={section_ids[0]: (one, _ref("1")), section_ids[2]: (empty, _ref("2"))},
        gaps=[gap],
    )
    assert not manifest.complete
    assert [item.id for item in manifest.inventory.opportunities] == ["section-0001:topic"]
    assert "two corrections exhausted" in manifest.inventory.proposal.summary
    with pytest.raises(HarnessValidationError, match="neither a shard nor a gap"):
        assemble_inventory_v8(
            evidence,
            plan,
            index_sha256=INDEX_SHA,
            plan_sha256="b" * 64,
            shards={section_ids[0]: (one, _ref("1"))},
            gaps=[gap],
        )
    author_plan = build_author_plan(
        plan, manifest.inventory, index_sha256=INDEX_SHA, inventory_sha256="a" * 64
    )
    item = author_plan.workItems[0]
    authored = assemble_author_v8(
        evidence,
        manifest.inventory,
        author_plan,
        index_sha256=INDEX_SHA,
        inventory_sha256="a" * 64,
        plan_sha256="d" * 64,
        shards={},
        gaps=[CoverageGap(kind="author", itemId=item.workItemId, reason="no answer", stage="s")],
    )
    assert not authored.complete
    assert authored.selection.proposal.candidates == []
    opportunity = authored.selection.opportunities[0]
    assert opportunity.disposition.value == "needs_evidence"
    assert "Coverage gap" in opportunity.dispositionReason


def test_projection_scales_with_sections_and_names_the_allowance() -> None:
    _, index = _index()
    plan = build_inventory_plan(index, index_sha256=INDEX_SHA)
    projection = project_run(
        index,
        make_rubric(""),
        plan,
        index_sha256=INDEX_SHA,
        author_route=_route("kimi", input_price=3_300_000, output_price=16_500_000),
        verifier_route=_route("google"),
        budget_micros=20_000_000,
        max_repairs=3,
    )
    assert projection.sectionCount == 10
    assert projection.regionCount == 75
    by_kind = {stage.kind: stage for stage in projection.stages}
    assert by_kind["inventory"].calls == 10
    assert by_kind["review"].calls >= 75
    assert projection.projectedCalls == sum(stage.calls for stage in projection.stages)
    assert 0 < projection.projectedCostMicros < 100_000_000


def test_repair_assembly_applies_admitted_components_and_names_the_rest() -> None:
    from temnia_pipeline.harness.topic_repair import admit_repair_shard  # noqa: PLC0415
    from temnia_pipeline.harness.topic_selection import content_hash  # noqa: PLC0415
    from temnia_pipeline.harness.topic_windows import assemble_repair_v8  # noqa: PLC0415
    from test_topic_repair import _garden_patch, _index_case, _reference  # noqa: PLC0415

    evidence, _, record, assessment, plan = _index_case()
    item = plan.workItems[0]
    patch = _garden_patch(record, item)
    response = _reference(patch, "response", "model_response")
    claims = _reference(patch, "claims")
    shard = admit_repair_shard(
        evidence,
        record,
        assessment,
        plan,
        item,
        patch,
        index_sha256="c" * 64,
        assessment_sha256=content_hash(assessment),
        response_artifact=response,
        inspection_artifact=claims,
        author_family="synthetic-author",
    )
    manifest = assemble_repair_v8(
        evidence,
        record,
        assessment,
        plan,
        shards={
            item.workItemId: (shard.patch, "synthetic-author", _reference(shard, "shard"), response)
        },
        gaps=[],
    )
    assert manifest.complete
    assert manifest.aggregatePatch == patch
    assert "Regular watering and garden roots" in {
        candidate.title for candidate in manifest.draft.proposal.candidates
    }
    gap = CoverageGap(kind="repair", itemId=item.workItemId, reason="no answer", stage="s")
    with pytest.raises(HarnessValidationError, match="no repair component was admitted"):
        assemble_repair_v8(evidence, record, assessment, plan, shards={}, gaps=[gap])
