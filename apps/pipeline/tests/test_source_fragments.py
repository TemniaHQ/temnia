"""Exact long-sentence coverage without a whole-sentence prompt or source-size refusal."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from temnia_pipeline.harness.source_index import (
    MAX_READ_CHARACTERS,
    build_topic_source_index,
    read_topic_source,
    validate_source_inspection,
    validate_source_read_ids,
)
from temnia_pipeline.harness.source_progress import (
    MAX_CHECKPOINT_BYTES,
    checkpoint_from_messages,
    inspection_from_checkpoint,
)
from temnia_pipeline.harness.validators import HarnessValidationError
from test_source_progress import INDEX_SHA, _compact, _tool_turn
from test_topic_source_index import FixtureEncoder, _long_evidence


def test_multilingual_sentence_is_read_in_exact_fragments_and_not_counted_early() -> None:
    evidence = _long_evidence(1)
    text = "वक्ता पूरा विचार समझाता है। " * 3_000
    sentence = evidence.sentences[0].model_copy(update={"text": text})
    evidence = evidence.model_copy(update={"sentences": [sentence]})
    index = build_topic_source_index(
        evidence, evidence_sha256="a" * 64, encoder=FixtureEncoder(), embedding_revision="b" * 40
    )
    assert index.sentences[0].text == text
    messages = _compact(
        [ModelRequest(parts=[UserPromptPart("Read the complete selected speech.")])]
    )
    cursor = 0
    pieces: list[str] = []
    while True:
        arguments: dict[str, object] = {
            "first_sentence_id": sentence.id,
            "last_sentence_id": sentence.id,
            "cursor_character": cursor,
        }
        page = read_topic_source(
            index,
            index_sha256=INDEX_SHA,
            first_sentence_id=sentence.id,
            last_sentence_id=sentence.id,
            cursor_character=cursor,
        )
        assert not page.sentences
        assert sum(len(fragment.text) for fragment in page.fragments) <= MAX_READ_CHARACTERS
        pieces.extend(fragment.text for fragment in page.fragments)
        messages = _tool_turn(
            messages,
            name="read_source",
            arguments=arguments,
            result=page,
            identifier=f"fragment-{cursor}",
        )
        checkpoint = checkpoint_from_messages(messages)
        assert checkpoint is not None
        assert len(checkpoint.model_dump_json().encode()) < MAX_CHECKPOINT_BYTES
        trace = inspection_from_checkpoint(checkpoint).model_copy(update={"role": "cold_reviewer"})
        if page.complete:
            validate_source_inspection(index, trace)
            validate_source_read_ids(index, trace, {sentence.id})
            with pytest.raises(HarnessValidationError, match="required speech"):
                validate_source_read_ids(
                    index, trace.model_copy(update={"delivered_fragment_ids": ()}), {sentence.id}
                )
            break
        with pytest.raises(HarnessValidationError, match="required speech"):
            validate_source_read_ids(index, trace, {sentence.id})
        assert page.nextSentenceId == sentence.id
        assert page.nextCharacterOffset is not None
        cursor = page.nextCharacterOffset
    assert "".join(pieces) == text
