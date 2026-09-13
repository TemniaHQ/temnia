"""Frozen per-run editorial policy identifiers, independent of model routing."""

from typing import Literal

EditorialPolicy = Literal[
    "standalone-topics/1",
    "standalone-topics/2",
    "standalone-topics/3",
]
TOPIC_POLICY: EditorialPolicy = "standalone-topics/1"
TOPIC_SELECTION_POLICY: EditorialPolicy = "standalone-topics/2"
TOPIC_SELECTION_POLICY_V3: EditorialPolicy = "standalone-topics/3"
TOPIC_POLICIES = frozenset({TOPIC_POLICY, TOPIC_SELECTION_POLICY, TOPIC_SELECTION_POLICY_V3})


def is_topic_policy(policy: str) -> bool:
    """Identify the lane without silently changing an existing program generation."""
    return policy in TOPIC_POLICIES
