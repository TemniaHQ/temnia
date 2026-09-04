# Temnia: a cutting room built around the whole source

**Date:** 2026-09-05

**Status:** rejected in user review on 2026-09-05. Retained as exploration 01; see `studio-direction.md` and `temnia-studio.html` for exploration 02. Neither proposal revises the PRD.

**Basis:** all 32 sections of `docs/prd.md`, the dated decisions in `AGENTS.md`, and the user's request for an OS-like editing workspace.

**Companion:** `temnia-cutting-room.html`, an interactive local concept with fictional content and illustrative footage.

## The product this interface must serve

Temnia starts with an existing master and takes it through understanding, chapter coverage, editorial decisions, production, review, client approval, delivery, publication, and measurement. An agency operates that loop for multiple clients. A short clip is one output of that system.

The main objects are the original source, its grounded ranges, chapter and drop decisions, derived edit specifications, asset families, and version-specific reviews and publications. The interface must make their relationships understandable. Its distinctive visual element should therefore be the source and its chapter spine.

The binding product rules become visible interactions:

- Every second of original source time is accounted for by the coverage plan; deliberate drops remain inspectable and restorable.
- A shared chapter cut is one editable object with two neighbours. Changing it previews the impact on both, then checks both.
- A moment can cross a chapter boundary or assemble several grounded spans. The chapter spine is an organizing structure, not a restriction that every moment must fit inside one chapter.
- Any quotation, claim, chapter, or asset can open its original evidence range.
- An AI proposal is distinguishable from an accepted human decision. Confidence and quality flags inform review; they are not approval and are not predictions of virality.
- An approval identifies an exact asset version. New drafts retain the history and trigger review according to policy; delivery independently verifies the approved version.
- Work always has an explicit client scope. Internal evidence, notes, costs, and administration do not leak into a client portal.
- Spend and rights checks happen where the user is about to act. Failures preserve useful previous results.

## Spatial model

Use one project room, with a small client/project capsule, a floating mode dock, and contextual tools attached to the selected work. Give most of the screen to source material. The workspace has a stable centre of attention: selected asset, source range, playhead, and version.

The spatial hierarchy has three scales:

1. **Agency desk:** work grouped by client and next required action. Resume a project, inspect intake, or find an approval blockage. Source contact sheets and small production queues give this screen its character. Financial and administrative details open through the agency space.
2. **Episode map:** the source becomes a horizontal chapter spine. Kept ranges and deliberate drops are distinguishable. Moments and deliverables branch into asset families. Connection lines appear primarily for the selected family.
3. **Cutting bench:** open a range into precise video, transcript, waveform, and timeline controls. The full map recedes while the selected chapter and neighbour remain identified.

The map is deterministically arranged. Manual repositioning may organize asset-family presentation but cannot change sequence, coverage, approval, or lineage. Chapter order follows source time. A user never has to wire a model-processing pipeline to edit an episode.

Use semantic zoom: collapsed chapter landmarks at source scale, range labels and asset families at editorial scale, precision tracks at cutting scale. Do not enlarge one undifferentiated graph and expect it to become a timeline. The precision timeline and the graph are distinct views over shared state.

An expanded short chapter needs a visible indication that the overview is schematic. Exact timestamps remain available. The production timeline must preserve its real time scale.

## The five work modes

| Mode | Dominant surface | Primary decision | Persistent context |
|---|---|---|---|
| Map | Source spine, chapters, drops, selected asset family | What belongs in this episode and what should it produce? | Project, source, selected chapter/range |
| Edit | Player, transcript, precision tracks, local tool tray | Where does this cut land and how should this asset look and sound? | Source/asset playhead, selection, draft version |
| Review | Review player, source evidence, relevant checks, decision controls | Do I accept this proposal or approve this version? | Exact version and evidence range |
| Feedback | Frame/range-anchored notes beside the affected media | What needs to change, and has the note been addressed? | Note's original version and source anchor |
| Deliver | Versioned package, destination composer, approval and rights preflight | Which approved assets go where? | Approved snapshot and destination |

The five modes are lenses on the same work. Switching modes should not reset the selected object or ask the user to find it again. Remember zoom and local panel arrangements per user. Show the active mode's name continuously and preserve a visible source/asset location.

Do not equate a mode with a permission. A contractor may have Edit access for one project. A strategist may enter Review without permission to approve on behalf of a client. Server authorization remains independent of mode visibility.

## Screen details

### Map: the episode's chapter spine

The master has a compact source thumbnail, duration, track count, transcript state, and language. The continuous chapter spine sits underneath it. Titles describe the editorial argument. Drops use a hatch and an explicit reason; colour alone never communicates exclusion.

Selecting a chapter emphasizes the corresponding range and its connected family. A quote card shows the quotation; a written asset shows text; a video shows footage and its aspect variants. Their visual forms help distinguish the work. An asset's review status is separate from its provenance link.

