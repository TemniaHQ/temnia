"""Sentences and paragraphs from Segment-any-Text.

The legacy grid finds sentences by looking for `.`, `!` and `?` in Whisper's
output, which the PRD itself names as the grid's weakness: the punctuation is
the ASR's guess, it is missing from most languages' output, and a sentence the
model never terminated silently becomes a 300-word one. `wtpsplit`'s SaT models
predict sentence boundaries from the text itself, were evaluated on ASR
transcripts with all punctuation and casing removed, and ship LoRA adapters for
transcribed speech.

How a segment becomes a word range. The input is the word texts joined by
single spaces, and each word's character offset is recorded while it is built.
wtpsplit's contract is that the returned segments concatenate back to the input
exactly, which is asserted here rather than assumed, so the segment boundaries
are character offsets into the same string. Each word then belongs to the
segment that holds its FIRST character, so a boundary predicted in the middle
of a word does not split it, and because both sequences run left to right the
mapping is one walk. Sentences therefore tile the words in order, with no gaps
and no overlaps, which is what makes `word_start` and `word_end` a safe address
for exact times.

Paragraphs, and why SaT's own paragraph mode is not the default. SaT predicts a
newline probability as well as a sentence probability, so it segments
paragraphs too, and the design expected two paragraph candidates to score
against each other. Measured on the fixtures here on 2026-09-07 it returns one
paragraph per sentence: 13 sentences and 13 paragraphs on `speech-40s`, 6 and 6
on `signal-boost-snippet`, unchanged at paragraph thresholds up to 0.95 and
barely moved at 0.99. That is what a transcript joined with single spaces and
no newline anywhere looks like to a model trained to predict newlines. So
`paragraphs=False` is the default and paragraphs come from the legacy rule over
SaT's sentences, which is the baseline the design named; `paragraphs=True` is
still there and still selectable by the eval runner, because the finding is a
measurement on four fixtures and a recorded episode may say otherwise.

What SaT does not decide. Speakers come from diarization, not from the text: a
sentence's speaker is the majority speaker of its words, ties going to the one
that speaks first. Questions come from Whisper's `?`, read with the legacy's
own regex so that `?"` still counts; a punctuation-restoration model as a
second opinion is a follow-up the plan names, not a dependency taken here.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from temnia_pipeline.substrate.backends import (
    library_version,
    load_sat,
    model_revision,
    sat_paragraph_lengths,
    sat_segments,
)
from temnia_pipeline.substrate.grid import PARAGRAPH_GAP_MS, QUESTION_TERMINAL
from temnia_pipeline.substrate.legacy_rules import RULE_SCORE, pack_paragraphs
from temnia_pipeline.substrate.model import (
    BoundaryCandidate,
    Layers,
    Paragraph,
    Provenance,
    Sentence,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from temnia_pipeline.substrate.backends import SaTModel
    from temnia_pipeline.substrate.grid import GridWord

#: The base model: three layers, small, and fast enough on the worker's CPU
#: that a two-hour episode is seconds. `sat-12l` on the Modal GPU is the
#: quality option and is selected by name, not by a flag.
DEFAULT_SAT_MODEL = "sat-3l-sm"


def _majority_speaker(words: Sequence[GridWord]) -> str | None:
    """The speaker most of a sentence's words carry.

    A sentence can tile across a turn (one word of crosstalk is in the
    `signal-boost-snippet` fixture), and the majority is a better answer than
    the first word's. `Counter.most_common` breaks a tie by first appearance,
    so an even split goes to whoever spoke first, which is the reading the
    legacy's own rule gives.
    """
    counts = Counter(word.speaker for word in words)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def word_char_starts(words: Sequence[GridWord]) -> tuple[str, list[int]]:
    """The joined text, and where each word's first character sits in it."""
    starts: list[int] = []
    offset = 0
    for word in words:
        starts.append(offset)
        offset += len(word.text) + 1
    return " ".join(word.text for word in words), starts


def word_groups(segments: Sequence[str], word_starts: Sequence[int]) -> list[list[int]]:
    """Word indices per segment, in order, dropping segments that hold none.

    A segment can hold no word when it is only whitespace, which the model
    produces at the very end of some inputs.
    """
    ends: list[int] = []
    offset = 0
    for segment in segments:
        offset += len(segment)
        ends.append(offset)
    groups: list[list[int]] = [[] for _ in segments]
    cursor = 0
    for index, start in enumerate(word_starts):
        while cursor < len(ends) - 1 and ends[cursor] <= start:
            cursor += 1
        groups[cursor].append(index)
    return groups


