"""Exact production topic request qualification, distinct from editorial acceptance."""

# Refusal messages and explicit fixture construction make this evidence contract auditable.
# ruff: noqa: EM101, TRY003, C901, PLR0912, PLR0913
from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from pydantic_ai import ModelResponse
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from pydantic_ai.tools import GenerateToolJsonSchema

from temnia_pipeline.contracts import (
    TopicCandidate,
    TopicOpportunity,
    TopicPortfolioReview,
    TopicProposal,
    TopicSelectionAssessment,
    TopicSelectionColdReview,
    TopicSelectionDraft,
    TopicSelectionPatch,
    TopicSelectionRecord,
    TopicSentenceSpan,
)
from temnia_pipeline.harness.artifacts import canonical_json
from temnia_pipeline.harness.gateway import (
    expected_gateway_body,
    expected_gateway_headers,
    gateway_accounting_model,
    gateway_output_token_parameter,
    parse_generation_observation,
)
from temnia_pipeline.harness.gateway_policy import GATEWAY_URLS, GatewayTransportPolicy
from temnia_pipeline.harness.qualification_editorial import editorial_qualification_case
from temnia_pipeline.harness.topic_feasible import augment_topic_evidence
from temnia_pipeline.harness.topic_selection import (
    SELECTION_COLD_PROMPT,
    SELECTION_PATCH_PROMPT,
    SELECTION_PROMPT,
    SELECTION_SOURCE_PROMPT,
    apply_selection_patch,
    assess_selection,
    content_hash,
    make_rubric,
    selection_cold_prompt,
    selection_patch_prompt,
    selection_prompt,
    selection_source_prompt,
    validate_selection,
)
from temnia_pipeline.harness.topic_selection_runtime import effective_topic_output_tokens

if TYPE_CHECKING:
    from temnia_pipeline.contracts import HarnessEvidence
    from temnia_pipeline.harness.routes import RouteEntry, RouteSnapshot

TOPIC_SELECTION_STAGES = ("topic_author", "topic_cold", "topic_source", "topic_patch")
TOPIC_SELECTION_SCHEMAS = {
    "topic_author": "topic-selection-draft/2",
    "topic_cold": "topic-selection-cold/2",
    "topic_source": "topic-selection-portfolio/2",
    "topic_patch": "topic-selection-patch/2",
}
STAGE_SEATS = {
    "topic_author": "propose",
    "topic_cold": "verify",
    "topic_source": "verify",
    "topic_patch": "propose",
}
_RESPONSE = TypeAdapter(ModelResponse)


