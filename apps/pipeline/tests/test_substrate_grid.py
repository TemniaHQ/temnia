"""The grid on constructed timelines where every expected boundary is hand-checkable.

Ported from the legacy `tests/intelligence-grid.test.ts` and the sentence-grid
half of `tests/intelligence-moments.test.ts`, so the port is held to the cases
the TypeScript was held to. The fixtures in `tests/test_substrate_parity.py`
then hold it to the TypeScript's actual output.
"""

from temnia_pipeline.contracts import TranscriptWord, WordTiming
from temnia_pipeline.substrate.grid import (
    CUTTER_MARGIN_MS,
    BackstopResult,
    MsRange,
    ShotGrid,
    build_cut_grid,
    capture_lead_in,
    capture_lead_in_two_turn,
    cutter_window,
    lead_out_trim,
    pause_air_extend,
    pause_boundaries,
    render_coarse,
    render_fine,
    resolve_paragraph_span,
    resolve_span,
    sentence_at_ms,
    sentence_end_times,
    sentence_start_times,
    sentence_starts,
    shot_snap,
    speaker_tag,
    speaker_turn_start_times,
    stale_open_flag,
    stamp_ms,
)


def word(text: str, start_ms: int, end_ms: int, speaker: str | None) -> TranscriptWord:
    return TranscriptWord(
        confidence=0.99,
        endMs=end_ms,
        speaker=speaker,
        startMs=start_ms,
        text=text,
        timing=WordTiming.aligned,
    )


def sentence(text: str, start_ms: int, end_ms: int, speaker: str) -> list[TranscriptWord]:
    """One sentence per phrase, words spread evenly across the span (TS test helper)."""
    parts = text.split(" ")
    step = (end_ms - start_ms) / len(parts)
    return [
        word(
            part,
            round(start_ms + index * step),
            end_ms if index == len(parts) - 1 else round(start_ms + (index + 1) * step) - 2,
            speaker,
        )
        for index, part in enumerate(parts)
    ]


# The interview shape from the TypeScript tests:
#   s0 S1 0-2000      "Tell me about the tour."         (question)
#   s1 S2 2200-3400   "Oh, the standoff?"               (short fragment)
#   s2 S1 3600-5600   "Yes, walk me through it."
#   s3 S2 5800-11800  "So we were in Mohali that day."  (the answer opens)
#   s4 S2 11810-17800 "I demanded the ball from Ricky."
#   s5 S2 17810-20000 "And he finally gave it to me."   (payoff; pause after)
#   s6 S1 21500-23000 "What happened next?"             (next question)
TIMELINE = [
    *sentence("Tell me about the tour.", 0, 2000, "0"),
    *sentence("Oh, the standoff?", 2200, 3400, "1"),
    *sentence("Yes, walk me through it.", 3600, 5600, "0"),
    *sentence("So we were in Mohali that day.", 5800, 11_800, "1"),
    *sentence("I demanded the ball from Ricky.", 11_810, 17_800, "1"),
    *sentence("And he finally gave it to me.", 17_810, 20_000, "1"),
    *sentence("What happened next?", 21_500, 23_000, "0"),
]

GRID = build_cut_grid(TIMELINE)

THREE_SENTENCES = [
    *sentence("One two three.", 200, 1100, "0"),
    *sentence("Four five six.", 1300, 2200, "0"),
    *sentence("Seven eight nine.", 2400, 3300, "0"),
]


# ---- Word-timeline helpers ------------------------------------------------


def test_sentence_starts_follow_terminal_punctuation() -> None:
    assert sentence_starts(THREE_SENTENCES) == [0, 3, 6]


def test_sentence_terminal_accepts_trailing_quotes_and_brackets() -> None:
    assert sentence_starts([word("Done.)", 0, 1, "0"), word("Next", 2, 3, "0")]) == [0, 1]
    assert sentence_starts([word('no."', 0, 1, "0"), word("Then", 2, 3, "0")]) == [0, 1]


def test_sentence_times_span_first_word_to_predecessor_end() -> None:
    assert sentence_start_times(THREE_SENTENCES) == [200, 1300, 2400]
    assert sentence_end_times(THREE_SENTENCES) == [1100, 2200, 3300]


