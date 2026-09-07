"""Base model for the eval snapshots' JSON.

The snapshots were dumped by the legacy's TypeScript, so their keys are
camelCase while the ported scorers are snake_case; the alias generator bridges
the two without per-field alias noise and keeps constructor signatures
snake_case for the type checker.

This is not the repository's TypeScript-Python seam. That is
`packages/contracts`, whose generated pydantic module is `contracts.py`; these
models exist only to read snapshot files that were frozen before it existed.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    """A snapshot model: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
