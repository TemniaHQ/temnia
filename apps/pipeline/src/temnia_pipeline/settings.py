"""Process settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemporalSettings:
    """Where the worker connects and which queue it serves."""

    address: str
    namespace: str
    task_queue: str

    @classmethod
    def from_env(cls) -> TemporalSettings:
        """Read `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`."""
        return cls(
            address=os.environ.get("TEMPORAL_ADDRESS", "localhost:56233"),
            namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
            # Must match TASK_QUEUES.pipeline in @temnia/contracts.
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "temnia-pipeline"),
        )
