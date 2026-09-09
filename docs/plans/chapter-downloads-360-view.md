# Accepted chapter downloads — 2026-09-09

Manual-testing preparation found that **Export accepted files** opens the internal `ChapterExport`
JSON manifest. A reviewer can play current previews but cannot directly download the accepted
chapter videos/captions, especially after a new revision replaces those previews. Complete this
existing export flow before handing PR #24 over for manual testing.

## Intended behavior

Show one **Accepted files** section for the selected run's accepted revision, labeled **Last accepted
output** when a newer edit is under review. List chapter titles from that exact accepted edit,
with **Download video** and **Download captions** links for each accepted keep. Use the existing
organization-scoped media proxy and same-origin HTML downloads, so large videos stream without a
browser/server ZIP buffer. Name downloads predictably by chapter order and revision. Preserve an
explicit **Download manifest** link for the structured export. No new GPU/model call, export mutation,
DDL, storage copy, public URL, or bulk archive is required.

## Identity, failure and scale

Read the immutable accepted edit and export through the existing bounded hash/size-verified JSON
loader and strict schemas. Require the selected source/run, accepted revision and edit hash to match;
require exactly one render per accepted keep, in accepted order, with unique section IDs and matching
edit identity. Derive titles from the accepted edit, never the current draft. Validate every required
check through its exact immutable reference and existing technical-eligibility rules before exposing
downloads. Missing/failed/corrupt/mismatched artifacts give a readable error with explicit retry and no
download links. Warnings stay visible. An accepted all-drop edit has an explicit no-files explanation.

Load a bounded number of JSON artifacts concurrently. Retain the existing JSON size/cache limits.
Do not fetch full video bodies in JavaScript. Key loading/rendering to source/run/revision/artifact
identity; switching runs or changing pointers immediately removes obsolete links, and late responses
cannot repopulate the previous run. Polling an unchanged immutable identity must not reset the panel.
Network recovery retries only artifact reads. Stable accepted files survive new edits, cancellation,
transcript correction and switching back to an older run.

## Scope and verification

The normal scoped query supplies the accepted pointer and immutable refs; downloads re-enter the
existing authenticated organization media boundary. Reject object references outside the selected
source prefix and construct encoded same-origin paths, never arbitrary URL schemes. Labels and
download filenames must not turn chapter text into markup, a path, or a response header.

Cover the validator's real failure boundaries: wrong run/revision/source/hash, missing/duplicate/drop
sections, non-accepted sections, cross-source references, malformed/missing/failed checks, and all-drop.
Extend the existing production browser journey to download an actual video/caption, compare the
downloaded bytes with the accepted immutable size/hash, confirm absence before acceptance and
retention of the same files after edit/cancel, and exercise read failure/retry and stale-run response
handling. Keep existing cross-tenant media coverage. Run focused unit/static checks in isolation,
then the final full exact-commit release gate and verified push on PR #24.

Work in the separate integration checkout while the live speech matrix continues on its frozen
checkout. No benchmark journal, deployment, model or worker source is changed by this web-only fix.
Update the manual walkthrough and implementation record to describe the actual downloadable result.
The original manifest remains the durable export contract; only presenting it as the finished
user-facing download was incomplete.