def topic_selection_qualification_case() -> tuple[
    HarnessEvidence, TopicSelectionRecord, TopicSelectionAssessment
]:
    """An invented complete discussion with a separate incomplete ending; never customer gold."""
    evidence, _, _, _ = editorial_qualification_case()
    evidence = augment_topic_evidence(evidence)
    span = TopicSentenceSpan(
        firstSentenceId=evidence.sentences[1].id,
        lastSentenceId=evidence.sentences[3].id,
    )
    candidate = TopicCandidate(
        id="garden-care",
        title="How regular watering supports garden roots",
        purpose="Explain a basic garden-care practice.",
        reason="A developed explanation available independently of the greeting.",
        firstSentenceId=span.firstSentenceId,
        lastSentenceId=span.lastSentenceId,
        coreSpans=[span],
        completionSpans=[
            TopicSentenceSpan(
                firstSentenceId=evidence.sentences[3].id,
                lastSentenceId=evidence.sentences[3].id,
            )
        ],
        requiredContextSpans=[],
        meaningChangingFollowups=[],
    )
    opportunity = TopicOpportunity.model_validate(
        {
            "id": "garden-value",
            "candidateIds": [candidate.id],
            "coreSpans": [span],
            "completionSpans": candidate.completionSpans,
            "requiredContextSpans": [],
            "meaningChangingFollowups": [],
            "valueEvidenceSpans": [span],
            "viewerPurpose": candidate.purpose,
            "disposition": "proposed",
            "dispositionReason": "This short explanation can stand independently.",
        }
    )
    rubric = make_rubric("Find worthwhile independent explanations for beginning gardeners.")
    record = TopicSelectionRecord.model_validate(
        {
            "format": "topic-selection/2",
            "runId": str(UUID(int=42)),
            "evidenceSha256": content_hash(evidence),
            "rubricSha256": content_hash(rubric),
            "rubric": rubric,
            "origin": "model",
            "parentSelectionSha256": None,
            "draft": TopicSelectionDraft(
                opportunities=[opportunity],
                proposal=TopicProposal(
                    version=1, candidates=[candidate], summary="One useful discussion."
                ),
            ),
        }
    )
    # The patch fixture grants only a title correction. It supplies no pretend critic results.
    assessment = TopicSelectionAssessment.model_validate(
        {
            "format": "topic-selection-assessment/2",
            "runId": record.runId,
            "selectionSha256": content_hash(record),
            "evidenceSha256": record.evidenceSha256,
            "rubricSha256": record.rubricSha256,
            "proposerFamily": "synthetic-author",
            "verifierFamily": "synthetic-reviewer",
            "coldReviews": [],
            "portfolioReview": None,
            "executionStatus": "needs_review",
            "responseArtifacts": [],
            "reasons": ["Synthetic title-correction fixture; no media or model judgment."],
            "findings": [
                {
                    "id": "synthetic-title",
                    "kind": "unsupported_title",
                    "severity": "required",
                    "affectedCandidateIds": [candidate.id],
                    "opportunityIds": [opportunity.id],
                    "evidenceSpans": [span],
                    "reason": (
                        "The discussion supports regular watering, not a general gardening method."
                    ),
                }
            ],
        }
    )
    return evidence, record, assessment


def topic_selection_qualification_prompts() -> dict[str, tuple[str, type[BaseModel], str]]:
    """Use the four production prompt functions and their native output types."""
    evidence, record, assessment = topic_selection_qualification_case()
    return {
        "topic_author": (
            selection_prompt(evidence, record.rubric),
            TopicSelectionDraft,
            SELECTION_PROMPT,
        ),
        "topic_cold": (
            selection_cold_prompt(evidence, record.draft.proposal.candidates[0], record.rubric),
            TopicSelectionColdReview,
            SELECTION_COLD_PROMPT,
        ),
        "topic_source": (
            selection_source_prompt(evidence, record.draft, record.rubric),
            TopicPortfolioReview,
            SELECTION_SOURCE_PROMPT,
        ),
        "topic_patch": (
            selection_patch_prompt(evidence, record, assessment, content_hash(record)),
            TopicSelectionPatch,
            SELECTION_PATCH_PROMPT,
        ),
    }


def validate_topic_selection_qualification_output(stage: str, output: object) -> None:
    """Source admission is measured separately from schema transport and publication quality."""
    evidence, record, assessment = topic_selection_qualification_case()
    if stage == "topic_author" and isinstance(output, TopicSelectionDraft):
        validate_selection(evidence, output)
        return
    if stage == "topic_patch" and isinstance(output, TopicSelectionPatch):
        apply_selection_patch(evidence, record, content_hash(record), assessment, output)
        return
    if stage not in {"topic_cold", "topic_source"}:
        raise ValueError("topic qualification output has the wrong stage or type")
    if stage == "topic_cold" and not isinstance(output, TopicSelectionColdReview):
        raise ValueError("topic qualification cold output has the wrong type")
    if stage == "topic_source" and not isinstance(output, TopicPortfolioReview):
        raise ValueError("topic qualification source output has the wrong type")
    judged = assess_selection(
        evidence,
        record,
        content_hash(record),
        cold_reviews=[output] if isinstance(output, TopicSelectionColdReview) else [],
        source_review=output if isinstance(output, TopicPortfolioReview) else None,
        author_family="synthetic-author",
        verifier_family="synthetic-reviewer",
    )
    if stage == "topic_cold" and len(judged.coldReviews) != 1:
        raise ValueError("topic qualification cold observation is not source-grounded")
    if stage == "topic_source" and judged.portfolioReview is None:
        raise ValueError("topic qualification source observation is not source-grounded")


