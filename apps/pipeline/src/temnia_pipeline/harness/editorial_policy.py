"""The one frozen editorial policy identifier a run carries, independent of model routing."""

from typing import Literal

EditorialPolicy = Literal["standalone-topics/3"]
TOPIC_SELECTION_POLICY_V3: EditorialPolicy = "standalone-topics/3"
TOPIC_POLICIES = frozenset({TOPIC_SELECTION_POLICY_V3})


def is_topic_policy(policy: str) -> bool:
    """Identify the lane; every run today carries the one standalone-topic policy."""
    return policy in TOPIC_POLICIES
