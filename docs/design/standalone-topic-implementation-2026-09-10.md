# Standalone topic implementation

September 10, 2026. This records the implementation following the
[research](standalone-topic-intelligence-2026-09-10.md) and
[experiment plan](../plans/standalone-topic-intelligence.md). Implementation and automated tests
are not evidence of human editorial acceptance. The full Karma baseline remains unchanged.

## Implemented behavior

The primary source action is **Find topic videos**. It supplies the generic editorial brief when
custom instructions are absent. The new `TopicRunWorkflow` freezes `standalone-topics/1` on the run;
historical chapter histories and their exact-cover schemas remain separate. Web queries and
mutations distinguish the policies, including pending request identities and unknown start outcomes.

A `TopicProposal` identifies each independently useful discussion, its purpose, selected sentence
extent, core, necessary context, completion evidence, and meaning-changing follow-ups. The semantic
fields are source references, not proof that the model's interpretation is correct. Code grounds
all IDs and derives quotes. Outputs are independent contiguous source intervals: they may overlap,
and unused source time does not have to become an exported greeting, bridge, or outro. There is no
requested video count or duration.

The qualified route snapshot supplies the seats. The program reserves a verifier family before
choosing its author; authoring and automatic repairs cannot consume that family. This is a routing
invariant, not a claim that the first configured model won a standalone-topic audition.

The author sees the complete source transcript. Each proposed video then has a cold review that
sees only its selected speech and title, without source context outside the video or the author's
rationale. A separate source-context review looks for missing setup, consequential follow-ups,
corrections, and duplicate core purposes. Shared setup is allowed. The criteria do not average away
a failed opening or ending. Passing and failing judgments require source evidence; invalid findings
are retained as unavailable review, with their original paid response, rather than manufactured
into a pass. These are text judgments and never claim that a model heard or watched the render.

Grounded editorial failures and deterministic unsafe-edge findings can authorize automatic repair.
Unknown-only observations cannot authorize rewriting. Unaffected candidates stay fixed. Physical-only
repairs retain their selected speech and may expand only the affected edge. Cold reviews of unchanged
title/span/ID inputs are reused; cosmetic proposal prose does not count as progress. The run retains
original proposals, findings, responses, costs, automatic revisions and final compiler refusals.
Finite operational repair/dispatch guards and unknown-outcome fences remain; these are not output
count/duration targets.

An initially source-invalid proposal is retained as a diagnostic bound to its exact successful paid
response. One of the same finite repair allowances can correct those validation findings before
any critic is called. Repeated invalid selections stop early. An unknown provider outcome cannot
enter this recovery path, and later semantic repair still preserves unaffected candidates.

## Independent compilation and actual media

`TopicEditSpec` is a portfolio of independent video executions. Each execution reuses a valid
`ChapterEditSpec` internally: one keep and outside drops. That local execution map is exact-cover;
the exported portfolio is not a global partition. The old validator is not weakened.

The topic compiler filters quantized candidate edges to preserve every selected word extent and
avoid crossing aligned or detected speech. It exposes the same physical boundary constraints before
repair and at final compilation. Missing speech evidence stays a review flag. A candidate without a
legal edge remains a recorded refusal, not a silently shortened output.

The topic renderer leases one verified source cache and uses the established accurate-seek renderer,
caption generation, and artifact checks for each independent execution. Its immutable descriptor
binds the portfolio to each execution and each child render descriptor. Technical checks and textual
editorial checks are separate from the person's acceptance.

`TopicReviewWorkflow` applies accept/reject/cancel through the existing scoped immutable revision
and compare-and-set machinery. It verifies the exact rendered candidate before acceptance. Current
export manifests contain only human-accepted videos; partial acceptance can expose those downloads.
The run becomes ready only when the complete compiled portfolio is decided and at least one video
is accepted. A successful model judgment alone cannot mark the run accepted.

## Audio and scene evidence

The new policy includes the existing independent Silero speech coverage. Gaps between aligned words
continue to be labelled as word gaps, not measured acoustic silence.

