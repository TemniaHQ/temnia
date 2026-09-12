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
5. An invalid or length-truncated repair retains the last valid assessed selection
   as evidence, but the v3 publication gate renders only candidates explicitly
   selected by a complete source review and carrying no required finding. A missing
   source decision, unresolved decision or known required defect is withheld.

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
revision workflow. A fresh full Karma run and actual playback judgment after each
editorial correction are required before this program can be called publication
ready.

The first production v3 Karma run completed on September 12 with ten candidates and
ten technically valid renders for $0.918466. That run is retained as a failed
editorial trial. The source reviewer twice exhausted a 32,768-token output allowance
after using about 31,450 tokens for internal reasoning, so it never returned a
complete portfolio decision. Both partial responses nevertheless identified the
same concrete defect: the first 10:45 treatment combined several discussions and
fully contained a separately selected sadhana treatment. Direct transcript and media
inspection also found a context-dependent opening in the YouTube-mantra treatment.
The run therefore supplied evidence about the control failure, not publication
acceptance.

Source prompt `topic-selection-source/4` removes duplicated candidate-local review
from that whole-source call. Cold review remains authoritative for local coherence,
completion and viewer value; source review returns only portfolio selection,
source-relative findings and opportunity coverage. Its reasons must be concise and
its JSON complete. This reduces output competition without increasing the declared
token allowance. The stricter v3 render gate prevents another incomplete source
review from silently producing a publishable-looking batch. A fresh Karma run uses
three bounded repair attempts; every repaired candidate must pass a subsequent cold
and source review before rendering.

The database start fence selects the enable flag by exact editorial generation.
`standalone-topics/2` requires its v2 flag and `standalone-topics/3` requires its v3
flag. This keeps a qualified v3 rollout independently startable without enabling v2
starts, while retries of already created runs retain their existing behavior.

## Measured Karma production result

Run `0c3a9707-30b9-558f-b0e3-1098bd1c04e2` exercised the complete v3 program on
the 43:56 Karma source through OpenRouter. Kimi K3 authored and repaired; Gemini
3.8 Flash independently inventoried and reviewed. The run spent $0.652119 over 18
settled attempts, retained zero reservation, and produced a repaired selection with
ten candidates and ten rendered MP4/VTT pairs. The final selection has parent
`714fe74feb1d96d757a943e2adc74bfb2dfaeb5a4e0457ca4aa6c93f71dc394e`,
which records that the renderer consumed a repaired revision rather than the initial
author output.

The first review produced required findings for an unfinished karma-fundamentals
ending, a missing three-strands opening, two physical edges and an unfinished wealth
argument. The author repaired them. Changed candidates then received fresh cold
review, and the repaired portfolio received fresh full-source review. That final
review returned `complete` with one remaining preference: the wealth candidate's
s349–s356 tail begins a separate sports and discipline reflection after the Rahu and
wealth discussion ends at s348.

Direct inspection showed that this preference was a publication defect. V3 now
normalizes a source-grounded `unfocused_extent` affecting a selected candidate to
`required`. Comparative opening and focus opinions from a cold reviewer remain
preferences; the deterministic promotion applies only when the source-relative
review identifies speech outside the candidate's purpose. Replaying the exact r11
record, cold reviews and portfolio review under this rule admits nine candidates and
withholds only the wealth video. Eighteen local tests cover the rule and the other v3
inventory and repair invariants.

All ten r11 media objects and captions were downloaded with descriptor byte-count and
SHA-256 verification. Every MP4 fully decodes with FFmpeg and contains 1280×720 H.264
at 25 fps plus stereo AAC. The nine admitted videos total 36:37 and 286,069,192 media
bytes. Twelve-frame contact sheets show continuous interview footage without black or
corrupt sections; every selected sentence and edge is retained in the review packet.
This is concrete production output. A viewer's final playback judgment remains the
acceptance measurement and is intentionally distinct from these technical and
text/visual inspections.

A fresh run on the final normalization, `7ae3b259-bade-5a1f-94de-ade29acee601`,
matched the same source, fingerprint and evidence hashes. It completed the inventory,
author and several cold-review attempts, then OpenRouter emitted an upstream Gemini
rate-limit error inside an HTTP 200 stream. Temnia terminated it as
`outcome_unknown`, with $0.586809 settled and $0.137916 unresolved exposure. It was
not reset or redispatched. The failed bundle is retained; it neither accepts nor
invalidates the nine-video r11 review set.

## Local evidence

Focused tests cover source-inventory admission, immutable inventory retention,
independent reviewer projection, both-edge replacement, all-required-finding patch
coverage, graceful invalid repair, v2 replay compatibility, the five-stage
qualification suite, experiment identity, UI dispatch and the recorded workflow.
The exact r11 bundle, media, captions, contact sheets, transcripts, technical audit,
publication-gate replay and r13 failed bundle are in the dated local production
review packet. The repository clean-commit gate is recorded with the delivered
commit.
