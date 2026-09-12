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

## Human playback follow-up

Rajesh's first playback review of the nine-video r11 set found the cuts sentence-complete
and the independent treatments close to production quality. It also exposed two narrower
release defects. The compiler split inter-utterance silence at its midpoint, leaving the
tail of Sakala Maa's pause at the next video's opening. In the portfolio, the prayer video
retained the opening of the mantra discussion while the mantra-and-karma video began after
that premise. This is semantic extent misallocation: the later treatment needs the premise,
and the earlier treatment should stop before it.

`topic-compiler/3` now chooses the latest admissible source-grid instant before the next
utterance for both standalone-video edges. The preceding video therefore owns all available
trailing pause; at 25 fps any residue before the next word is strictly less than one frame.
The compiler still refuses to remove speech for a cosmetically exact boundary.

The inventory-first author prompt remains `topic-selection-author/6`; the latest independent
source review is `topic-selection-source/8`, and paired repair is `topic-selection-patch/9`.
Together they require one clear owner for each developed discussion, identify a premise
stranded in a neighbouring video's tail as a required `unfocused_extent` affecting both
candidates, and apply coordinated extent replacements: trim the earlier video and extend
the later video to the earliest necessary premise. Exact repeated core remains a separate
`duplicate_core` defect; context may still overlap when both videos need it to stand alone.

The first follow-up run exposed a stronger control requirement. The author moved the mantra
candidate's start to s237 but left the prayer candidate ending at s247. Although the source prompt
explicitly described this failure, the independent reviewer accepted both overlapping extents.
Prompt emphasis therefore did not make the review exhaustive. The program now computes every exact
candidate overlap and includes it separately from author prose. A V3 source response must classify
each supplied pair exactly once as necessary shared context, misallocated topic extent, duplicate
core or unresolved, while copying the exact overlap span. Misallocation and duplication are invalid
without a grounded required finding naming both candidates. Missing, altered or duplicate overlap
decisions make the entire portfolio observation unavailable. V2 retains its historical response
contract; the stricter shape is `topic-selection-portfolio/3` for V3 only.

The next full Karma run supplied a clean initial handoff: prayer ended at s239 and mantra began at
s240. Its review found that the karma-foundations candidate needed both a longer ending and a title
correction. The repair represented those as `replace_extent` and `retitle` operations on the same
candidate, so the atomic validator correctly refused a multiply affected candidate and retained the
initial selection. That was a missing edit primitive. V3 now supports one `replace_candidate`
operation for a coupled content-and-title or content-and-purpose correction. It preserves identity,
cannot cross the finding-authorized source window, and accepts each changed axis only when a cited
finding grants that exact authority. It does not weaken the single-operation-per-candidate rule.

The following r16 run exposed the remaining ownership loophole. Its initial selection had no
prayer/mantra overlap, but the mantra video began with “And depends…”. Cold review correctly rejected
that dependent opening. Repair repeatedly extended backward to recover its antecedent, eventually
starting mantra at s232 and overlapping the prayer candidate through s247. An intermediate source
review classified s245–s247 as necessary shared context even though the prayer candidate called it
completion and only the mantra candidate called it context. The final source response was invalid,
so the strict gate rendered zero videos.

V3 now accepts `necessary_shared_context` only when the entire exact overlap is inside
`requiredContextSpans` for both candidates. Core development and completion must have one owner.
Authoring and repair also distinguish two cures for a dependent opening: extend backward only for a
same-topic antecedent that changes meaning; otherwise trim connective runway and start at the first
self-contained statement of the new topic. This prevents a local intelligibility repair from
annexing a completed neighbouring discussion.

The r18 run proved that this was still insufficient for adjacent extents. The initial prayer video
ended at s247 and the mantra video began at the dependent s248. Cold review trimmed the opening to
s251; source review then demanded s248–s250 as setup; repair restored s248 and recreated the cold
failure. The third repair converged at the independently intelligible s249 and the final review
reported no findings. It nevertheless left s240–s247 in the prayer video and explicitly described
that material as belonging there. The run spent $0.922924 over 22 settled attempts and rendered 11
videos with `topic-compiler/3`, but it did not implement Rajesh's requested topic ownership. This is
a measured false acceptance, not a reason to reject the already measured sentence-complete cuts.

The source program now derives `candidateHandoffs` for every adjacent non-overlapping pair after
ordering candidate extents by source position. Each row contains the last 16 selected sentences of
the left candidate and first 16 of the right candidate. `topic-selection-portfolio/4` requires one
typed judgment per row: `clean_handoff`, `misallocated_topic_extent`, or `unresolved`. A
misallocation must provide exact recommended left-ending and right-opening sentence IDs inside the
supplied window, keep the final left edge before the right edge, and carry a required
`unfocused_extent` finding naming both candidates. Patch `/9` must implement those two reviewed
edges exactly in one transaction. A repair cannot turn evidence cited to explain a problem into a
literal instruction to include every cited sentence. The overlap-only V3 response remains readable
for historical artifacts; new V3 runs request the V4 portfolio shape.

Exact OpenRouter qualification then exercised all five current stages on Astra, Gemini and Kimi.
The main cohort passed 14 of 15 requests for $0.629171; Gemini's author request ended in a conclusive
HTTP 429 with no charge or unknown outcome. One replacement Gemini-author request passed for
$0.011006. The bound accepted qualification therefore contains 15 passing stage/route observations,
costs $0.640177, and retains the failed transport observation separately. This proves exact request,
schema, grounding and accounting admission. It is not editorial acceptance.