Ingest's historical shot JSON has no verified master/proxy hash or timestamp mapping. New topic
evidence measures scenes against the already verified master and selected video stream. Cache
identity includes source bytes, selected timeline, detector identity, settings and explicit
PTS-to-source mapping. Unavailable detection and measured-empty detection have different provenance.

At Rajesh's request, the next harness trial uses PySceneDetect AdaptiveDetector. The adapter
uses selected-track PyAV decoding and rational PTS-backed time, including VFR; it does not reconstruct
time from a frame count and nominal frame rate. Native adaptive ratio and content score are retained.
The pinned runtime is `scenedetect-headless==0.7.1`, installed by the worker image's `scene-challenger`
extra with an import check during the build. `HARNESS_TOPIC_SHOT_DETECTOR` selects
`pyscenedetect-adaptive` for new topic runs by default, or `scdet` for an explicit comparison.
The first insert saves the choice as `topicShotDetector` in the run snapshot. Refetches use that
saved value; historical snapshots without it remain `scdet`. Installation of both regular and
headless distributions, a wrong version, or a missing dependency is explicit unavailable evidence,
with no silent switch to the other detector.

FFmpeg `scdet` remains an explicitly selectable comparison. Its raw 0–100 score is retained; the
normalized selection score is explicitly not a probability. PySceneDetect's adaptive score likewise
does not represent the probability that a discussion is complete.

Fresh comparison: FFmpeg already exists in the execution image and supplies a measured baseline.
PySceneDetect adds adaptive visual-change scoring, but neither detector establishes discussion
completion. The adapter passed against the actual 0.7.1 runtime; this validates its API and
timing path, not superiority on standalone cuts. Its API/PTS decisions follow the current
[detector documentation](https://www.scenedetect.com/docs/latest/api/detectors.html) and
[0.7 migration guide](https://www.scenedetect.com/docs/latest/api/migration_guide.html).
The matched semantic-span detector comparison in the experiment plan remains necessary.

A local pass on the complete verified Karma master returned 428 adaptive cuts in 211.526 seconds
and 409 selected FFmpeg cuts in 15.617 seconds; 403 matched exactly. Each used its production
decode/scaling path, sequentially on this Mac. This records actual runtime and agreement, not a
controlled speed benchmark or scene-quality ranking. No labelled scene or editorial gold was used.
The daily log records the immutable result receipt and its hash. PySceneDetect remains selected for
the next topic-workflow trial at Rajesh's request.

Chapter-Llama's existing frozen-deployment integration is available as optional source-bound
navigation evidence. Its suggestions are re-read and validated against the exact source before
being offered to the author. They are not cut commands, completion judgments, or substitutes for
independent review. The prepared first topic comparison leaves it disabled to avoid changing that
factor simultaneously with the editorial architecture.

## Validation and current limits

Focused tests cover independent overlaps, mandatory speech retention, unsafe cut constraints,
source-relative time mapping, cold-context isolation, grounded repair authority, unchanged-review
reuse, source/portfolio identity, human decisions, export membership and model accounting. The
optional detector's real package test was run separately. Exact test totals and final gate state
belong in the daily log and PR verification record.

The prepared full-source Karma experiment uses the existing 43:56 master and accepted transcript,
the exact generic button brief, and a frozen copy of the implementation. It has no manually supplied
topic extents. The unchanged prior baseline has 12 kept chapters, zero independent editorial calls,
and $0.14417573 settled model cost. A new standalone acceptance rate has not been measured.

The new full-source provider run has not started as of this implementation record: automatic approval
review requires explicit user approval for sending the transcript through Vercel AI Gateway to the
named DeepSeek/DeepInfra and GLM/Baseten routes. The retained route wrapper enforces zero data
retention and provider-only routing; that does not substitute for the requested authorization.
No new paid inference or new Karma video outputs are claimed here.

Remaining evaluation work includes actual full-source output inspection, matched partition versus
independent-span comparison, human acceptance on untouched recordings, and per-seat model auditions.
Full-source context admission is checked explicitly; inputs too large for the selected route do not
silently lose context. The larger-source hierarchy and a measured model winner are not claimed by
this implementation. Minor review-speed improvements remain in the deferred backlog.
