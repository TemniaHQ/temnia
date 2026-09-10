"""Portable V2 verification preserves exact retained bytes and legacy body hashes."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from temnia_pipeline.evals.chapters import EditorialVerificationBody, content_sha256
from temnia_pipeline.harness.artifacts import canonical_json

FIXTURE = Path(__file__).parent / "fixtures/harness/editorial-verification-v2.real.json"
FIXTURE_SHA256 = "5732a8484719cdf657e91bff94c1d601bc5d3be8419d0ad1910f050b8e5fd768"


def test_real_postrender_verification_preserves_the_complete_accepted_v2_body() -> None:
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA256
    body = EditorialVerificationBody.model_validate_json(raw)
    assert body.editorial is not None
    assert body.editorial.status == "needs_review"
    assert len(body.editorial.findings) == 2
    assert body.editorial.findings[0].boundaryIds == ("c000002",)
    assert body.editorial.findings[1].boundaryIds == ("c000004",)
    assert canonical_json(body.model_dump(mode="json", by_alias=True)) == raw
    assert content_sha256(body) == FIXTURE_SHA256


def test_legacy_verification_body_roundtrips_without_a_new_null_field() -> None:
    raw = json.loads(FIXTURE.read_bytes())
    del raw["editorial"]
    body = EditorialVerificationBody.model_validate_json(json.dumps(raw))
    assert body.editorial is None
    assert body.model_dump(mode="json", by_alias=True) == raw
    assert content_sha256(body) == hashlib.sha256(canonical_json(raw)).hexdigest()


@pytest.mark.parametrize("field", ["status", "reasons", "finding_reason"])
def test_v2_verification_refuses_a_changed_compatibility_projection(field: str) -> None:
    raw = json.loads(FIXTURE.read_bytes())
    if field == "status":
        raw["verdict"]["status"] = "failed"
    elif field == "reasons":
        raw["verdict"]["reasons"] = list(reversed(raw["verdict"]["reasons"]))
    else:
        raw["editorial"]["findings"][0]["reason"] = "Changed accepted judgment."
    with pytest.raises(ValueError, match="compatibility projection"):
        EditorialVerificationBody.model_validate_json(json.dumps(raw))


def test_v2_verification_keeps_the_strict_editorial_contract() -> None:
    raw = json.loads(FIXTURE.read_bytes())
    changed = copy.deepcopy(raw)
    changed["editorial"]["findings"][0]["disposition"] = "repairable"
    with pytest.raises(ValueError, match="uncertainty cannot authorize a repair"):
        EditorialVerificationBody.model_validate_json(json.dumps(changed))
    changed = copy.deepcopy(raw)
    changed["editorial"]["findings"][0]["inventedField"] = True
    with pytest.raises(ValueError, match="Extra inputs"):
        EditorialVerificationBody.model_validate_json(json.dumps(changed))
