# Temnia Studio — exploration 02

**Date:** 2026-09-05

**Status:** a new proposal after the first direction was rejected. Product requirements remain in the PRD; this document does not amend them.

**Open:** `temnia-studio.html` alongside `studio.css`, `studio.js`, and `assets/studio-source.png`.

## The central change

Put the media at the centre of the room. The default experience is a cinematic monitor with a chapter in focus, a floating transcript lens, and a compact filmstrip of the complete episode underneath it. The selected chapter is substantial enough to work on, while the whole source remains within reach.

Separate **work mode** from **editing tool**. The upper control chooses Edit, Review, or Feedback. The glass dock opens Sources, Chapters, Transcript, Captions, Sound, Brand, and Deliver. A person can understand what kind of work they are doing without confusing it with which tool is currently open.

## Two views of the same room

**Stage** is for sustained attention. The source monitor stays in place while the contextual lens changes from transcript, to review evidence, to anchored feedback. A focus control expands the monitor. The chapter reel navigates source ranges without replacing the workspace.

**Canvas** is for relationships. Original source leads into chapter coverage, which connects to a moment, a quote card, and review. Its nodes can be dragged, moved with the keyboard, panned, and zoomed. Fit brings all current nodes into view. Positions are presentation state and do not alter chapter order or lineage.

The reel uses contact-sheet thumbnails with exact source ranges, rather than pretending to be a frame-accurate time ruler. The original-source scrubber has a continuous time scale. Precision editing opens a separate boundary lens.

## The visual language

The stage sits on an ink-dark floor with a restrained violet atmosphere. The footage supplies the dominant colour and detail. Frosted plum surfaces give tools visible depth. Warm peach accents identify a boundary or a note; lavender identifies a selected control.

The glass dock has rounded, tinted tool bodies, reflected highlights, expanding desktop labels, and a liquid connection between neighbouring controls. Text and icon strokes remain sharp because the liquid filter applies only to the connecting layer. Motion is disabled for reduced-motion preferences. Small screens use labelled tool popovers and a compact dock.

The transcript lens floats beside the monitor with clear separation from playback controls. Controls open near the work instead of taking over a permanent navigation sidebar. The source card has a small layered-film treatment to distinguish the immutable master from an editable output.

The photo is a generated fictional architect in a workshop. It establishes the intended relationship between real-looking source material and interface chrome; it is not customer footage or a real recording. The original generated file remains untouched; a copy is in this design directory.

## Interactions included

- Chapter selection updates the monitor label, source range, playhead, transcript context, and reel selection.
- Transcript phrases seek to their sample source position.
- Caption style, text, visibility, and size preview on the monitor. Closing the tool cancels the preview; saving creates a new draft.
- Landscape, portrait, and square framing can be previewed with a safe-area overlay. These previews are not saved edit variants.
- The shared-boundary lens adjusts both chapter 04's end and chapter 05's start together. The cut is applied as a versioned change.
- Restoring a proposed drop updates the coverage count and preserves complete source accounting.
- Review presents source evidence and illustrative quality checks. Internal approval precedes client approval; a manifest remains blocked until the selected chapter's current version is approved.
- Editing after approval creates a new draft and blocks delivery again. The manifest uses the approved source aspect, not an unsaved crop preview.
- Feedback keeps its author, original version, source position, visibility scope, and resolution state. Caption and boundary notes lead to their respective tools. Resolving a note does not approve an asset.
- Source inspection, project context, brand controls, a command menu, and a client-review preview are included.

## Product implications

The coverage lane remains the organizing editorial structure. The opening screen emphasizes the actual act of editing, and the canvas exposes the relationships when they are needed. Moments remain grounded ranges connected to that structure; they are not the headline of the product.

This direction still needs an agency desk for intake and cross-project work, purpose-built written and visual studios, a calendar, real publishing status, usage/budget controls, and permissions. Those surfaces should use the same context model without crowding the cutting room. None of the PRD's governance, economics, or provenance requirements are removed by the quieter chrome.

## What this prototype does not prove

The source is a still image and playback is a simulated playhead without audio. Waveforms, transcription, evidence, sensor results, source tracks, and rights states are fictional examples. The canvas is implemented locally to explore interactions; it is not a production React Flow integration. There is no ingest, renderer, AI editorial pipeline, realtime collaboration, persistence across reloads, or publishing.

The approval example uses a local draft counter and a selected-chapter scope check. Production requires immutable per-asset specifications, render hashes, approvals, and an independently enforced publication gate. The client surface is shown as a preview dialog; it must be served with separate client authorization in the product.

Desktop geometry, smaller-screen reflow, cancellation, canvas movement/fit, and version transitions can be inspected here. Whether editors find the spatial composition faster and clearer still requires testing with real source material.
