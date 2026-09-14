# Topic source index v2

2026-09-14. This record freezes the first hierarchical source-index contract used by the topic
selection harness. It supplements
[indexed-editorial-evidence-2026-09-14.md](indexed-editorial-evidence-2026-09-14.md). It describes
implemented behavior and local evidence, not provider qualification or editorial acceptance.

## Contract and identity

`topic-source-index/2` is a complete immutable `checks` artifact created from one accepted
`harness-evidence/2` artifact. Its content identity binds evidence hash, source, transcript ID and
revision, embedding model and immutable revision, vector dimensions, leaf partition limits,
section fanout, every exact sentence and every hierarchy node. The stable artifact manifest binds
organization, source, exact evidence dependency and every producer choice while omitting run and
editorial-policy identity. Each consuming run publishes a separate use record. V1 artifacts remain
historical records and are not interpreted as v2.

The root ID is `episode`. Its ordered children are `section-0001`, `section-0002`, and so on. Each
section owns at most eight ordered leaves named `region-0001`, `region-0002`, and so on. Leaves form
an exact, gap-free partition of every evidence sentence. They normally contain at most 32 sentences
and 8,000 characters. A single source sentence is indivisible at this layer; a sentence above the
64,000-character exact-read envelope makes index construction unavailable rather than truncating it.

Every node records its exact first and last sentence IDs, source-relative times, sentence count,
parent, ordered children, ordinal, keywords, preview and normalized vector. Keywords are computed
from all owned text after fixed tokenization and stop-word removal. The preview contains bounded
first, middle and last source extracts. A leaf vector averages normalized embeddings of at most
800-character source units. Section and episode vectors are normalized sentence-count-weighted
averages of their child vectors. No model call builds or summarizes this hierarchy.

## Admission invariants

Index admission reproduces the evidence sentence sequence and verifies:

- the exact source, transcript revision and evidence hash;
- one episode root, ordered sections, ordered leaves and unique node IDs;
- exact leaf coverage without gaps, overlaps or reordered sentences;
- each section's exact child IDs and source extent, and the root's complete episode extent;
- the configured leaf and section boundaries;
- deterministic node keywords and previews from the owned sentences;
- finite unit-length leaf vectors and exact bottom-up section and episode vectors.

The stored artifact hash detects any other byte-level mutation. These structural checks make a
malformed but newly hashed index fail closed at the semantic boundary too.

## Model traversal

The initial model request receives only index identity, duration, hierarchy counts and tool limits.
`browse_source(parent_id, cursor, limit)` returns direct children in source order. The root returns
sections and sections return leaves; a leaf refuses browse. `search_source` ranks leaves using the
fixed BM25/cosine baseline. `read_source` returns exact sentences with bounded pagination.

Inventory, author and source-review prompts must browse the root from cursor zero through
completion, then browse every returned section in root order through completion. Code reconstructs
the successful tool calls and results as `topic-source-inspection/2` for new compacted runs and
independently checks every cursor, parent, returned ID and completion marker. Skipping a section,
visiting sections out of
order, jumping to a later cursor, altering a tool result or browsing a leaf is refused. Each role
must also search and must exactly read every sentence it cites in a final source span.

Search remains targeted discovery; it cannot replace chronological traversal. Node descriptions
remain navigation data; they cannot authorize a candidate, finding, quotation or completion claim.

## Scaling evidence and limits

The deterministic four-hour fixture has 2,400 six-second sentences. V2 creates 75 leaves in 10
sections, keeps the serialized initial overview below 500 characters, traverses every hierarchy
edge with bounded pages, retrieves a rare phrase and paginates exact speech. Mutation and trace
tests refuse a broken parent, changed deterministic summary, changed parent vector, skipped section,
out-of-order section and leaf browse.

This establishes local construction, bounded navigation and admission behavior. It does not measure
provider tool reliability, retrieval quality, editorial opportunity recall, latency or cost. The
prompt-history milestone is now implemented in
[indexed-agent-checkpoints-2026-09-14.md](indexed-agent-checkpoints-2026-09-14.md): every new
continuation is rebuilt from a durable compact checkpoint and retained exact excerpts.

## Operational consequences

V2 indexes now use a source-bound producer identity and can be reused by later runs in the same
organization/source scope. Lookup requires the exact accepted evidence identity and revalidates the
stored hash, lineage, complete structure, model and immutable revision before reporting a cache hit.
Every run publishes `topic-source-index-use/1` with its exact evidence/index references and observed
build-or-reuse outcome. The full contract, invalidation and concurrency behavior is recorded in
[source-index-reuse-2026-09-14.md](source-index-reuse-2026-09-14.md).

Active tool returns no longer accumulate on the wire for the indexed roles. The bounded checkpoint
can still refuse a portfolio whose final required exact excerpts exceed its envelope; later bounded
portfolio reconciliation must address that explicitly.

No paid embedding or model route is added by v2. The existing pinned local encoder builds the index,
and every model continuation retains its existing request, ledger, receipt and unknown-outcome
identity. Deployment continues through the existing main-branch automation after review and merge.