def test_pause_boundaries_use_the_inter_word_gap() -> None:
    words = [
        word("Alpha", 0, 400, "0"),
        word("beta", 500, 900, "0"),
        word("gamma", 1700, 2100, "0"),
    ]
    assert pause_boundaries(words, 700) == [2]
    assert pause_boundaries(words, 900) == []


def test_speaker_turns_start_at_the_first_word_and_at_changes() -> None:
    assert speaker_turn_start_times(TIMELINE) == [0, 2200, 3600, 5800, 21_500]


def test_empty_words_give_empty_helpers() -> None:
    assert sentence_starts([]) == []
    assert sentence_start_times([]) == []
    assert sentence_end_times([]) == []
    assert pause_boundaries([]) == []
    assert speaker_turn_start_times([]) == []


def test_capture_lead_in_grows_over_a_short_other_speaker_turn() -> None:
    interview = [
        *sentence("Earlier context ends here.", 200, 1400, "0"),
        *sentence("What was your plan?", 1600, 2800, "1"),
        *sentence("Well I bowled fast.", 3000, 4200, "0"),
    ]
    captured = capture_lead_in(MsRange(end_ms=4200, start_ms=3000), interview)
    assert captured.start_ms == 1600


def test_capture_lead_in_leaves_a_mid_monologue_start_alone() -> None:
    monologue = [
        *sentence("First thought ends.", 200, 1100, "0"),
        *sentence("Second thought ends.", 1300, 2200, "0"),
    ]
    assert capture_lead_in(MsRange(end_ms=2200, start_ms=1300), monologue).start_ms == 1300


def test_capture_lead_in_ignores_a_long_turn_and_a_long_silence() -> None:
    long_turn = [
        *sentence(" ".join(f"q{index}" for index in range(80)), 0, 40_000, "1"),
        *sentence("The answer here.", 40_200, 41_100, "0"),
    ]
    assert capture_lead_in(MsRange(end_ms=41_100, start_ms=40_200), long_turn).start_ms == 40_200

    gapped = [
        *sentence("A question?", 0, 900, "1"),
        *sentence("An answer.", 5900, 6800, "0"),
    ]
    assert capture_lead_in(MsRange(end_ms=6800, start_ms=5900), gapped).start_ms == 5900


def test_capture_lead_in_does_nothing_when_speakers_are_unknown() -> None:
    unlabeled = [
        word("Setup", 0, 400, None),
        word("line?", 500, 900, None),
        word("The", 1100, 1500, "0"),
        word("answer.", 1600, 2000, "0"),
    ]
    assert capture_lead_in(MsRange(end_ms=2000, start_ms=1100), unlabeled).start_ms == 1100


# ---- build_cut_grid -------------------------------------------------------


def test_tiles_the_words_into_annotated_sentences() -> None:
    assert len(GRID.sentences) == 7
    assert GRID.sentences[0].question is False
    assert GRID.sentences[1].question is True
    assert GRID.sentences[6].question is True
    assert GRID.sentences[3].opens_turn is True
    assert GRID.sentences[4].opens_turn is False
    assert GRID.sentences[5].ends_turn is True
    # The silence between the payoff and the next question.
    assert GRID.sentences[5].pause_after_ms == 21_500 - 20_000


def test_packs_paragraphs_on_speaker_turns_without_splitting_sentences() -> None:
    # Speakers alternate 0/1/0/1 (three sentences) /0 — five paragraphs.
    assert len(GRID.paragraphs) == 5
    assert GRID.paragraphs[3].start_sentence == 3
    assert GRID.paragraphs[3].end_sentence == 5


def test_a_paragraph_breaks_on_the_word_cap() -> None:
    words = [word(f"w{index}", index * 100, index * 100 + 80, "0") for index in range(300)]
    words[-1] = word("end.", 299 * 100, 299 * 100 + 80, "0")
    grid = build_cut_grid(words)
    assert len(grid.sentences) == 1
    # One 300-word sentence is never split: the cap cannot break what it cannot
    # cut, which is the rule that separates these paragraphs from the viewer's.
    assert len(grid.paragraphs) == 1


def test_a_paragraph_breaks_when_the_next_sentence_would_pass_the_cap() -> None:
    words: list[TranscriptWord] = []
    cursor = 0
    for run in range(5):
        for index in range(30):
            text = "end." if index == 29 else f"w{run}_{index}"
            words.append(word(text, cursor, cursor + 80, "0"))
            cursor += 100
    grid = build_cut_grid(words)
    assert len(grid.sentences) == 5
    # Four 30-word sentences reach exactly 120; the fifth breaks the paragraph.
    assert [(p.start_sentence, p.end_sentence) for p in grid.paragraphs] == [(0, 3), (4, 4)]


