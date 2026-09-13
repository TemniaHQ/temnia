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
    TOPIC_SELECTION_SCHEMAS,
    TOPIC_SELECTION_V3_SCHEMAS,
    native_schema_sha256,
    topic_selection_qualification_prompts,
)
from temnia_pipeline.modal_build import source_build_id

TopicProgramVersion = Literal["standalone-topics/2", "standalone-topics/3"]


def current_program(
    program_version: TopicProgramVersion = "standalone-topics/2",
) -> TopicProgramManifest:
    """Freeze actual template functions/native schemas, including unused repair stages."""
    v3 = program_version == "standalone-topics/3"
    functions = {
        **({"topic_inventory": topic_selection.opportunity_inventory_prompt} if v3 else {}),
        "topic_author": topic_selection.selection_prompt,
        "topic_cold": topic_selection.selection_cold_prompt,
        "topic_source": topic_selection.selection_source_prompt,
        "topic_patch": (
            topic_selection.selection_patch_prompt_v3
            if v3
            else topic_selection.selection_patch_prompt
        ),
    }
    prompts = topic_selection_qualification_prompts(program_version)
    schemas = TOPIC_SELECTION_V3_SCHEMAS if v3 else TOPIC_SELECTION_SCHEMAS
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
