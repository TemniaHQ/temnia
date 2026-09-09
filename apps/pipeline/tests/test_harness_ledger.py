from __future__ import annotations

import uuid

import pytest

from temnia_pipeline.harness import ledger


def test_operation_identity_is_canonical_and_run_scoped() -> None:
    run_id = uuid.uuid4()
    first = ledger.operation_identity(
        run_id=run_id,
        kind=ledger.OperationKind.MODEL,
        inputs={"artifact": "abc", "ids": [2, 1]},
        config={"temperature": 0},
    )
    reordered = ledger.operation_identity(
        run_id=run_id,
        kind="model",
        inputs={"ids": [2, 1], "artifact": "abc"},
        config={"temperature": 0},
    )
    other_run = ledger.operation_identity(
        run_id=uuid.uuid4(),
        kind="model",
        inputs={"artifact": "abc", "ids": [2, 1]},
        config={"temperature": 0},
    )
    assert first == reordered
    assert first[0] != other_run[0]
    assert all(len(digest) == 64 for digest in first)


def test_operation_identity_rejects_nonfinite_config() -> None:
    with pytest.raises(ValueError, match="finite canonical JSON"):
        ledger.operation_identity(
            run_id=uuid.uuid4(), kind="model", inputs={}, config={"x": float("nan")}
        )


def test_failure_sanitizer_removes_urls_credentials_and_bounds_output() -> None:
    raw = (
        "POST https://user:password@gateway.example/generate?token=secret "
        "Authorization: Bearer-secret api_key=abc " + "x" * 3000
    )
    clean = ledger.sanitize_failure(raw)
    assert "gateway.example" not in clean
    assert "password" not in clean
    assert "Bearer-secret" not in clean
    assert "api_key=abc" not in clean
    assert len(clean) == ledger.MAX_ERROR_LENGTH
