# Standalone topic production quality v3

September 12, 2026. This is the implementation response to the full-source Karma
failures recorded in
[the OpenRouter evaluation](openrouter-topic-model-evaluation-2026-09-11.md). The
target is unchanged: a generic action should return useful, independently
publishable discussions whose spoken setup, answer and meaning-changing follow-up
are complete. Technical validity and schema qualification remain necessary but do
not establish that outcome.

## Failure being corrected

The v2 author both discovered source opportunities and chose their packaging. That
made omissions hard to distinguish from selection decisions. Its source reviewer
received the author's rationales, which weakened the independence of its challenge.
The repair prompt sent the full source and exposed complete opportunity records even
though most of their fields were immutable. Its operation vocabulary could extend
one edge but could not trim a candidate or replace both edges. A complete but
truncated repair then prevented compilation and rendering of an otherwise valid
selection.

Those defects appeared directly in the Karma runs. Both completed authors ended the
wealth discussion before its answer. Kimi's repair rewrote protected evidence and
was refused. Gemini's repair consumed most of its allowance as reasoning, terminated
at length and left no render. More model auditions against the same program would
not correct these representation and control failures.

## Program contract

`standalone-topics/3` is a separate Temporal workflow and prompt generation. V2
history and schemas remain unchanged.

1. A reviewer-family model reads the whole source before seeing any packaging and
   creates an opportunity inventory. Every entry identifies core value, required
   setup, completion and meaning-changing follow-ups. It cannot propose candidates
   or decide that an opportunity is low value or unextractable.
2. The author receives the exact inventory. It must retain every inventory ID and
   source-evidence field, then map each opportunity to a candidate or an explicit
   disposition. It may add opportunities that the inventory missed.
3. Cold review still receives only one selected transcript, its title and the frozen
   audience rubric. Source review receives the whole source and the selection with
   author rationale and disposition rationale removed.
4. Repair receives required findings, affected candidates, immutable opportunity
   definitions, separately editable mappings and only the authorized source spans
   with adjacent context. `replace_extent` can trim or extend either edge while
   retaining candidate identity, title and viewer purpose. A nonempty v3 patch must
   cite every required finding in the transaction.
5. An invalid or length-truncated repair retains the last valid assessed selection.
   Candidates explicitly declined by source review remain excluded; unresolved
   candidates can still be compiled and rendered for human review.

The inventory is an independent hypothesis, not gold. Its failure remains visible
in run diagnostics and authoring may continue so one unavailable reviewer call does
not erase the entire run. The reviewer and author remain different model families.
No duration, output-count or source-coverage target was added.

## Qualification and rollout

V3 has five exact native stages and a separate `topic-selection-qualification/5`
binding. The proof requires explicit transport identity and settled requests for
the inventory, author, cold review, source review and v3 patch schemas. The worker
reads it from `HARNESS_TOPIC_SELECTION_V3_QUALIFICATION_PATH`; the web and worker
gate new starts with `HARNESS_TOPIC_SELECTION_V3_ENABLED`. V2 retains its existing
flag and proof path.

Source review may retain an `unknown` observation about supplied source material
that affects no candidate or opportunity, such as a dangling transcript fragment
outside every selected discussion. It remains visible in the portfolio review but
does not become an actionable assessment finding or block unrelated candidates.
Any required or preference finding still has to name an affected candidate or
opportunity. This resolves the mismatch exposed when multiple open-weight models
correctly reviewed the qualification candidate but reported the fixture's harmless
unused fragment.

The experiment manifest now freezes the selected program generation and resulting
Temporal workflow type. V3 continues to use the requested PySceneDetect
AdaptiveDetector trial and the existing evidence, compiler, renderer, ledger and
revision workflow. A full Karma run and actual playback judgment are still required
before this program can be called publication ready.

The database start fence selects the enable flag by exact editorial generation.
`standalone-topics/2` requires its v2 flag and `standalone-topics/3` requires its v3
flag. This keeps a qualified v3 rollout independently startable without enabling v2
starts, while retries of already created runs retain their existing behavior.

## Local evidence

Focused tests cover source-inventory admission, immutable inventory retention,
independent reviewer projection, both-edge replacement, all-required-finding patch
coverage, graceful invalid repair, v2 replay compatibility, the five-stage
qualification suite, experiment identity, UI dispatch and the recorded workflow.
The repository clean-commit gate and full-source Karma result are recorded with the
delivered commit rather than anticipated here.
