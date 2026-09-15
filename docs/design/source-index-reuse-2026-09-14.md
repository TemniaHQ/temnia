# Source-bound topic-index reuse

2026-09-14. This record freezes the cache identity, validation and audit behavior for reusing
`topic-source-index/2` across editorial runs. It extends
[topic-source-index-v2-2026-09-14.md](topic-source-index-v2-2026-09-14.md). It records implemented
behavior and local evidence, not a provider, latency or editorial-quality result.

## Two artifact responsibilities

One stable `checks` artifact stores the source index. Its identity is independent of a harness run,
audience brief, rubric, editorial programme and model route because none of those facts changes the
source map. Later runs can reuse that exact artifact within the same organization and source.

Every consuming run also publishes one `topic-source-index-use/1` `checks` artifact. The use record
names the run, source, accepted evidence reference, exact index reference and whether the activity
found a verified cache hit before loading the encoder. It depends directly on both the evidence and
index artifacts. This preserves run-level auditability without copying run identity into the stable
producer cache.

## Stable identity

The source-index fingerprint binds all inputs that can change accepted bytes:

- the accepted evidence artifact ID, kind, fingerprint and SHA-256;
- source ID, transcript ID and transcript revision from the validated evidence;
- index format `topic-source-index/2` and builder version `topic-source-index-builder/1`;
- the MiniLM model and immutable model revision;
- leaf embedding-unit, sentence, character, section-fanout, keyword and preview limits;
- the root identity and maximum indexed-sentence size; and
- Python, NumPy, sentence-transformers and torch versions used by the producer.

The stable artifact metadata repeats this producer configuration and evidence lineage. It has no
`runId` or `programVersion`. Its direct dependency is the exact accepted evidence artifact. A new
evidence artifact, transcript revision, producer constant, library version, model revision or index
format therefore creates a new cache identity even when some resulting content happens to match.

This is deliberately conservative. Later evidence revisions are not assumed equivalent, and a
transcript ID by itself is never a cache key.

## Lookup and admission

Lookup is limited by the ordinary organization scope, exact source ID and source deletion fence.
Before lookup, code verifies that the supplied evidence reference is the accepted `evidence`
artifact in that scope. On a hit it then:

1. compares every reference field with the accepted database row;
2. compares the exact stable fingerprint, metadata and evidence dependency;
3. reads the stored JSON through the artifact service, which verifies size, SHA-256 and canonical
   bytes;
4. parses the complete v2 contract;
5. re-derives all hierarchy, partition, description and vector invariants against the accepted
   evidence; and
6. verifies the immutable encoder model and revision.

Only after all six checks does the activity report `reused=true`. A missing artifact is a normal
cache miss. Corrupt bytes, mismatched lineage, a wrong artifact kind or malformed index fail closed;
they do not trigger an unrecorded rebuild under the same identity.

On a miss the activity loads the exact pinned encoder, verifies its returned revision, builds and
validates the index, and publishes it with the stable identity. The next source-aware operation
re-reads and validates the accepted artifact before exposing a tool result to a model.

## Concurrency and retries

The artifact table already admits only one `(organization, source, kind, fingerprint)` row. Two
workers can both observe a miss and perform duplicate local encoder work. Publication accepts one
row; the other succeeds only if its bytes, storage key, metadata and dependency lineage are exactly
the same. Any difference is an identity conflict. Thus concurrent construction can waste bounded
CPU, but it cannot split one cache identity into different accepted indexes.

`reused` means the activity avoided encoder construction because it verified an existing artifact.
A worker that built identical bytes and lost the publication race reports `reused=false`. If a
first attempt publishes the stable index but fails before publishing its run use record, the
workflow-owned activity retry verifies the cache and publishes a `reused=true` use record. The
accepted index and use record are content-addressed, so Temporal replay or activity retry cannot
create divergent accepted records.

## Retention and invalidation

Reuse stays inside the existing source artifact lifecycle. There is no global, cross-source or
cross-organization vector cache. A source fenced for deletion refuses lookup, construction and
read. Referenced immutable evidence and indexes remain available to in-flight runs under the
ordinary artifact retention rules; deleting the source removes that reusable scope.

No background invalidation mutates an artifact. A changed input creates a new fingerprint and old
artifacts remain historical evidence until normal source retention removes them. Historical
run-scoped v2 indexes remain readable by their original run references but do not match the new
stable producer fingerprint. The first run after this change creates the stable artifact; later
matching runs reuse it.

## Local evidence and limits

Database-backed tests on a fresh migrated Postgres database establish that two matching builds
produce one accepted index row, the second call avoids the encoder, and a stored-byte corruption
fails before any encoder load. Pure tests establish that evidence fingerprint, transcript revision
and source changes alter the cache identity, and that an accepted artifact of another kind cannot
masquerade as evidence. Workflow tests retain the run-scoped use record.

These checks establish identity, authorization, integrity and retry behavior. They do not measure
cache-hit rate, index build time under concurrent load, multilingual retrieval, provider tool use or
editorial quality. Those remain part of the real 44-minute, two-hour and four-hour evaluation after
bounded portfolio reconciliation is implemented.

The first exact-commit attempt found two formatter changes in new tests. After formatting, two full
attempts passed the complete unit, database, lint, type, build and cold-image phases, then exposed an
unrelated timing race in the upload-resume browser test: successful adoption could finish after the
two-second grace before the transient waiting label was observed. The test now refreshes the seeded
upload's liveness immediately before the adopting browser begins, so it still proves both the visible
grace countdown and reuse of the stored first part. This record does not treat either interrupted gate
as acceptance evidence.

The exact clean-commit gate then passed on `f97249a4849766e12a3b7ff9f62a1c7b7e1a7575` at
`2026-09-14T13:29:33.304Z`: 1,226 pipeline tests passed with one expected skip, alongside 33 contract,
202 web, 41 database-isolation, three legacy-reference and two script tests. Every lint, type and
build check passed, both production images built after the required cache prune, and all 18
containerized Playwright tests passed in 2.5 minutes. This is local integrity and execution evidence;
the provider and editorial limits above remain unmeasured.
