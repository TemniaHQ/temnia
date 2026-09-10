"""Frozen per-run editorial policy identifiers, independent of model routing."""

from typing import Literal

EditorialPolicy = Literal["legacy", "chapter-editorial/1", "standalone-topics/1"]
LEGACY_EDITORIAL_POLICY: EditorialPolicy = "legacy"
EDITORIAL_POLICY: EditorialPolicy = "chapter-editorial/1"
TOPIC_POLICY: EditorialPolicy = "standalone-topics/1"