A strategist can open the durable episode brief and choose when to plan. Before a metered rerun, show the scope, estimate, available budget, and what will be reused. Keep the previous committed plan visible if planning fails. Partial completion shows what exists and the exact action needed to continue; no invented ETA.

Provide synchronized list and board views for people who need to scan many assets or use a keyboard. Project-wide status should not require panning through every branch.

### Edit: a tool for the boundary problem

The default bench puts the player beside the transcript, consistent with the PRD's need to keep playback available during review. The difference is how the bench opens from the selected range and how the shared cut becomes its own editing surface.

Opening a chapter boundary displays:

- The end of the outgoing chapter and the beginning of the incoming chapter.
- Waveforms and the shared silence gap, with a single cut control.
- The same timestamp on both adjacent edges.
- A short playback loop across the join.
- A preview of the changed words or silence, checks on both neighbours, and the affected derived outputs.

Sentence snapping is the default semantic edit. A deliberate precision action enables frame-accurate adjustment where allowed by the editing spec. Moving a control previews; applying it creates a version. Rejecting a suggestion restores the last committed state. Old versions remain inspectable.

Original source time and assembled output time must be visibly different. For a non-contiguous moment, show each retained span and each join in both transcript and timeline. Do not silently let transcript deletion modify unrelated source words. A transcript correction creates a transcript revision; an edit decision creates an edit-spec version.

Keep captions, crop/reframe, audio, multicam, and tightening tools in a contextual tray. Detailed controls replace the local tool surface, not the whole workspace. A reframe view has a crop rectangle and platform safe zones. Audio has a real before/after preview per aligned track. A synthetic voice correction has visible consent and range markers. All these proposals are restorable.

Trimmed silence belongs to neither emitted neighbouring clip. Preserve its accounting in original-source coverage/drop data instead of disguising it as an overlap or an unexplained missing range.

### Review: show evidence close to the decision

Use the render under review, not just the edit spec. Put source provenance, boundary flags, caption sync, sound, brand wording, and any rights blocker near the decision. Expand failures into actual evidence. Avoid a large composite score that obscures the reason for a flag.

Early curation supports accept, shortlist, reject-with-reason, restore-drop, merge-adjacent-keeps, and boundary adjustment. Formal internal/client approval is a later action on a rendered asset version. These are different decisions even when they share the Review mode.

Offer compare-with-previous and preview-source-context without losing the pending decision. Approval buttons name the version. A stale version cannot accept an old confirmation. Editing an approved item branches a new draft and identifies which dependent renders and approvals need attention.

### Feedback: turn a note into an editing destination

A note carries its asset version, frame or range, author, visibility scope, and resolution state. Selecting it seeks the review copy to the actual anchor and offers the relevant editing action. Unresolved notes can be scanned as a focused queue.

Keep original anchors when the draft changes. Map an anchor into the new edit where possible; show an explicit stale/unmapped state when the span has been removed. Do not pretend an old comment was written on the latest version.

Resolving a note does not approve an asset. Reviewer preferences may become brand-memory suggestions, with their evidence, client scope, and accept/reject/expire controls. A note never silently changes future editing policy.

### Deliver: an identifiable approved edition

Present the deliverable set as an edition, with each asset's type, version, approval, and destination. The package can contain chapter videos, sidecar captions, moment variants, written content, quote cards, chapter timestamps, evidence, and NLE handoff files.

Each asset has its own approval record; a moment's approval does not approve a whole episode package. Blockers remain readable before the action, including missing rights, invalid format, missing approval, stale version, unavailable account, and budget/entitlement limits where applicable.

Open a destination-specific composer for copy, media, thumbnails, and other supported fields. Scheduling must name the timezone. Actual publication follows the durable publishing service; show the remote receipt and verified result, and distinguish retryable failures from missing user action.

### Client review: its own simple surface

Use the agency's brand, a large review player, a version label, notes, original context, and approve/request-changes controls. Clients should not need to learn the chapter canvas or agency dock.

Approvers and viewers have distinct capabilities. Internal-only notes, costs, AI routing, and team operations are excluded by the server's scope. Signed guest links grant only their explicit actions. This surface becomes the basis for review on mobile; a small screen does not need a compressed full editing desk.

## Visual and motion direction

The palette is a dark mineral work surface (`#151C20`), slate glass (`#2B363A`), soft white (`#EDF0E8`), pale sage for selection (`#C5DCC5`), warm amber for a cut or unresolved decision (`#EBBB80`), and a restrained blue for relationships (`#ADCBDC`). Source media provides most of the visual variation.

The prototype pairs a condensed editorial face for screen identity with DM Sans for controls and IBM Plex Mono for source time. The final type choices remain reviewable. Numbers retain tabular spacing; dense editing controls prioritize legibility.

Use glass on floating navigation and temporary control islands. Footage, transcript text, and inspector evidence sit on stable opaque surfaces. Large decorative blur fields would interfere with visual assessment of video.

