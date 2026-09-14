# Candidate and measured-media evidence tools

2026-09-14. This record freezes the reviewer-only evidence surface added after the hierarchical
source index and durable checkpoint milestones. It supplements
[indexed-editorial-evidence-2026-09-14.md](indexed-editorial-evidence-2026-09-14.md),
[topic-source-index-v2-2026-09-14.md](topic-source-index-v2-2026-09-14.md) and
[indexed-agent-checkpoints-2026-09-14.md](indexed-agent-checkpoints-2026-09-14.md).

The purpose is to make internal candidate structure and already measured media facts directly
inspectable by the independent source reviewer. This is implemented and locally tested behavior.
It does not establish that a model notices every internal split, hears an edit, watches a render or
makes a correct editorial decision.

## Decision and role boundary

Inventory and authoring retain exactly three source tools:
`browse_source`, `search_source` and `read_source`. Source review receives exactly those three plus:

- `inspect_candidate(candidate_id, cursor, limit)`, a mandatory paginated view of every source-index
  leaf intersecting one candidate; and
- `read_media_evidence(first_sentence_id, last_sentence_id, cursor, limit)`, an optional
  chronological projection of measured alignment, boundary, pause, shot and speech-coverage facts.

The accepted selection and evidence artifact references are immutable dependencies of a
source-review call. Organization, run, source, selection, evidence and index identity come from the
runtime; the model cannot supply or replace them as tool arguments. The model can choose a candidate,
sentence span, page cursor and bounded page size only.

Candidate inspection is source-review-only because it is a critique of the accepted author output.
Giving it to the author would add no independent authority. Measured media is kept out of cold review,
which remains isolated to the selected speech and audience policy.

## Candidate inspection contract

`TopicCandidateInspectionPage` binds the selection and index SHA-256 values, candidate ID,
completion cursor and ordered `TopicCandidateRegionHit` records. Each hit contains:

- the exact region and section IDs;
- full region and candidate-intersection sentence edges;
- source times, sentence count, deterministic keywords and preview; and
- one deterministic relation: `same_extent`, `inside_candidate`, `covers_candidate`,
  `opening_overlap` or `closing_overlap`.

Only leaf regions that intersect the candidate are returned. The default page contains eight hits;
the hard maximum is sixteen. Cursor zero is the first hit, and a cursor outside the result set is
refused. Region order is inherited from the validated source index.

The source-review prompt `topic-selection-source/11` requires a complete cursor-zero inspection of
every candidate in accepted selection order. It also requires exact source reads across internal
region changes before the reviewer decides whether a candidate has one coherent viewer purpose or
contains several discussions. Region boundaries and descriptions are navigation hypotheses. One
region can still contain a compound candidate, and several regions can still form one discussion.

Admission replays each tool call against the exact selection and index. It refuses a missing,
reordered, repeated or incomplete candidate traversal, a wrong page cursor or limit, and any changed
region IDs or continuation state. This makes the previously invisible “one compound candidate” case
observable without turning retrieval chunks into automatic split points.

## Measured-media contract

`TopicMediaEvidencePage` binds the evidence SHA-256, source ID, exact sentence span, detector
identity, speech-coverage status and warning list. It returns at most eighty chronological events per
page. Events contain no transcript text and use kind-scoped IDs so a sentence, boundary and pause
cannot collide even if their source identifiers match. Overlong source IDs are represented by a
kind-scoped SHA-256 identity inside the 256-character contract bound.

The event kinds are:

| Kind | Projected measured facts |
| --- | --- |
| `sentence` | time extent, word count, aligned/interpolated counts, minimum available confidence and word IDs |
| `boundary` | time, kind, score, clearance, review flag, reasons and related sentence |
| `pause` | time extent and neighbouring word IDs |
| `shot` | time and detector score |
| `speech_interval` | measured speech-coverage extent |

This projection does not run a detector and does not create new evidence. It reads the accepted
`harness-evidence/1` artifact. The prompt states that these records are sensor data, not playback.
They cannot authorize a claim that the reviewer heard or saw the candidate, and the deterministic
compiler remains the authority for physical cut feasibility.

Media inspection is optional because every editorial source judgment does not need sensor detail.
When it is used, admission reproduces the page from the exact accepted evidence and refuses changed
event IDs, pagination or completion state. Exact selection/evidence/index identity checks happen
before any tool result is produced.

## Durable loop, artifacts and transport

Both tool results participate in `topic-agent-checkpoint/1`. Candidate pages retain region IDs;
media pages retain event IDs. Source text is not copied into either progress record. The existing
limits now count node, sentence and evidence-event IDs together. A third identical successful call
still refuses the loop as no progress.

The final `topic-source-inspection/2` is replayed before the source assessment is published. A
source-review inspection artifact directly depends on the evidence, index, accepted selection,
model response and exact final checkpoint. Other indexed roles keep their existing dependencies.

The model and physical gateway admit only complete role-specific function sets. Inventory and author
requests must carry the exact three-tool set; source-review requests must carry the exact five-tool
set. Partial sets, duplicate declarations, native tools, output tools and non-automatic tool choice
are refused. Optional route pre-flight uses the same five source-review declarations and production
result shapes.

## Local evidence and remaining work

Deterministic tests cover candidate pagination, source intersection relations, chronological media
pagination without transcript text, required inspection of every candidate, exact replay of optional
media pages, checkpoint compaction of both result kinds, role-specific qualification declarations
and the real streaming SDK request shape. Production database and image/browser checks remain part
of the exact-commit repository gate recorded in the day log.

No paid model or GPU call, staging change, deployment, real long-source run, playback evaluation or
human editorial evaluation was performed for this milestone. Safe cross-run index reuse and bounded
portfolio reconciliation remain the next implementation items. Real 44-minute, two-hour and
four-hour provider and editorial runs remain an explicit later evaluation, not a conclusion inferred
from these fixtures.
