"""Frozen per-run editorial policy identifiers, independent of model routing."""

from typing import Literal

EditorialPolicy = Literal[
    "legacy", "chapter-editorial/1", "standalone-topics/1", "standalone-topics/2"
]
LEGACY_EDITORIAL_POLICY: EditorialPolicy = "legacy"
EDITORIAL_POLICY: EditorialPolicy = "chapter-editorial/1"
TOPIC_POLICY: EditorialPolicy = "standalone-topics/1"
TOPIC_SELECTION_POLICY: EditorialPolicy = "standalone-topics/2"
TOPIC_POLICIES = frozenset({TOPIC_POLICY, TOPIC_SELECTION_POLICY})


def is_topic_policy(policy: str) -> bool:
    """Identify the lane without silently changing an existing program generation."""
    return policy in TOPIC_POLICIES