def native_schema_sha256(output_type: type[BaseModel]) -> str:
    """Match the pinned SDK native strict schema; transport tests detect SDK drift."""
    schema = TypeAdapter(output_type).json_schema(schema_generator=GenerateToolJsonSchema)
    schema.pop("description", None)
    return _sha(OpenAIJsonSchemaTransformer(schema, strict=True).walk())


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _read_ref(value: dict[str, Any], base: Path) -> bytes:
    path = Path(value["path"])
    raw = (path if path.is_absolute() else base / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != value["sha256"]:
        raise ValueError("topic qualification artifact bytes changed")
    if "sizeBytes" in value and len(raw) != value["sizeBytes"]:
        raise ValueError("topic qualification artifact size changed")
    return raw


def _unsettled_call(call: dict[str, Any]) -> bool:
    """A terminal cohort cannot clear an unfinished or unaccounted-for candidate call."""
    if call.get("state") not in {"planned", "skipped", "known_failure", "passed", "failed"}:
        return True
    cost = call.get("cost")
    if call["state"] in {"passed", "failed"} or cost is not None:
        if not isinstance(cost, dict):
            return True
        observation = cast("dict[str, Any]", cost)
        return (
            observation.get("status") != "reported" or observation.get("actual_cost_micros") is None
        )
    # Planned/skipped calls incurred no dispatch; a conclusive HTTP failure has no
    # generation to settle. They still cannot supply a passed required stage.
    return False


def _terminal_reports(
    snapshot: RouteSnapshot, manifest: dict[str, Any], base: Path
) -> list[tuple[dict[str, Any], Path]]:
    """Read original report bytes once and apply uncertainty fences across every report."""
    reports: list[tuple[dict[str, Any], Path]] = []
    unsettled: set[tuple[str, str, str]] = set()
    for reference in manifest["reports"]:
        report = json.loads(_read_ref(reference, base))
        report_file = Path(reference["path"])
        report_base = (report_file if report_file.is_absolute() else base / report_file).parent
        if (
            report.get("format") != "temnia-gateway-qualification/1"
            or report.get("suite") != "topic-selection"
            or report.get("status") not in {"completed", "halted"}
        ):
            raise ValueError("topic qualification report has the wrong suite or is nonterminal")
        catalogue = {candidate["id"]: candidate for candidate in report["catalogue"]["candidates"]}
        for call in report["calls"]:
            candidate = catalogue[call["candidateId"]]
            transport = cast("dict[str, Any]", candidate.get("transport") or {})
            if _unsettled_call(call):
                unsettled.add(
                    (
                        str(transport.get("gateway", "vercel")),
                        candidate["gatewayModel"],
                        candidate["provider"],
                    )
                )
        reports.append((report, report_base))
    selected = {
        (
            route.transport.gateway if route.transport else "vercel",
            route.gateway_model,
            route.provider,
        )
        for route in snapshot.routes
    }
    if blocked := selected & unsettled:
        message = (
            f"topic qualification selected model/provider has unsettled calls: {sorted(blocked)}"
        )
        raise ValueError(message)
    return reports


def _qualified_output_map(
    snapshot: RouteSnapshot, manifest: dict[str, Any], requested_max: int | None
) -> dict[str, int]:
    """A route profile is explicit intent, never inferred from successful token usage."""
    ceiling = manifest.get("maxOutputTokens")
    if (
        type(ceiling) is not int
        or ceiling <= 0
        or (requested_max is not None and requested_max != ceiling)
    ):
        raise ValueError("topic qualification output setting differs from the requested run")
    usable = {
        route_id
        for seat in set(STAGE_SEATS.values())
        for route_id in snapshot.seats[seat].route_ids
    }
    if manifest["format"] == "topic-selection-qualification/2":
        if "routeMaxOutputTokens" in manifest:
            raise ValueError("topic qualification per-route outputs require explicit version 3")
        return dict.fromkeys(usable, ceiling)
    declared = manifest.get("routeMaxOutputTokens")
    expected = {
        route_id: effective_topic_output_tokens(ceiling, snapshot.route(route_id))
        for route_id in usable
    }
    values = cast("dict[str, Any]", declared) if isinstance(declared, dict) else {}
    if (
        set(values) != usable
        or any(type(value) is not int or value <= 0 for value in values.values())
        or values != expected
    ):
        raise ValueError("topic qualification per-route output map differs from frozen ceilings")
    return expected


def _route_transports(snapshot: RouteSnapshot) -> dict[str, Any]:
    usable = {
        route_id
        for seat in set(STAGE_SEATS.values())
        for route_id in snapshot.seats[seat].route_ids
    }
    result: dict[str, Any] = {}
    for route_id in sorted(usable):
        route = snapshot.route(route_id)
        if route.transport is None:
            raise ValueError("version 4 qualification requires explicit route transport")
        result[route_id] = {
            "transport": route.transport.model_dump(mode="json"),
            "providerAccountingName": route.provider_accounting_name,
        }
        if route.accounting_model is not None:
            result[route_id]["accountingModel"] = route.accounting_model
    return result


def _transport_matches(
    route: RouteEntry,
    candidate: dict[str, Any],
    request: dict[str, Any],
    response: ModelResponse,
    cost: dict[str, Any],
    *,
    transport_bound: bool,
) -> bool:
    if not transport_bound:
        return (
            route.transport is None
            and candidate.get("transport") is None
            and "transport" not in request
        )
    if route.transport is None:
        return False
    policy = route.transport.model_dump(mode="json")
    try:
        recorded = GatewayTransportPolicy.model_validate(candidate.get("transport"), strict=True)
    except ValueError:
        return False
    expected_headers = {
        key.lower(): value for key, value in expected_gateway_headers(route).items()
    }
    http_timeout = dict.fromkeys(
        ("connect", "read", "write", "pool"), route.transport.request_timeout_seconds
    )
    components = cost.get("components", {})
    return (
        recorded == route.transport
        and candidate.get("providerAccountingName") == route.provider_accounting_name
        and candidate.get("accountingModel") == route.accounting_model
        and request.get("transport") == policy
        and request.get("providerAccountingName") == route.provider_accounting_name
        and request.get("accountingModel") == route.accounting_model
        and request.get("method") == "POST"
        and request.get("url") == GATEWAY_URLS[route.transport.gateway] + "/chat/completions"
        and request.get("stream") is (route.transport.mode == "streaming")
        and request.get("httpTimeout") == http_timeout
        and request.get("headers") == expected_headers
        and response.provider_url is not None
        and response.provider_url.rstrip("/") == GATEWAY_URLS[route.transport.gateway]
        and response.model_name == route.gateway_model
        and (response.provider_details or {}).get("gatewayTransport") == policy
        and response.state == "complete"
        and (route.transport.mode != "streaming" or response.finish_reason == "stop")
        and components.get("generationId") == response.provider_response_id
        and components.get("model") == gateway_accounting_model(route)
        and components.get("provider") == (route.provider_accounting_name or route.provider)
        and components.get("isByok") is False
    )


def _request_settings_match(route: RouteEntry, request: dict[str, Any], output: int) -> bool:
    output_key = (
        "maxTokens"
        if gateway_output_token_parameter(route) == "max_tokens"
        else "maxCompletionTokens"
    )
    other_key = "maxCompletionTokens" if output_key == "maxTokens" else "maxTokens"
    if request.get(output_key) != output or request.get(other_key) is not None:
        return False
    if route.transport is not None and route.transport.gateway == "openrouter":
        return (
            request.get("provider") == expected_gateway_body(route)["provider"]
            and request.get("providerOptions") is None
            and request.get("reasoning")
            == ({"effort": route.reasoning_effort} if route.reasoning_effort is not None else None)
            and request.get("reasoningEffort") is None
        )
    return (
        request.get("providerOptions")
        == {"gateway": {"only": [route.provider], "zeroDataRetention": True}}
        and request.get("reasoningEffort") == route.reasoning_effort
    )


def _accounting_receipt_matches(route: RouteEntry, call: dict[str, Any], base: Path) -> bool:
    """A transport-bound charge is reproduced from retained bytes, never a copied total."""
    references = call.get("costReceipts")
    if route.transport is None or not isinstance(references, list) or not references:
        return False
    matched = False
    for reference in cast("list[dict[str, Any]]", references):
        raw = _read_ref(reference, base)
        if (
            reference.get("generationId") != call["generationId"]
            or reference.get("url") != GATEWAY_URLS[route.transport.gateway] + "/generation"
        ):
            return False
        if reference.get("statusCode") != HTTPStatus.OK:
            continue
        observation = parse_generation_observation(
            raw, route=route, gateway=route.transport.gateway, generation_id=call["generationId"]
        )
        matched = matched or observation.model_dump(mode="json") == call["cost"]
    return matched


def validate_topic_selection_qualification(
    snapshot: RouteSnapshot, report_path: Path | str, *, max_output_tokens: int | None = None
) -> None:
    """Require bound, settled live requests for every usable route and new schema.

    A local manifest contains report byte references, not hand-authored per-model pass flags.
    Every selected receipt is rehashed, re-parsed and admitted against today's contract.
    This remains transport/source admission evidence, never an editorial acceptance claim.
    """
    manifest_path = Path(report_path)
    manifest = json.loads(manifest_path.read_bytes())
    if (
        manifest.get("format")
        not in {
            "topic-selection-qualification/2",
            "topic-selection-qualification/3",
            "topic-selection-qualification/4",
        }
        or manifest.get("snapshotId") != snapshot.snapshot_id
        or snapshot.synthetic
    ):
        raise ValueError("topic qualification manifest does not bind this production snapshot")
    transport_bound = manifest["format"] == "topic-selection-qualification/4"
    if transport_bound:
        if manifest.get("routeTransports") != _route_transports(snapshot):
            raise ValueError("topic qualification transport map differs from frozen routes")
    elif "routeTransports" in manifest or any(
        route.transport is not None for route in snapshot.routes
    ):
        raise ValueError("explicit transport qualification requires version 4")
    qualified_outputs = _qualified_output_map(snapshot, manifest, max_output_tokens)
    prompts = topic_selection_qualification_prompts()
    qualified: set[tuple[str, str]] = set()
    for report, report_base in _terminal_reports(snapshot, manifest, manifest_path.parent):
        catalogue = {c["id"]: c for c in report["catalogue"]["candidates"]}
        for call in report["calls"]:
            stage = call["stage"]
            if stage not in prompts or call["state"] != "passed":
                continue
            prompt, output_type, version = prompts[stage]
            candidate = catalogue[call["candidateId"]]
            request = call["request"]
            # These agents have no hidden instruction/tool messages. Match the pinned
            # SDK wire envelope as well as the human-readable production prompt.
            messages = [{"role": "user", "content": prompt}]
            expected_messages = {
                "count": 1,
                "sha256": _sha(messages),
                "sizeBytes": len(canonical_json(messages)),
            }
            if (
                request.get("messages") != expected_messages
                or call.get("promptVersion") != version
                or call.get("schemaVersion") != TOPIC_SELECTION_SCHEMAS[stage]
                or call.get("outputContractSha256") != _sha(output_type.model_json_schema())
                or call.get("promptSha256") != hashlib.sha256(prompt.encode()).hexdigest()
                or call.get("nativeSchemaSha256") != native_schema_sha256(output_type)
                or call.get("nativeSchemaSha256") != request["responseFormat"]["schemaSha256"]
                or request["responseFormat"].get("strict") is not True
                or call.get("cost", {}).get("status") != "reported"
                or call.get("cost", {}).get("actual_cost_micros") is None
                or not call.get("generationId")
            ):
                raise ValueError("topic qualification request identity or settlement is invalid")
            response = _RESPONSE.validate_json(_read_ref(call["response"], report_base))
            if response.provider_response_id != call["generationId"]:
                raise ValueError("topic qualification response generation changed")
            if response.text is None:
                raise ValueError("topic qualification response has no structured text")
            output = output_type.model_validate_json(response.text, strict=True)
            validate_topic_selection_qualification_output(stage, output)
            for route in snapshot.routes:
                if route.id not in qualified_outputs:
                    continue
                qualified_output = qualified_outputs[route.id]
                if (
                    candidate["gatewayModel"] == route.gateway_model
                    and candidate["provider"] == route.provider
                    and candidate["family"] == route.family
                    and request["model"] == route.gateway_model
                    and request["store"] is False
                    and _transport_matches(
                        route,
                        candidate,
                        request,
                        response,
                        call["cost"],
                        transport_bound=transport_bound,
                    )
                    and _request_settings_match(route, request, qualified_output)
                    and (
                        not transport_bound or _accounting_receipt_matches(route, call, report_base)
                    )
                    and qualified_output <= route.max_output_tokens
                    and (
                        manifest["format"] == "topic-selection-qualification/2"
                        or (
                            type(candidate.get("maxOutputTokens")) is int
                            and route.max_output_tokens <= candidate["maxOutputTokens"]
                        )
                    )
                    and request.get("serviceTier") == route.service_tier
                    and not route.cache_enabled
                ):
                    qualified.add((route.id, stage))
    needed = {
        (route_id, stage)
        for stage, seat in STAGE_SEATS.items()
        for route_id in snapshot.seats[seat].route_ids
    }
    if missing := needed - qualified:
        message = f"topic production routes lack exact request qualification: {sorted(missing)}"
        raise ValueError(message)


def bind_topic_selection_qualification(
    snapshot: RouteSnapshot,
    reports: list[Path],
    output: Path,
    *,
    max_output_tokens: int,
    per_route_output: bool = False,
    transport_bound: bool = False,
) -> None:
    """Create a checked manifest without changing the frozen route snapshot."""
    value: dict[str, Any] = {
        "format": "topic-selection-qualification/4"
        if transport_bound
        else "topic-selection-qualification/3"
        if per_route_output
        else "topic-selection-qualification/2",
        "snapshotId": snapshot.snapshot_id,
        "maxOutputTokens": max_output_tokens,
        "reports": [
            {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in reports
        ],
        "proofLimit": (
            "Exact request qualification; full-source editorial acceptance remains separate. "
            "Original completed or halted reports remain intact. Per-route qualification does "
            "not resolve unrelated cohort outcomes or expenses; selected model/provider pairs "
            "with any unsettled call in any referenced report remain excluded."
        ),
    }
    if transport_bound:
        value["routeTransports"] = _route_transports(snapshot)
    if per_route_output or transport_bound:
        value["routeMaxOutputTokens"] = {
            route_id: effective_topic_output_tokens(max_output_tokens, snapshot.route(route_id))
            for route_id in sorted(
                {
                    route_id
                    for seat in set(STAGE_SEATS.values())
                    for route_id in snapshot.seats[seat].route_ids
                }
            )
        }
    # Create-only. Invalid evidence never leaves an admission manifest behind.
    with output.open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")
    try:
        validate_topic_selection_qualification(snapshot, output)
    except BaseException:
        output.unlink()
        raise