def test_an_empty_transcript_gives_empty_grids() -> None:
    grid = build_cut_grid([])
    assert grid.sentences == []
    assert grid.paragraphs == []
    assert render_coarse(grid) == ""
    assert render_fine(grid) == ""
    assert sentence_at_ms(grid, 0) is None
    empty_span = resolve_span(grid, 0, 0)
    assert (empty_span.clamped, empty_span.start_ms, empty_span.end_ms) == (False, 0, 0)
    assert resolve_paragraph_span(grid, 0, 0).end_ms == 0


# ---- Resolvers ------------------------------------------------------------


def test_maps_sentence_ids_to_exact_word_times() -> None:
    span = resolve_span(GRID, 3, 5)
    assert (span.clamped, span.start_ms, span.end_ms) == (False, 5800, 20_000)


def test_clamps_out_of_range_and_inverted_ids_with_a_flag() -> None:
    span = resolve_span(GRID, -2, 99)
    assert (span.clamped, span.start_ms, span.end_ms) == (True, 0, 23_000)
    inverted = resolve_span(GRID, 5, 3)
    assert inverted.clamped is True
    assert inverted.end_ms > inverted.start_ms


def test_rounds_a_fractional_id_the_way_javascript_rounds() -> None:
    # Math.round is floor(x + 0.5), so a tie goes up, not to even.
    assert resolve_span(GRID, 2.5, 2.5).start_ms == GRID.sentences[3].start_ms
    assert resolve_span(GRID, 3.5, 3.5).start_ms == GRID.sentences[4].start_ms


def test_maps_paragraph_ids_to_paragraph_spans() -> None:
    span = resolve_paragraph_span(GRID, 3, 3)
    assert (span.start_ms, span.end_ms) == (5800, 20_000)


def test_sentence_at_ms_returns_the_last_started_sentence() -> None:
    assert sentence_at_ms(GRID, 0) is not None
    found = sentence_at_ms(GRID, 6000)
    assert found is not None
    assert found.id == 3
    # Before the first sentence starts, the first sentence.
    before = sentence_at_ms(GRID, -1)
    assert before is not None
    assert before.id == 0


# ---- Renderings -----------------------------------------------------------


def test_coarse_lines_carry_paragraph_ids_and_speaker_tags() -> None:
    coarse = render_coarse(GRID)
    assert "P000 [00:00] S1: Tell me about the tour." in coarse
    assert "P003 [00:05] S2: So we were in Mohali" in coarse


def test_fine_lines_carry_sentence_ids_turn_question_and_pause_marks() -> None:
    fine = render_fine(GRID)
    assert "s0001 [00:02] S2: Oh, the standoff?  ⟲turn ·q" in fine
    assert "¶1.5s" in fine
    assert len(fine.split("\n")) == 7


def test_fine_lines_carry_a_cut_mark_inside_the_snap_window() -> None:
    assert "·cut" in render_fine(GRID, [5800 + 500])
    assert "·cut" not in render_fine(GRID, [5800 + 501])


def test_fine_renders_a_window_when_asked_for_one() -> None:
    assert render_fine(GRID, (), 1, 2).split("\n") == render_fine(GRID).split("\n")[1:3]


def test_a_stamp_pads_and_rolls_past_a_minute() -> None:
    assert stamp_ms(0) == "00:00"
    assert stamp_ms(999) == "00:00"
    assert stamp_ms(65_000) == "01:05"
    assert stamp_ms(3_600_000) == "60:00"


def test_a_speaker_tag_is_one_based_or_the_id_itself() -> None:
    assert speaker_tag(None) == "S?"
    assert speaker_tag("0") == "S1"
    assert speaker_tag("9") == "S10"
    assert speaker_tag("guest") == "Sguest"
    # Number(), not int(): "1.0" is an integer to JavaScript, and the tag the
    # TypeScript prints for it is S2.
    assert speaker_tag("1.0") == "S2"
    assert speaker_tag("1.5") == "S1.5"


# ---- The Cutter window ----------------------------------------------------


