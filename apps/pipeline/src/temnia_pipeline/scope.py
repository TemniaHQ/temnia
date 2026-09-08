"""The single pre-identity organization scope seam shared by pipeline tools."""

from temnia_pipeline.contracts import SEEDED_SCOPE, Scope


def resolve_scope() -> Scope:
    """Return the fixed seeded scope until the S24 identity resolver replaces this seam."""
    return SEEDED_SCOPE.model_copy(deep=True)
