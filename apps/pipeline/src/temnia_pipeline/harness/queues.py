"""Deterministic routing for short chapter control activities."""


def control_task_queue(pipeline_queue: str) -> str:
    """Keep control capacity independent of media in the same namespace/process."""
    return f"{pipeline_queue}-control"