The dock uses rounded glass icons with an expanding text label. The selected mode keeps its label. Hover and keyboard focus reveal the other labels. A short spring and soft connecting bridge suggest liquid motion between capsules, while icons and text remain crisp. The concept approximates that effect with CSS; a production motion pass would refine its geometry.

Keep the active target predictable: avoid icons moving away from the pointer, large hover hit-area changes, or a dock that hides every label on touch. Offer persistent labels and a low-motion preference. Respect the system's reduced-motion setting. No elastic or gooey motion should affect the playhead, waveform, trim handle, or footage.

Mode changes should carry the selected asset into the next arrangement. They may use a short shared-element transition, but playback and keyboard input must not wait for it. A command menu provides direct navigation; it supplements visible controls.

## How the rest of the PRD fits

| PRD scope | Interface home |
|---|---|
| Identity, onboarding, first client/brand/recipe | Setup, then agency desk; return directly to pending work after authentication |
| Tenancy, roles, permissions, audit | Client/project capsule and agency settings; permission preview in member assignment |
| Uploads, imports, source groups, scanning, queues | Intake desk with resumable items and truthful lifecycle states |
| Proxy media, transcription, speaker naming | Source detail and source bench; separate media/transcription failure states |
| Source analysis, Q&A, extraction, semantic search | Source map and evidence search; unanswerable is a valid result |
| Chapters, drops, moments, editorial harness | Map, Edit, and Review; bounded proposals with reasons and action costs |
| Tightening, audio, captions, translation, reframe, multicam, takes, teasers | Tools specific to the selected edit; review surfaces appropriate to each proposal |
| Rendering, caches, exports, NLE handoff | Render queue and Deliver; preview/render lineage and sensor failures |
| Brand kits, voice, claims, memory | Brand space and contextual checks in the studios |
| Written studio, quote cards, asset families | Typed branch assets opening purpose-built text or visual surfaces |
| Collaboration, notifications, tasks | Anchored notes, presence, assigned work, and a compact inbox |
| Approval stages and client portal | Review plus a separate branded client surface |
| Publishing, scheduling, metrics | Deliver and agency calendar/performance spaces; lineage back to source |
| Recipes and learning | Recipe setup and reviewed memory; automation builder remains post-GA |
| Usage, billing, entitlements | Action estimates and blockers; client/agency usage in management spaces |
| Governance, privacy, support access | Administrative policy surfaces and contextual rights/consent checks |
| Public API, enterprise, deeper analytics, mobile | Reserved information architecture; not presented as shipped v1 features |

Post-GA scope remains post-GA. Recording, ungrounded generative B-roll, unsupported virality predictions, and free-running agent configuration do not gain a place in the interface.

## What to build first

First build the source-to-chapter loop: a master, a grounded transcript, a coverage plan, a shared-boundary editor, source playback, and accept/reject/restore/merge decisions. That is the product's hardest differentiating interaction. Connect one real moment and one written deliverable to prove the lineage model.

Then prove one complete internal-review → client-review → approved-delivery journey. Introduce wider graph navigation, asset families, multiple studios, recipes, and operations once those core interactions work with real agency material.

The PRD already proposes React Flow for the campaign canvas and a canvas-drawn precision timeline. Keep their responsibilities separate. Store graph facts, edit specifications, approvals, and job states in the domain services; the screen renders and requests changes. Mode, camera position, and temporary panels are presentation state. Navigation, glass styling, and animation must not become the source of business rules.

## Proposed changes to confirm before implementation

PRD §5 names a two-pane source workspace. This concept keeps the useful player/transcript pairing in Edit and Review, but makes it a focused bench opened from a spatial project room. PRD §16 describes sources → moments → assets → reviews. The proposed public hierarchy adds the coverage chapter spine first, while preserving actual range-based lineage, including moments that cross chapters and non-contiguous assemblies. Neither change has been written into the PRD.

Validate the design with fresh users doing concrete tasks: restore a proposed drop, move one shared cut, explain why a moment was selected, address a note from an old version, and deliver only the approved version. Measure whether they complete those tasks without help, how long they search for context, accidental cut changes, and unnecessary mode switching. A distinctive visual treatment is worthwhile only if these operations become clear.

## Prototype boundaries

The HTML demonstrates five work modes, the simplified client review surface, local version/approval transitions, a shared-boundary control, feedback resolution, source-evidence dialogs, and the glass dock. Fictional source: 42:18, initially six kept chapters plus an 18-second proposed drop. Its player is an illustration with a simulated playhead, not actual video. Sensor, rights, and brand-check rows are sample states. The chapter overview expands short ranges for legibility.

The active cutting bench is a single chapter-04/05 example, and the demo uses one shared draft counter to demonstrate invalidation. Production must version each relevant spec and asset independently and calculate which descendants are affected. The prototype has no graph pan/zoom, real AI, real media processing, backend, durable storage, realtime collaboration, export rendering, or publishing. Reloading resets the local session.
