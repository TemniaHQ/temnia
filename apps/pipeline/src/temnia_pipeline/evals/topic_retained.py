"""Offline admission of an external author receipt as an unqualified diagnostic."""

# ruff: noqa: C901, EM101, PLR0912, PLR0915, TC003, TRY003, TRY004

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from temnia_pipeline.contracts import HarnessEvidence, TopicProposal
from temnia_pipeline.evals.topics import (
    RetainedExpense,
    TopicConfiguration,
    TopicEvaluationBundle,
    digest,
    validate_topic_bundle,
)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("retained JSON contains duplicate keys")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("retained JSON contains a nonfinite constant")


def _read(path: Path) -> tuple[dict[str, Any], str]:
    body = path.read_bytes()
    value = json.loads(body, object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("retained receipt is not a JSON object")
    return cast("dict[str, Any]", value), hashlib.sha256(body).hexdigest()


def _mapping_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("retained receipt requires an array of objects")
    rows = cast("list[object]", value)
    if any(not isinstance(item, dict) for item in rows):
        raise ValueError("retained receipt requires an array of objects")
    return cast("list[dict[str, Any]]", value)


def retained_author_diagnostic(
    *, directory: Path, preparation_sha256: str, response_sha256: str
) -> TopicEvaluationBundle:
    """Verify the documented external probe files; never run its launcher or analyzer."""
    preparation, preparation_hash = _read(directory / "preparation-receipt.json")
    request, request_hash = _read(directory / "openrouter-request.json")
    response, response_hash = _read(directory / "inference-body.bin")
    intent, intent_hash = _read(directory / "attempt-intent.json")
    transport, transport_hash = _read(directory / "inference-transport.json")
    evidence_raw, _evidence_file_hash = _read(directory / "evidence-canonical.json")
    reconciliation, reconciliation_hash = _read(directory / "accounting-reconciliation.json")
    if preparation_hash != preparation_sha256 or response_hash != response_sha256:
        raise ValueError("retained inputs differ from caller-frozen identities")
    if (
        intent.get("preparationReceiptSha256") != preparation_hash
        or request_hash != intent.get("requestSha256")
        or request_hash != preparation.get("openrouterWireBodySha256")
    ):
        raise ValueError("retained request and preparation receipts disagree")
    if (
        transport.get("httpStatus") != 200  # noqa: PLR2004
        or transport.get("bodySha256") != response_hash
        or transport.get("complete") is not True
    ):
        raise ValueError("retained inference transport was not a complete successful response")
    if (
        request.get("model") != preparation.get("model")
        or request.get("model") != response.get("model")
        or response.get("id") != reconciliation.get("generationId")
        or reconciliation.get("inferenceBodySha256") != response_hash
    ):
        raise ValueError("retained response model or generation identity differs")
    references = _mapping_list(preparation.get("evidence"))
    if (
        len(references) != 1
        or digest(evidence_raw) != references[0].get("sha256")
        or references != intent.get("evidence")
    ):
        raise ValueError("retained canonical evidence identity differs")
    native_bytes = (
        json.dumps(request.get("response_format"), ensure_ascii=False, allow_nan=False, indent=2)
        + "\n"
    ).encode()
    if hashlib.sha256(native_bytes).hexdigest() != preparation.get("nativeSchemaSha256"):
        raise ValueError("retained native schema differs from its preparation")
    choices = _mapping_list(response.get("choices"))
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        raise ValueError("retained author result is incomplete")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("retained author response lacks a complete textual proposal")
    # Provider reasoning fields are deliberately neither read nor exported.
    proposal_json = json.loads(content, object_pairs_hook=_unique, parse_constant=_constant)
    proposal = TopicProposal.model_validate(proposal_json)
    evidence = HarnessEvidence.model_validate(evidence_raw)
    if str(evidence.sourceId) != preparation.get(
        "sourceId"
    ) or evidence.transcriptSha256 != preparation.get("transcriptSha256"):
        raise ValueError("retained source or transcript differs from preparation")
    if (
        reconciliation.get("generationMetadataConfirmed") is not True
        or reconciliation.get("costsAgree") is not True
    ):
        raise ValueError("retained charge has not been reconciled")
    generation, generation_hash = _read(directory / "generation-followup-body.bin")
    followup = reconciliation.get("followupTransport", {})
    data = generation.get("data", {})
    if followup.get("bodySha256") != generation_hash or data.get("id") != response.get("id"):
        raise ValueError("retained generation receipt identity differs")
    cost = Decimal(str(response.get("usage", {}).get("cost")))
    if (
        not cost.is_finite()
        or cost < 0
        or cost != Decimal(str(data.get("total_cost")))
        or cost != Decimal(str(reconciliation.get("generationReportedCostUsd")))
    ):
        raise ValueError("retained provider charges disagree")
    cost_micros = int((cost * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    if cost_micros != reconciliation.get("responseReportedCostMicrosRoundedUp"):
        raise ValueError("retained normalized charge differs")
    usage_raw = response.get("usage", {})
    usage = {
        "inputTokens": int(usage_raw.get("prompt_tokens", 0)),
        "outputTokens": int(usage_raw.get("completion_tokens", 0)),
        "reasoningTokens": int(
            usage_raw.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
        ),
    }
    original_identity = {
        "format": "external-author-continuation-manifest/1",
        "qualified": False,
        "newInferenceDispatched": False,
        "continuationExecuted": False,
        "originalRunId": preparation.get("originalRunId"),
        "originalRequestFingerprint": preparation.get("originalRequestFingerprint"),
        "preparationSha256": preparation_hash,
        "requestSha256": request_hash,
        "responseSha256": response_hash,
        "intentSha256": intent_hash,
        "transportSha256": transport_hash,
        "reconciliationSha256": reconciliation_hash,
        "generationMetadataSha256": generation_hash,
        "promptSha256": preparation.get("promptSha256"),
        "nativeSchemaSha256": preparation.get("nativeSchemaSha256"),
        "originalFrozenSourceFiles": preparation.get("frozenSourceFiles", {}),
        "provider": response.get("provider"),
        "requestedProvider": intent.get("provider"),
        "model": response.get("model"),
        "requestedModel": intent.get("model"),
        "canonicalModel": intent.get("canonicalModel"),
        "generationId": response.get("id"),
        "laterStages": (
            "Fresh reviewer/repair/render work must retain separate identities; "
            "no database operation or cache entry is created."
        ),
    }
    bundle = TopicEvaluationBundle(
        source_id=UUID(str(preparation["sourceId"])),
        recording_group=str(preparation["sourceId"]),
        source_fingerprint=evidence.sourceFingerprint,
        duration_ms=evidence.durationMs,
        run_id=None,
        observed_at=datetime.fromisoformat(str(transport["finishedAt"])),
        split="development",
        mode="retained_author_diagnostic",
        status="unqualified_downstream_not_run",
        configuration=TopicConfiguration(
            configuration_id=f"retained-author:{request_hash}",
            policy="standalone-topics/1",
            source_sha256=preparation.get("sourceSha256"),
            transcript_sha256=evidence.transcriptSha256,
            program_identity={
                "originalPolicy": "standalone-topics/1",
                "frozenFiles": preparation.get("frozenSourceFiles", {}),
            },
            author_identity={
                "model": response.get("model"),
                "provider": response.get("provider"),
                "requestSha256": request_hash,
                "settings": {
                    key: value
                    for key, value in request.items()
                    if key not in {"messages", "response_format"}
                },
            },
            prompt_identity={"sha256": preparation.get("promptSha256")},
            schema_identity={"sha256": preparation.get("nativeSchemaSha256")},
            media_identity={"evidenceConfig": evidence.config},
            execution_identity={"origin": "external_retained", "qualified": False},
        ),
        evidence_sha256=digest(evidence_raw),
        evidence=evidence,
        retained_proposal=proposal,
        final_candidate_sha256s=tuple(
            digest(candidate.model_dump(mode="json")) for candidate in proposal.candidates
        ),
        final_selection_sha256=digest(proposal.model_dump(mode="json")),
        retained_identity=original_identity,
        retained_expense=RetainedExpense(
            external_attempt_id=str(intent["attemptId"]),
            generation_id=str(response["id"]),
            response_sha256=response_hash,
            receipt_sha256=reconciliation_hash,
            actual_cost_micros=cost_micros,
            estimated_exposure_micros=int(intent.get("estimate", {}).get("reservedMicros", 0)),
            usage=usage,
        ),
        limitations=(
            (
                "External author receipt only; no workflow continuation, independent critics, "
                "repairs, compilation, renders or human acceptance were executed."
            ),
            (
                "Original native output is preserved; "
                "this record does not qualify a new provider route or prompt."
            ),
            (
                "No audience-rubric artifact existed in this external probe; "
                "audience-dependent qualification remains unavailable."
            ),
        ),
    )
    validate_topic_bundle(bundle)
    return bundle