The r20 full Karma run (`b6393525-f373-5683-b0fa-86c9dc01a8ef`) supplied the required negative
model result. Kimi K3 authored and repaired while Gemini 3.8 Flash inventoried and reviewed, with the
same source, fingerprint, evidence, rubric, PySceneDetect detector, compiler and execution settings.
It spent $0.907369 across 19 settled attempts and retained no reservation. The first source review
used the V4 handoff contract correctly to trim the Shani candidate to s201 and move the following
perfection premise into the next candidate at s202. However, the inventory and author had already
merged the entire prayer and mantra discussions into one candidate, s202–s312. Every later review
preserved that compound candidate; the final reviewer called it one distinct high-value video and
reported a clean handoff only at s312/s313.

That is a failed editorial arm against Rajesh's explicit target: prayer must end before the mantra
discussion and the mantra-and-karmic-collision video must own that discussion from its beginning.
The run ended `needs_review` because a separate wealth candidate retained an unresolved required
completion finding. It rendered seven videos, but rendering does not rescue the failed topic split.
The result identifies the remaining model-selection problem precisely: typed adjacent handoffs can
enforce ownership only after two discussions exist as separate candidates. They cannot recover an
internal split that both the inventory and source reviewer fail to discover.

A controlled r22 arm changed only the author route from Kimi K3 to Astra and kept Gemini review and
all other fixed factors identical. The fixed Gemini inventory settled for $0.057453. Astra then
opened an HTTP 200 stream but did not produce a complete response inside the frozen 540-second
aggregate deadline. No generation handle was available for receipt lookup. The run
(`294dcaff-4b8c-5ebe-8974-5cabf3b6fd1e`) is `outcome_unknown`; its $5.271530 reservation remains
active and inference was not replayed. This is Astra's second full-Karma transport failure even
though its short qualification requests pass. It is therefore unavailable for the production
full-source author seat under this transport.

The next declared configuration arm, r23, assigned Gemini to authoring and Kimi to independent
inventory/review. This changed both role identities, so it was a configuration comparison rather
than a pure author substitution. It preserved the source, program, prompts, schemas, detector,
compiler and execution settings. The Kimi inventory settled for $0.235307, then Gemini returned an
HTTP 200 stream followed by an upstream rate-limit error before a complete author response. Run
`6f156f15-b07a-5f00-a3ce-cfc0412d4d3b` is `outcome_unknown` with its $0.213370 reservation retained.
No inference was replayed, and the arm produced no selection to judge.

The next declared challenger uses DeepSeek V4 Pro 0813 through Fireworks for authoring and repair,
with Kimi retaining independent inventory and review. Its exact five-stage qualification passed for
$0.144163. The catalogue-bound route records the canonical accounting model
`deepseek/deepseek-v4-pro-20260813`, 1,048,576 context tokens, 943,718 maximum output tokens and the
provider's advertised structured-output capability. Qualification establishes request admission;
the full-source arm remains the editorial test.

Full Karma run r24 (`02ad8512-6cca-5248-b845-1aa59a5380ae`) settled 22 calls for $1.235900 with no
unsettled expense among them: Kimi inventory, DeepSeek authoring and all 20 Kimi cold reviews. The
proposal represented prayer separately at s223–s240, unlike r20's compound span, but did not satisfy
the human ownership criterion. It overlapped the next sadhana/mantra-risk candidate at s238–s267 and
then split the connected discussion into spiritual window-shopping at s268–s292 and mantra/karmic
collision at s293–s312. That is useful author evidence, not an accepted selection.

Kimi's full-source review opened HTTP 200 but crossed the frozen 540-second aggregate deadline
without a complete response or generation handle. The run is `outcome_unknown` with its $1.015747
reservation retained. It committed no source assessment, patch, final review, edit or render; the
initial proposal is not promoted to a final selection, and the unknown request is not replayed. A
final r25 configuration keeps DeepSeek as author and changes the reviewer from Kimi to Gemini. This
isolates the unavailable full-source reviewer while retaining the stronger proposal family; Gemini
previously completed this exact full-source review shape on Karma.

R25 (`919ecbcc-e41b-5e10-8479-a18304bc22e0`) completed 13 model calls for $0.430738 with no active
reservation and rendered nine videos. It still failed the target. DeepSeek proposed one s223–s308
candidate titled “From Morning Prayer to Continuous Sadhana and the Danger of Unguided Mantras.”
That candidate combines the completed prayer discussion with the later mantra and karmic-collision
discussion instead of transferring ownership between two videos. Gemini's full-source review
explicitly accepted it as complete and distinct, reported no overlap or misallocated handoff, and
called every external handoff clean. Its only required finding concerned the opening of a different
candidate. DeepSeek's attempted repair of that finding was invalid, so the original selection was
retained and the run ended `needs_review`. Final selection SHA-256 is
`dbd9229b395b32c82f929595a12d1e62a5b7cc0308a6fdb66582768faa96361f`; render descriptor SHA-256 is
`0819b418956e1aad771cca95fe302785681783efe0d7959cb6618d46fcf176e4`.

The evaluated model configurations therefore produce no editorial winner. The program now handles
ownership errors between represented neighbouring candidates, but it cannot force review of an
internal topic seam when author and reviewer both label a compound span as one topic. That is the
measured next program problem. More model arms under the same representation would repeat the blind
spot rather than resolve it.

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
