"""The running topic programme's own prompt-template and native-schema identity.

Every topic run freezes this manifest, so a bundle from staging binds the same bytes an
experiment bundle binds. It costs no model call and reads only local source.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Literal

from temnia_pipeline.evals.topics import TopicProgramManifest, TopicProgramStage
from temnia_pipeline.harness import topic_selection
from temnia_pipeline.harness.qualification_topic_selection import (
    STAGE_SEATS,
    TOPIC_SELECTION_V3_SCHEMAS,
    TOPIC_SELECTION_V4_SCHEMAS,
    TOPIC_SELECTION_V5_SCHEMAS,
    TOPIC_SELECTION_V6_SCHEMAS,
    TOPIC_SELECTION_V7_SCHEMAS,
    native_schema_sha256,
    topic_selection_qualification_prompts,
    topic_selection_v4_qualification_prompts,
    topic_selection_v5_qualification_prompts,
    topic_selection_v6_qualification_prompts,
    topic_selection_v7_qualification_prompts,
)
from temnia_pipeline.harness.topic_repair import repair_component_prompt
from temnia_pipeline.modal_build import source_build_id

TopicProgramVersion = Literal[
    "standalone-topics/3",
    "standalone-topics/4",
    "standalone-topics/5",
    "standalone-topics/6",
    "standalone-topics/7",
]


def current_program(
    program_version: TopicProgramVersion = "standalone-topics/3",
) -> TopicProgramManifest:
    """Freeze actual template functions/native schemas, including unused repair stages."""
    if program_version == "standalone-topics/7":
        functions = {
            "topic_inventory_shard": topic_selection.opportunity_inventory_shard_prompt,
            "topic_author": topic_selection.author_packaging_shard_prompt,
            "topic_cold": topic_selection.selection_cold_prompt,
            "topic_source": topic_selection.source_review_shard_prompt,
            "topic_patch": repair_component_prompt,
        }
        prompts = topic_selection_v7_qualification_prompts()
        schemas = TOPIC_SELECTION_V7_SCHEMAS
    elif program_version == "standalone-topics/6":
        functions = {
            "topic_inventory_shard": topic_selection.opportunity_inventory_shard_prompt,
            "topic_author": topic_selection.author_packaging_shard_prompt,
            "topic_cold": topic_selection.selection_cold_prompt,
            "topic_source": topic_selection.source_review_shard_prompt,
            "topic_patch": topic_selection.selection_patch_prompt_v3,
        }
        prompts = topic_selection_v6_qualification_prompts()
        schemas = TOPIC_SELECTION_V6_SCHEMAS
    elif program_version == "standalone-topics/5":
        functions = {
            "topic_inventory_shard": topic_selection.opportunity_inventory_shard_prompt,
            "topic_author": topic_selection.author_packaging_shard_prompt,
            "topic_cold": topic_selection.selection_cold_prompt,
            "topic_source": topic_selection.selection_source_prompt,
            "topic_patch": topic_selection.selection_patch_prompt_v3,
        }
        prompts = topic_selection_v5_qualification_prompts()
        schemas = TOPIC_SELECTION_V5_SCHEMAS
    elif program_version == "standalone-topics/4":
        functions = {
            "topic_inventory_shard": topic_selection.opportunity_inventory_shard_prompt,
            "topic_author": topic_selection.selection_prompt,
            "topic_cold": topic_selection.selection_cold_prompt,
            "topic_source": topic_selection.selection_source_prompt,
            "topic_patch": topic_selection.selection_patch_prompt_v3,
        }
        prompts = topic_selection_v4_qualification_prompts()
        schemas = TOPIC_SELECTION_V4_SCHEMAS
    else:
        functions = {
            "topic_inventory": topic_selection.opportunity_inventory_prompt,
            "topic_author": topic_selection.selection_prompt,
            "topic_cold": topic_selection.selection_cold_prompt,
            "topic_source": topic_selection.selection_source_prompt,
            "topic_patch": topic_selection.selection_patch_prompt_v3,
        }
        prompts = topic_selection_qualification_prompts()
        schemas = TOPIC_SELECTION_V3_SCHEMAS
    return TopicProgramManifest(
        policy=program_version,
        program_version=program_version,
        implementation_sha256=source_build_id(),
        stages={
            stage: TopicProgramStage(
                seat="author" if STAGE_SEATS[stage] == "propose" else "reviewer",
                prompt_version=prompts[stage][2],
                prompt_template_sha256=hashlib.sha256(
                    inspect.getsource(function).encode()
                ).hexdigest(),
                schema_version=schemas[stage],
                native_schema_sha256=native_schema_sha256(prompts[stage][1]),
            )
            for stage, function in functions.items()
        },
    )
