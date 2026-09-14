# Indexed editorial recovery batch, 15 September 2026

Rajesh requested closing the current batch, making the pipeline suite green, committing, and
stopping. This record describes that boundary. It does not replace the historical
[production review](indexed-harness-production-review-2026-09-14.md), declare the harness
production-ready, or claim publication acceptance.

## Bounded evidence

V7 source-review prompts now carry a manifest for structured candidate and opportunity context.
`read_editorial_context` delivers at most eight records and 12,000 characters per page. Oversized
records are delivered as exact JSON fragments with ordinal and character offsets. The source
reviewer must consume every assigned context fragment before its decision is admitted. The
16-candidate and 48-opportunity context-planning refusals are disabled for this paged V7 path;
the decision-sized assignments retain their existing bounds. Earlier programs keep inline context.
The V7 source-shard prompt is `topic-selection-source-shard/2`, and its qualification request
includes the added tool. No provider qualification was run in this batch.

Source speech pages contain at most 16,000 characters. An unusually long sentence is preserved
under its original identity and delivered through exact character fragments instead of being
refused at 64,000 characters. The model follows `nextSentenceId` and `nextCharacterOffset`, passing
the latter as `cursor_character`. A partial fragment cannot satisfy speech coverage: admission
replays the immutable reads and requires delivery of all character ranges. The source-index
artifact builder is version 2 so the changed read strategy has a different reuse identity.

Inspection audit segments rotate after 96 calls or 2,048 result IDs, or when checkpoint byte
pressure requires it. The latest checkpoint stays bounded; immutable parent checkpoints retain
the earlier audit. A segmented final trace (`topic-source-inspection/4`) is bound to its final
model response and verified parent chain before coverage is aggregated in an activity. Source
text itself is still selected and compacted. Complete speech coverage means consuming bounded
pages over time, never embedding the entire recording in one model prompt.

## Continuation and retry

Each V7 editorial decision runs in `TopicEditorialWorkWorkflow`. A 32-request PydanticAI quantum
continues that child through Temporal Continue-as-New, carrying assignment identity and compact
context. Its next execution loads the durable source checkpoint. The quantum does not consume an
output-admission retry. The child caches only admitted work, keyed by the exact original assignment;
continuation state does not change that key. A later parent retry can reuse that admitted artifact.

Known transient failures still receive two attempts per eligible route, with a pause before the
second. Conclusive context or parameter incompatibilities advance to another eligible route;
authentication and payment refusals remain explicit stops. Settled invalid outputs retain the
three-attempt correction allowance. Error classification now unwraps child and activity boundaries
so budget pauses and unknown paid outcomes retain their meanings. Unknown paid calls are never
replayed by this recovery loop.

V7 reserves one reviewer family outside every author fallback route. Review selection continues
to exclude all contributing author families, and its fallback position resets when the contributor
set changes. This prevents an author fallback from consuming the last independent reviewer family.

## Editorial resume and product wiring

`topic-editorial-progress/1` records the accepted selection, current revision, seen semantic keys,
phase and optional compiled artifact. Progress is retained after accepted repair and around the
revision compare-and-swap. An incomplete review render remains in the review phase. Explicit retry
can reopen review instead of immediately rerendering that revision, reusing exact admitted shards.
Each explicit V7 resume receives a fresh finite repair allowance. Changed human revisions cannot
be overwritten by stale progress; the narrowly recognized compile/save crash window requires the
exact compiled artifact to match the stored revision.

The web action now selects V7 and exposes retry for `needs_review`; server admission still checks
whether that particular state is resumable. The recorded topic fixture and browser expectation
have been updated. Synthetic inspection records explicitly say that model evidence consumption
was not measured. They are test provenance, not forged production read receipts.

## Verification and limits

Verification results are recorded in the [daily log](../log/2026-09-15.md). New regression cases
exercise dense structured context, audit rotation, exact multilingual source fragments, delivery
proof, author/reviewer reservation and the SDK-quantum continuation boundary. The connected workflow
fixture also retries an incomplete compiled revision with no additional model calls. Workflow
tests replace Temporal I/O; they do not prove real server replay or Continue-as-New recovery.

Outstanding work at this stopping boundary:

- Large connected repair still has its existing limits: 12 findings, eight candidates,
  24 opportunities, eight sections, 16 operations and a 256 KiB prompt. Incomplete staging code
  was removed from this batch.
- Internal-topic interpretation and cross-section reconciliation are not closed by proof that
  the model received all speech. Typed internal subdecisions and editorial evaluation remain.
- Child histories are bounded, but the parent can still accumulate many child events. Parent
  continuation and real Temporal history/concurrent-failure scale tests remain.
- Index build memory, artifact size, upload/transcription/media limits, budgets and actual route
  context still bound execution. There is no demonstrated arbitrary-length upload guarantee.
- The changed V7 request needs exact provider qualification. The V7 production-image/browser
  journey, real two-/four-hour provider runs and human source/playback acceptance were not run.
- No push, deployment or production-ready declaration is part of this stopping batch.
