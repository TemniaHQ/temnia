"""Only prepared or historically supported prompt identities may reach durable dispatch."""

from __future__ import annotations

import pytest

from temnia_pipeline.harness.editorial_versions import (
    editorial_dispatch_version,
    editorial_prompt_generation,
)
from temnia_pipeline.harness.runtime_types import VerificationPlan
from temnia_pipeline.harness.validators import HarnessValidationError


def test_old_prepared_plan_keeps_its_wire_shape_and_recorded_dispatch_generation() -> None:
    legacy = VerificationPlan.model_validate_json('{"prompt":"Retained exact v2 prompt"}')
    assert legacy.editorial_prompt_version is None
    assert "editorial_prompt_version" not in legacy.model_dump(mode="json")
    assert (
        editorial_dispatch_version(legacy.editorial_prompt_version) == "chapter-editorial-assess-v2"
    )
    assert (
        editorial_dispatch_version(legacy.editorial_prompt_version, repair=True)
        == "chapter-editorial-repair-v2"
    )


@pytest.mark.parametrize(
    ("repair", "generation"),
    [(False, 1), (False, 2), (False, 3), (True, 1), (True, 2), (True, 3), (True, 4)],
)
def test_supported_retained_prompt_identity_resolves_without_reinterpretation(
    *, repair: bool, generation: int
) -> None:
    version = f"chapter-editorial-{'repair' if repair else 'assess'}-v{generation}"
    plan = VerificationPlan(editorial_prompt_version=version)
    restored = VerificationPlan.model_validate_json(plan.model_dump_json())
    assert editorial_dispatch_version(restored.editorial_prompt_version, repair=repair) == version
    assert editorial_prompt_generation(version, repair=repair) == generation


@pytest.mark.parametrize("version", [None, "", "chapter-editorial-assess-v4", "foreign-v3"])
def test_retained_response_requires_an_explicit_supported_prompt_version(version: object) -> None:
    with pytest.raises(HarnessValidationError, match="unsupported prompt version"):
        editorial_prompt_generation(version)


def test_prepared_version_cannot_cross_editorial_seats() -> None:
    with pytest.raises(HarnessValidationError, match="unsupported prompt version"):
        editorial_dispatch_version("chapter-editorial-repair-v3")
    with pytest.raises(HarnessValidationError, match="unsupported prompt version"):
        editorial_dispatch_version("chapter-editorial-assess-v3", repair=True)