def test_the_window_contains_the_two_turns_preceding_the_rough_start() -> None:
    # The rough range is the answer only (s3..s5); the margin is tiny so the
    # turn expansion is what does the work.
    window = cutter_window(GRID, MsRange(end_ms=20_000, start_ms=5800), 100)
    assert window.from_sentence <= 1
    assert window.to_sentence >= 6


def test_the_default_margin_covers_the_whole_short_timeline() -> None:
    window = cutter_window(GRID, MsRange(end_ms=20_000, start_ms=5800))
    assert CUTTER_MARGIN_MS == 90_000
    assert (window.from_sentence, window.to_sentence) == (0, 6)


# ---- Narrative backstops --------------------------------------------------


def test_lead_in_grows_over_the_setup_and_one_short_fragment() -> None:
    result = capture_lead_in_two_turn(MsRange(end_ms=20_000, start_ms=5800), GRID)
    assert "lead_in_captured" in result.flags
    # Two turns back: s2 (the question) and then s1 (the fragment).
    assert result.range.start_ms == 2200


def test_lead_in_does_nothing_when_the_span_opens_mid_sentence() -> None:
    result = capture_lead_in_two_turn(MsRange(end_ms=20_000, start_ms=6100), GRID)
    assert result.flags == []
    assert result.range.start_ms == 6100


def test_lead_in_does_not_cross_a_long_same_speaker_predecessor() -> None:
    # s4 opens mid-monologue: the same speaker is talking before it.
    result = capture_lead_in_two_turn(MsRange(end_ms=20_000, start_ms=11_810), GRID)
    assert result.flags == []


def test_lead_out_trims_a_span_that_ends_on_the_next_question() -> None:
    result = lead_out_trim(MsRange(end_ms=23_000, start_ms=5800), GRID)
    assert "lead_out_trimmed" in result.flags
    assert result.range.end_ms == 20_000


def test_lead_out_leaves_a_span_ending_on_the_payoff_alone() -> None:
    result = lead_out_trim(MsRange(end_ms=20_000, start_ms=5800), GRID)
    assert result.flags == []


def test_pause_air_extends_the_out_point_capped_at_one_gap() -> None:
    # 1500 ms of silence follows the payoff; the extension caps at 700.
    assert pause_air_extend(MsRange(end_ms=20_000, start_ms=5800), GRID).end_ms == 20_700
    # Mid-sentence ends are left alone.
    assert pause_air_extend(MsRange(end_ms=19_000, start_ms=5800), GRID).end_ms == 19_000


def test_shot_snap_moves_an_in_point_onto_a_cut_in_the_preceding_silence() -> None:
    # The silence before s3 runs 5600 to 5800, so a cut at 5700 is snappable.
    result = shot_snap(MsRange(end_ms=20_000, start_ms=5800), GRID, [5700])
    assert "shot_snapped" in result.flags
    assert result.range.start_ms == 5700


def test_shot_snap_never_crosses_speech() -> None:
    # 5500 is inside the previous sentence's words.
    result = shot_snap(MsRange(end_ms=20_000, start_ms=5800), GRID, [5500])
    assert result.flags == []
    assert result.range.start_ms == 5800


def test_shot_snap_moves_an_out_point_into_the_following_pause() -> None:
    result = shot_snap(MsRange(end_ms=20_000, start_ms=5800), GRID, [20_300])
    assert result.range.end_ms == 20_300


def test_shot_snap_without_shots_is_a_no_op() -> None:
    span = MsRange(end_ms=20_000, start_ms=5800)
    assert shot_snap(span, GRID, []) == BackstopResult(flags=[], range=span)


def test_stale_open_flags_a_span_opening_on_a_turn_final_sentence() -> None:
    # s5 is the final sentence of S2's turn: the Preity Zinta shape.
    assert stale_open_flag(MsRange(end_ms=23_000, start_ms=17_810), GRID) is True
    assert stale_open_flag(MsRange(end_ms=20_000, start_ms=5800), GRID) is False


# ---- The shot grid --------------------------------------------------------


def test_only_shots_at_or_above_the_decision_threshold_snap() -> None:
    grid = ShotGrid.model_validate(
        {
            "version": 1,
            "emit_floor": 3.0,
            "decision_threshold": 10.0,
            "shots": [
                {"t": 1.0, "score": 9.99},
                {"t": 2.0005, "score": 10.0},
                {"t": 21.7, "score": 31.4},
            ],
        }
    )
    assert grid.snap_times_ms() == [2001, 21_700]
