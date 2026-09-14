"""The one frozen editorial policy identifier a run carries, independent of model routing."""

from typing import Literal

EditorialPolicy = Literal["standalone-topics/3", "standalone-topics/4"]
TOPIC_SELECTION_POLICY_V3: EditorialPolicy = "standalone-topics/3"
TOPIC_SELECTION_POLICY_V4: EditorialPolicy = "standalone-topics/4"
TOPIC_POLICIES = frozenset({TOPIC_SELECTION_POLICY_V3, TOPIC_SELECTION_POLICY_V4})


def is_topic_policy(policy: str) -> bool:
    """Identify the standalone-topic lane across frozen program generations."""
    return policy in TOPIC_POLICIES