class SaTSegmenter:
    """Segment-any-Text sentences, with paragraphs from SaT or the legacy rule."""

    name = "sat"

    def __init__(
        self,
        model: str = DEFAULT_SAT_MODEL,
        *,
        style_or_domain: str | None = None,
        language: str | None = None,
        threshold: float | None = None,
        paragraphs: bool = False,
    ) -> None:
        self.model = model
        self.style_or_domain = style_or_domain
        self.language = language
        self.threshold = threshold
        self.paragraphs = paragraphs
        self._sat: SaTModel | None = None

    def _load(self) -> SaTModel:
        """Load once per instance; a two-hour episode is one `split` call."""
        if self._sat is None:
            self._sat = load_sat(
                self.model, style_or_domain=self.style_or_domain, language=self.language
            )
        return self._sat

    def _provenance(self, *, paragraph_source: str, shots: int) -> Provenance:
        models = {"sat": self.model}
        if self.style_or_domain is not None and self.language is not None:
            models["sat_adapter"] = f"{self.style_or_domain}/{self.language}"
        return Provenance(
            segmenter=self.name,
            models=models,
            params={
                "language": self.language,
                "paragraph_source": paragraph_source,
                "paragraphs": self.paragraphs,
                "shots": shots,
                "style_or_domain": self.style_or_domain,
                "threshold": self.threshold,
            },
            versions={
                "torch": library_version("torch"),
                "transformers": library_version("transformers"),
                "wtpsplit": library_version("wtpsplit"),
                **(
                    {"sat_revision": revision}
                    if (revision := model_revision(f"segment-any-text/{self.model}")) is not None
                    else {}
                ),
            },
        )

    def _split(self, text: str) -> tuple[list[str], list[int]]:
        """The flat sentence list, and how many of them each paragraph holds."""
        sat = self._load()
        answer = sat.split(text, self.threshold, do_paragraph_segmentation=self.paragraphs)
        sentences = sat_segments(answer)
        joined = "".join(sentences)
        if joined != text:
            msg = (
                "wtpsplit's segments do not concatenate back to the input "
                f"({len(joined)} characters against {len(text)}); the character "
                "offsets the word mapping needs are not trustworthy"
            )
            raise ValueError(msg)
        lengths = sat_paragraph_lengths(answer) if self.paragraphs else [len(sentences)]
        return sentences, lengths

    def segment(self, words: Sequence[GridWord], *, shot_times_ms: Sequence[int] = ()) -> Layers:
        """Sentences from the text, paragraphs from SaT or from the legacy rule."""
        if not words:
            return Layers(
                words=words,
                sentences=(),
                paragraphs=(),
                candidates=(),
                provenance=self._provenance(paragraph_source="none", shots=len(shot_times_ms)),
            )

        text, word_starts = word_char_starts(words)
        segments, paragraph_lengths = self._split(text)
        groups = word_groups(segments, word_starts)

        sentences: list[Sentence] = []
        # Which paragraph each surviving sentence came out of, so a segment
        # that held no word cannot shift the paragraph boundaries.
        paragraph_of: list[int] = []
        paragraph_index = 0
        remaining = paragraph_lengths[0] if paragraph_lengths else len(segments)
        for group in groups:
            while remaining == 0 and paragraph_index + 1 < len(paragraph_lengths):
                paragraph_index += 1
                remaining = paragraph_lengths[paragraph_index]
            remaining -= 1
            if not group:
                continue
            first, last = group[0], group[-1]
            span = words[first : last + 1]
            sentence_text = " ".join(word.text for word in span)
            # The latest end among the words, not the last word's end: with
            # overlapping speech a sentence's last word can end before an
            # earlier one does, and a span that ends there leaves speech
            # outside every sentence (S2 review, I11).
            sentences.append(
                Sentence(
                    id=len(sentences),
                    start_ms=words[first].startMs,
                    end_ms=max(word.endMs for word in span),
                    word_start=first,
                    word_end=last,
                    speaker=_majority_speaker(span),
                    text=sentence_text,
                    is_question=QUESTION_TERMINAL.search(sentence_text) is not None,
                )
            )
            paragraph_of.append(paragraph_index)

        if not self.paragraphs:
            packed = pack_paragraphs(sentences, whole_extent=True)
            return Layers(
                words=words,
                sentences=tuple(sentences),
                paragraphs=packed.paragraphs,
                candidates=packed.candidates,
                provenance=self._provenance(
                    paragraph_source="legacy_rule", shots=len(shot_times_ms)
                ),
            )

        paragraphs, candidates = _paragraphs_from_sat(sentences, paragraph_of)
        return Layers(
            words=words,
            sentences=tuple(sentences),
            paragraphs=paragraphs,
            candidates=candidates,
            provenance=self._provenance(paragraph_source="sat", shots=len(shot_times_ms)),
        )


def _paragraphs_from_sat(
    sentences: Sequence[Sentence], paragraph_of: Sequence[int]
) -> tuple[tuple[Paragraph, ...], tuple[BoundaryCandidate, ...]]:
    """Group the sentences by the paragraph SaT put them in, and say why each broke.

    SaT's paragraph mode returns a decision, not a probability, so every
    candidate scores 1.0 the way the legacy rules' do. The kind is read off the
    same evidence the legacy rule uses (a speaker change is a `turn`, a gap over
    the legacy threshold is a `pause`), and a break with neither is the
    model saying the subject moved on, which is a `topic`. Scores are only
    comparable within one segmenter's own list; `BoundaryCandidate` says so.
    """
    paragraphs: list[Paragraph] = []
    candidates: list[BoundaryCandidate] = []
    start = 0
    for index in range(1, len(sentences) + 1):
        if index < len(sentences) and paragraph_of[index] == paragraph_of[start]:
            continue
        paragraphs.append(
            Paragraph(
                id=len(paragraphs),
                start_ms=sentences[start].start_ms,
                end_ms=max(sentence.end_ms for sentence in sentences[start:index]),
                sentence_start=start,
                sentence_end=index - 1,
                speaker=sentences[start].speaker,
            )
        )
        if index < len(sentences):
            opener = sentences[index]
            previous = sentences[index - 1]
            if opener.speaker != sentences[start].speaker:
                kind = "turn"
            elif opener.start_ms - previous.end_ms > PARAGRAPH_GAP_MS:
                kind = "pause"
            else:
                kind = "topic"
            candidates.append(
                BoundaryCandidate(
                    sentence_id=opener.id, ms=opener.start_ms, score=RULE_SCORE, kind=kind
                )
            )
            start = index
    return tuple(paragraphs), tuple(candidates)
