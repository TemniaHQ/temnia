# Temnia Sprint Plan

**Status:** v0.2, 2026-09-05 — direction set by Rajesh the same day the v0.1 draft was reviewed: **build every editing feature first, because editing is the core of Temnia, then do all the stitching that launches it as a multi-tenant SaaS.** Identity is deferred behind a stubbed scope resolver. This file is the sequence record the PRD header points to; AGENTS.md carries the pointer. Sprint-level detail is revised at each sprint start; the phase order is the commitment. The PRD's inherited `[Sn]` / `[An]` / `[Cn]` tags and its M2–M4 gates are re-baselined to §10 at its next revision.
**Inputs:** [prd.md](prd.md) v1.2 (what the product does), the dated decisions in [AGENTS.md](../AGENTS.md), the 2026-09-05 tech-stack research (three structural decisions accepted the same day, §9), [design/studio-direction.md](design/studio-direction.md) and the exploration-03 prototype, [build-in-public.md](build-in-public.md). The legacy `sprint-plan.md` and `pipeline-implementation-plan.md` were consulted as reference only, per the 2026-09-05 rule; every departure is listed in §8 with its reason.
**Precedence:** the PRD owns *what* and *for whom*; this file owns *sequence*; the tech stack (an S0 deliverable) owns system design; the pipeline architecture and clip-cut architecture (S3–S4 deliverables) own the harness and editorial design. Where this file and the PRD disagree on sequence, this file wins and the PRD is updated.

## 1. How to read this plan

- **A sprint is a scope unit with an exit test, not a calendar block.** The legacy sized two-week sprints for a three-person team and then shipped its first six in three calendar weeks with one founder working AI-assisted. This plan is sized for that team: one founder, coding agents doing most of the implementation. The working assumption is about a week per sprint; the exit test, not the date, decides when a sprint ends. Sequence is the commitment.
- **Three phases after the proof: the editing core, the campaign layer, the SaaS stitching.** Nothing in the stitching phase starts before the editing core is complete and dogfooded. Design partners are onboarded onto a finished editor, not a promising one.
- **Milestones are gates.** Each has entry criteria measured on staging against real long-form sources. If a gate fails, the next phase does not start; the next sprint is the round that fixes it.
- **Scope from the first migration, identity when a second person needs an account.** Every table carries the organization id and forced RLS from the migration that creates it. Until S24, the scope resolver returns a seeded Temnia organization and a seeded dogfood user, and staging sits behind Cloudflare Access. Identity replaces one function and adds the membership tables; it can be pulled forward any time.
- **Build lists are written at sprint start, not here.** This file records goals, exit tests, and the PRD sections each sprint must make true. The per-sprint build list is derived from the PRD when the sprint begins, against the codebase as it exists then, with each choice researched under the 2026-09-05 rule.
- **Harness lanes get their detail when scheduled, never before.** Each lane's spec, sensors, prompts, and gates are written into the pipeline implementation plan in the PR that starts it.

## 2. The evidence this sequence rests on

The legacy repository is the only measured record of building this product, and its numbers set the shape of this plan:

| Fact | Measured | Consequence here |
|---|---|---|
| M0 (upload → proxy → transcript) passed on day 15 | 2026-08-23 | M0 sits after S2, not S3: the platform beneath it is trimmed to what the harness needs |
| M1 measured on day 20 and **failed at 18%**, wrong boundaries in 9 of 14 rejections; the cutting room was redesigned and rebuilt the same day and never re-measured | 2026-08-28 | The core bet is tested at S5 with the minimum beneath it, and nothing widens until it holds (§4 rule 8) |
| 120 commits of platform preceded the first editorial measurement | 2026-08-08 → 08-28 | Tenancy hierarchy, identity, and every SaaS surface move *after* the editing core |
| Segments cost $0.43–1.01 per source-hour, discovery $0.43–0.97; the real cost problem was failure amplification (24 attempts for 4 calls), not model price | 2026-08-30 | The economics layer (S3) ships before any real prompt runs |
| Sentence grid, paragraphs, and lead-in rules were ported to Python bit-for-bit with a permanent parity chain (A1) | 2026-08-31 | S2 re-hosts that chain instead of re-deriving it |
| Positioning leads with the chapter cut; shorts hang off the spine | 2026-09-04 | M1 is the chapter lane; moments follow at S6 |

## 3. Milestones

| Gate | After | Proves | PRD sections that must hold |
|---|---|---|---|
| **M0 — Walking skeleton** | S2 | A real two-hour master goes from browser upload to a playable, duration-verified proxy and a speaker-labelled transcript on staging, with the ledger metered and cross-tenant probes green against two seeded organizations | 3 (scope + RLS), 4 (upload), 5, 6 |
| **M1 — The chapter cut** | S5 | On three fresh sources, Rajesh accepts ≥ 70% of proposed chapters with minor or no boundary change; every surfaced range is grounded and playable; cost per source-hour is within target | 8, 9 (segments lane), 31 |
| **M2 — The editing core** | S20 | Every harness lane in PRD §10 is done per §7; the studio edits, previews, renders, and exports from one versioned spec; Temnia's own content has been produced in Temnia for two consecutive weeks; cost per source-hour is within target for every lane | 7, 9, 10, 11, 12, 4 (source groups), 6 |
| **M3 — Private alpha** | S27 | Three to five design partner agencies sign in to their own organizations, run a client's episode through the finished editor, approve internally and through the branded portal, and export; publishing is by hand | 2, 3 (full hierarchy, authz, audit), 13, 14, 15, 18, 19 |
| **M4 — Beta** | S31 | Design partners run real weekly client work end to end: intake → production → client approval → verified publication, with zero duplicate posts for two consecutive weeks | 16, 17, 20, 21 (collection), 4 (connectors, batch) |
| **M5 — Commercial GA** | S37 | Self-serve signup, billing, entitlements, onboarding, hardening, and reporting are live; margin per client is visible in the product; a cold-start agency reaches its first curated portfolio without support contact | 22, 23, 25, 26, 28 |

## 4. Cross-cutting rules (every sprint)

1. **Anything metered writes to the usage ledger in the sprint it ships.** Cost visibility is never retrofitted (PRD §1.5 rule 6).
2. **Every AI capability ships with tracing, a golden eval, and a recorded cassette in the same sprint.** Prompts change only through the registry; CI runs cassettes, never live models.
3. **Every table carries organization scoping and forced RLS in the migration that creates it, and a probe in the isolation suite.** The suite runs in the PR gate from S0 against two seeded organizations. The organization id always comes from the scope resolver, never from input; until S24 the resolver returns the seeded Temnia organization.
4. **Every sprint ends with a recorded demo run on staging against a real long-form source.** Not a toy fixture — the silent-truncation bug in the legacy only appeared at two hours.
5. **Every harness lane ships backend and review surface together.** An unverifiable harness is an unfinished harness (§7).
6. **Every session ends with the build-in-public log and two post drafts** ([build-in-public.md](build-in-public.md)); every sprint ends with a recap post carrying the sprint's real numbers.
7. **Legacy is reference only.** Each sprint's build list is researched against today's options; a legacy incident is cited as evidence, never a legacy choice as the answer (AGENTS.md, 2026-09-05).
8. **Do not widen until the bet holds.** If a gate fails, the following sprint is an editorial or engineering round against that gate, not the next feature. The legacy widened for twenty days before measuring.

## 5. Phases and sprints

### Phase 0 — Prove the cut (S0–S5)

The shortest honest path from an empty monorepo to a measured chapter-cut acceptance rate.

| Sprint | Goal | Exit test | PRD |
|---|---|---|---|
| **S0 — Foundation** | Deploy-on-merge to a real environment before any product code, with both languages in one workspace | A commit merged to `main` is live on staging within minutes, and staging is reachable only through Cloudflare Access. `turbo lint typecheck test` is green across the web app and the Python pipeline. A hello-workflow started from a Next.js server action completes on a Python Temporal worker on the VPS. The scope resolver is one function returning the seeded organization and user. Deliverables: the workspace, compose (Postgres + pgvector, Garage, Temporal + UI), Dokploy targets behind a Cloudflare Tunnel, the local gate with per-SHA attestation, AGENTS.md rules. `docs/tech-stack.md` v1.0 was accepted on 2026-09-05 and is the S0 build list's starting point | 26 |
| **S1 — Ingest on the runtime** | Large real-world media flows in reliably and every row is owned by an organization | A real two-hour master uploaded through the browser, resumable across a reload, becomes a duration-verified HLS ladder with I-frame playlists, waveform peaks, thumbnails, audio extract, and shot grid on staging, playable in the two-pane source page through the org-scoped media proxy. `organization` and `user` are created in Better Auth's column shape so S24 is additive. Storage bytes and processing minutes are in the ledger. Stalled and abandoned work is reaped. Cross-tenant probes pass on every table that exists | 3 (organization, user, project, source, RLS), 4 (upload), 5 |
| **S2 — Transcript and substrate** | The word timeline exists and the grid over it is proven identical to the legacy's | Transcription is WhisperX on Modal from the first run (Rajesh, 2026-09-05), behind the provider seam with a deterministic mock. The HLS ladder moves to the same Modal deployment first (NVENC; the VPS laddered a 2.5-hour master at 2.9x realtime, about 46 minutes, in the S1 exit run, and publishing the 3.8 GB ladder to R2 from the VPS took another 21 minutes at eight concurrent puts; Rajesh, 2026-09-06). The publish moves with the ladder. The same source has a speaker-labelled, integer-millisecond transcript with revisions, viewable, searchable, and correctable, with speaker rename and SRT/VTT export. The substrate's coarse and fine renderings are byte-identical to the legacy's on three recorded sources; the eval runner reproduces the legacy scorers bit-for-bit through the re-hosted parity chain. Transcription minutes metered. **→ M0** | 6, 8 (substrate, eval runner) |
| **S3 — Chassis and economics** | Failure is cheap and progress is durable before any real prompt runs | Chaos test: a worker killed mid-run resumes with zero repeated model calls. A deterministic failure is terminal on attempt one; a budget breach completes with partials; every call is in `llm_calls` with seat, models attempted, cache statistics, and cost; family exclusion and failover-halt rules are unit-tested; cassettes run in CI. Seam contracts (JSON Schema from the contracts package) validated from both languages. Deliverable: the pipeline architecture document for this repository | 8 (chassis, economics, LLM seam, seam), 24 (gateway rules) |
| **S4 — The chapter lane** | The coverage lane runs end to end on recorded sources, with no UI | From a recorded source, Director → rough → Reconciler → Cutter → Publisher → independent-family verify → render → file sensors produce chapter files and `chapters.json` that pass the full battery, under budget, with boundary-F1 at or above the legacy baseline and dollars per source-hour recorded on the run. Deliverable: the clip-cut architecture document for this repository | 9 (segments), 5 (program cuts), 12 (file sensors) |
| **S5 — The cutting room** | A human decides every cut in tooling built for the review, and the decisions feed the flywheel | In the studio on staging, a reviewer plays rendered chapters through the media proxy, accepts, rejects with a reason, nudges a shared boundary (both neighbours move, both re-check), restores a drop, and merges adjacent keeps. Every decision is a signal to the run, an audit row, and an automatic fixture. The metrics readout prints acceptance rate and boundary-adjustment magnitude. Playwright opens every interactive surface. **→ M1** measured on three fresh sources | 9 (review), 8 (fixture flywheel), 11 (spec v0 for the cut) |

### Phase 1 — The editing core (S6–S20)

Every editing feature in the PRD, each lane with its review surface, before any SaaS surface. The lane order follows the rule in §6.

| Sprint | Goal | Exit test | PRD |
|---|---|---|---|
| **S6 — The moments lane** | The peak lane on the same chassis, steerable, with its review surface | Moments render 9:16 with burned captions and pass the battery; a per-run strategist brief steers selection and is audited; the cold Reviewer's verdicts inform and never gate; the same review actions and metrics apply. The M1 bar is applied to moments on the same three sources and the result recorded | 9 (moments, steerable discovery) |
| **S7 — Source intelligence and the episode package** | The source is understood, every claim about it is provable, and the editor's evidence surfaces exist | Source map (summary, chapters, topics, entities); extraction of quotes, stories, claims, and Q&A over one cached prefix; the grounding aligner as the provenance gate; embeddings and source Q&A with citations clamped to retrieved chunks and `answerable=false` first-class; the highlights panel; the episode package (show notes, YouTube chapters, titles, pull quotes, guest bios) with every sentence grounded. Exit: a real episode is published to YouTube by hand from the generated chapters and description | 7 |
| **S8 — Edit spec and captions** | One versioned spec that the timeline, transcript, preview, and render all read; captions as the first roster lane | Versioned edit spec with non-contiguous spans; transcript-based cutting with restore; the captions lane with its sensors (chars/sec, line length, sync, shot-change rules) and style presets; Remotion Player preview over the HLS proxy; canvas timeline v1 with keyframe-only filmstrips through MediaBunny; undo, autosave, conflict-safe persistence. Exit: an editor joins a question to its later answer by editing text, previews it captioned, and the server render of the same spec matches the golden fixture | 11, 10 (captions) |
| **S9 — Render tiers and templates** | Renders are reproducible functions of (source, spec, template version) | A TypeScript Temporal worker renders Remotion templates (captions, titles, logo, progress bar, lower thirds, audiograms) from a brand kit v0 of logo, colours, and fonts; tiers: preview, watermarked review, final, archive; render cache keyed by spec hash; file sensors on every output; aspect variants by centre-crop until S14; NLE handoff (FCPXML, EDL, Premiere XML) with the evidence manifest; render queue UI with status, retry, cost. Exit: an FCPXML export opens in a real NLE with the same cuts | 12 |
| **S10 — Tighten** | Rough-cut cleanup inside kept material: silence, filler, tangent, told-twice, smooth cuts | Silences and fillers deleted from the pause map and filler lexicon with the model judging only ambiguous cases; tangent, ramble, false-start, and told-twice trims proposed with a reason and restorable in the studio; jump cuts concealed by alternating punch-in, never across a shot boundary. Sensors: no clipped onsets, pace within a WPM band, max consecutive jump cuts, payoff sentence survives, remaining text sentence-complete. M-style round recorded | 10 (C1), 11 |
| **S11 — Audio enhancement** | Noise, reverb, and loudness per track behind a provider seam, with before/after evidence | −14 LUFS ±1.5, no clipped onsets, speech-band energy preserved, noise-floor drop at target; a real before/after preview per track in the studio; one ledger entry per render; deterministic smooth cuts as template behaviour gated by the jump-cut sensor | 10 (C1), 12 |
| **S12 — Transcription calibration** | Tune WhisperX against the substrate, between lanes and never during one | Model size, VAD, alignment, and diarization settings judged on word-timestamp quality against the sentence grid, diarization quality on the recorded sources, cost, and throughput on Modal; the punctuation-quality check before planning; a hosted provider compared only if the bar is missed; results recorded in AGENTS.md | 6, 10 (C4) |
| **S13 — Mid-roll insertion** | Ranked ad insertion points from the chapter structure | Segment boundaries re-scored for topic completion and distance from narrative peaks; snap to pause and minimum spacing sensors; the review surface ranks and previews each join | 10 (C2) |
| **S14 — Smart reframe** | 16:9 → 9:16 subject tracking and true aspect variants | A crop keyframe path from face and saliency tracks on Modal, never per-frame crops; sensors: subject-in-frame ratio, crop velocity and acceleration caps, no pan across a shot boundary; the manual crop override editor with platform safe zones; 9:16, 1:1, and 16:9 variants inheriting from one base edit; moments leave centre-crop | 10 (C5), 11, 12 |
| **S15 — Multi-track source groups** | A Riverside, Zoom, Squadcast, or Descript export is one source | Per-speaker audio and video tracks plus the mix ingest as one source, aligned to the mix at ingest; diarization binds to track identity; per-speaker mute and per-track cleanup in the studio | 4 (source groups), 11 |
| **S16 — Multicam auto-switching** | A camera cut list from track activity, face presence as fallback | `{time, cam_id}` keyed to word indices; sensors: minimum shot length, no cut mid-word, active speaker on camera ≥ 90%, max time on one camera; review surface with per-cut override | 10 (C6) |
| **S17 — Translation, dubbing groundwork, voice fixes** | Multilingual output through the same templates, with consent as a hard gate | Caption translation with glossary and translation memory, judged by back-translation consistency and the broadcast sensors; a TTS/voice seam with duration-fit ±10%; a consent-gated fix of a misspoken word in the speaker's own voice, refused without a consent row and flagged synthetic in the spec and evidence report; multilingual caption tracks rendered | 10 (C3), 12 |
| **S18 — Trailer and teaser** | Ordered sparse segments with roles, evaluator-weighted | Sensors: no spoiler segment, hook within 3 s, each segment standalone; the evaluator carries most of the weight and its agreement with the human is recorded | 10 (C8) |
| **S19 — Best-take assembly** | Script-to-take alignment for scripted content | `{script_line → take_id, word_range}`; sensors: full script coverage, per-line WER, audio continuity at joins; review surface | 10 (C7) |
| **S20 — Core hardening and dogfood** | The editing core is complete and used for real | Temnia's own content produced in Temnia for two consecutive weeks; every lane's M-style round recorded and §7 satisfied; render-determinism golden fixtures green; export packages (ZIP, manifest, evidence report) so outputs leave the system by hand; cost per source-hour within target for every lane. **→ M2** | 12 (export packages), 31 |

### Phase 2 — The campaign layer (S21–S23)

The non-video deliverables that hang off the chapter spine. Still single-tenant; still harness work.

| Sprint | Goal | Exit test | PRD |
|---|---|---|---|
| **S21 — Brand intelligence** | Brand context in every generation, checked by an evaluator | Brand profile per brand (visual kit, voice, vocabulary, approved and prohibited claims, examples), versioned with approval states; brand packs in context assembly with snapshots and per-item provenance; the brand-check evaluator applied to the episode package and captions. Exit: every generation records which brand snapshot it used | 13 |
| **S22 — Written content studio** | Grounded writing with sentence-level evidence | Tiptap studio generating LinkedIn posts, X threads, and newsletter sections with evidence links to ranges, unsupported-claim flags, hook variants, platform length rules and previews, structured `@` references, brand voice checks. Exit: a post's every sentence plays back to its range | 14 |
| **S23 — Visuals, asset families, library** | Every asset traces to its moment and its context snapshot | Quote cards through Satori; the variant and asset-family model with a lineage panel; the library with filters, search, statuses, versions; cross-source intelligence v1 (client-wide search, twin detection, guest profiles); the Polotno spike concluded and recorded. Exit: from one source, chapters, moments, a post, a thread, and quote cards, each traceable and brand-checked | 15, 7 (cross-source v1) |

### Phase 3 — The SaaS stitching (S24–S37)

Identity, many clients, approval under governance, publication that never duplicates, money, and the front door.

| Sprint | Goal | Exit test | PRD |
|---|---|---|---|
| **S24 — Identity** | The scope resolver reads a session instead of a constant | Better Auth with email and password, sessions, organizations, members, invitations; fail-closed privacy-preserving password recovery; client IP from Cloudflare's header. The seeded organization and user become ordinary rows. Playwright specs provision their own account and organization. Exit: two people work in two organizations on staging and the isolation suite still passes | 2 |
| **S25 — Hierarchy, authorization, audit** | The canonical object model and the permission matrix | Clients → brands → campaigns → projects with the context switcher; the authorization package (role × scope × action, deny-by-default, permission preview); append-only audit log with export; rights and consent tables that block generation, export, and publish. Exit: a contractor scoped to one client sees nothing else through any path; permission preview matches enforcement | 3 |
| **S26 — Review and approval engine** | An approved asset cannot ship as anything but its approved version | Review requests with stages, sequential or parallel, required approvers; version-pinned approvals with material-edit invalidation; proofing on frames, ranges, text selections, image regions; revision-round counting; guest and review links. Exit: an adversarial suite fails to publish a non-approved version | 18, 2 (guest links) |
| **S27 — Client portal and delivery** | Clients approve on their own domain and never see agency internals | White-label portal on a custom hostname through Cloudflare for SaaS; client roles; the source-evidence view; approved-asset library; delivery downloads; signed expiring URLs; watermarked review renders; internal-versus-client comment scoping enforced. Exit: a client approves on their domain from a phone. **→ M3** | 19, 12 (delivery) |
| **S28 — Collaboration, canvas, notifications** | The team works in the same room | Comments, mentions, threads on assets, ranges, and frames; tasks and the activity feed; presence and live cursors; the campaign canvas as a React Flow view over the graph with synchronized table and board views; notification inbox, preferences, digests through the chosen vendor and Resend. Exit: two users co-review one moment live; resolving a note approves nothing | 17, 16 |
| **S29 — Publishing wave 1** | Reliable publication to LinkedIn, X, and YouTube | Nango OAuth and token lifecycle; in-house adapters; destination composer; preflight (approval, format, rights, conflicts); durable idempotent publish with receipts and post-publish verification; per-tenant credentials encrypted; the shared vault. Exit: zero duplicate posts across fifty publishes under forced failures | 20, 25 (secrets) |
| **S30 — Calendar, scheduling, analytics v1** | Time-zone-correct scheduling and the first lineage back to source | Content calendar with production, approval, and publication events; approval-gated scheduling and queues; failure dashboards; metrics collection through the same adapters; lineage v1 (publication → asset → moment → source). Exit: a scheduled post publishes at the right local time and its metrics appear against its source range | 20 (calendar), 21 (collection, lineage) |
| **S31 — Intake connectors and beta hardening** | Everything a weekly client workflow needs to arrive on its own | Import connectors (direct URL, Drive, Dropbox, Zoom, Vimeo) behind one interface; intake and request forms; malware scanning on every untrusted path; batch intake with per-client fairness keys and a truthful queue view. Exit: a design partner runs one full week of client work end to end. **→ M4** | 4 (connectors, batch, scanning) |
| **S32 — Billing and entitlements** | Merchant-of-record payments and an entitlement engine that explains itself | Dodo Payments for plans, lifecycle, webhooks, customer portal; the entitlement engine (seats, clients, source hours, render minutes, pools, rollover, overage) with real-time checks and explain-why-blocked; pre-action estimates; usage dashboards. Exit: a trial organization hits a limit, sees why, upgrades, continues; the ledger reconciles to the invoice; the fair-billing rule holds under a forced retry | 23 |
| **S33 — Recipes and the learning loop** | The weekly show sets itself up | Recipes v1 with one-click project generation; moment-ranking calibration from acceptance data; brand-memory suggestions with approve, reject, expire; per-client voice from approved outputs; the consent surface for voice fixes. Exit: a recurring client is set up once and the next episode arrives as a correctly structured project | 22, 13 (memory) |
| **S34 — Analytics lineage and client reports** | Which source moments performed, per client, in the product | Lineage dashboards with campaign and client views; branded scheduled client reports delivered to the portal; bill-back and profitability v1 over the ledger. Exit: margin per client is visible without a spreadsheet | 21, 19 (reports) |
| **S35 — Hardening** | The service is operable by one person under load and on a bad day | Audit-coverage review; signed-URL and rights-blocking audit; rate limits; load tests; incident runbooks; status page; alerting SLOs; backup restore drill; error tracking, traces, product analytics, uptime; time-boxed support access. Exit: the restore drill passes and every SLO alert fires in a game day | 25, 26, 2 (support access) |
| **S36 — Onboarding, localization, accessibility** | A cold-start agency succeeds without a call | Organization onboarding wizard (first client, brand intake, first recipe, sample project); empty states, tours, help docs; accessibility pass on core flows; localization with RTL verified. Exit: an unassisted new agency reaches its first curated portfolio | 28 |
| **S37 — GA** | Pricing, packaging, and the public front door | Pricing and packaging; trial flow; marketing site aligned to the coverage-lane positioning; self-serve signup. **→ M5** | 28, 26 |

## 6. Lane order in the editing core

The harness lanes (S8, S10–S19) ship on the chassis the two clip lanes proved, each as one sprint, backend and review surface together, and none is called done until §7 holds.

**Ordering rule:** rank by the fraction of quality that is deterministically checkable; break ties by adjacency to machinery already built; place the transcription A/B between lanes, never during one. That rule gives: captions (~90% checkable, S8), tighten (S10), audio enhancement (loudness and onset sensors already exist, S11), transcription calibration (S12), mid-roll (a re-scoring of boundaries, S13), smart reframe (first computer-vision lane, S14), source groups then multicam (S15–S16), translation and voice (S17), trailer (~30% checkable, S18), best-take (fewest partners need it, S19). Each lane expands into its own section of the pipeline implementation plan in the PR that starts it.

## 7. What "done" means for a lane

A lane is done when all of the following hold, and not before:

- Its review surface exists in the studio and every interactive element has Playwright coverage.
- Its golden evals are green through cassettes in CI; its live eval was run before the prompt was promoted.
- An M-style review round on fresh sources has been recorded with the acceptance rate and boundary-adjustment magnitude.
- Cost per source-hour is on every run row and within the target set for the lane.
- Every approve-with-edit writes a fixture; every metered call is in the ledger.

## 8. Departures from the legacy plan

1. **M1 is the chapter cut, not moments.** The legacy's M1 was moment acceptance. Positioning now leads with the coverage lane (2026-09-04), and a chapter partition is the stricter test. Moments follow at S6 and are held to the same bar.
2. **Six sprints to M1 instead of seven two-week sprints.** Tenancy hierarchy moves to S25, identity to S24, source intelligence to S7, and the app shell carries only what review needs. The rebuilt cutting room reads the grid, not extractions, so extraction no longer has to precede the lanes.
3. **The whole editing core ships before any SaaS surface** (Rajesh, 2026-09-05). Every lane in PRD §10, the studio, rendering, source groups, and the dogfood gate come before identity, hierarchy, approvals, the portal, publishing, and billing. This converges with the legacy's 2026-08-31 "harness roster first" priority, but it is Rajesh's decision for his own reason, editing being the core of Temnia, and it goes further: the legacy kept its app work interleaved. The v0.1 draft of this plan had proposed the opposite (roster interleaved after alpha) and was overruled.
4. **Identity is deferred to S24; scope is not** (Rajesh, 2026-09-05). Every table is organization-scoped with forced RLS from its first migration; the scope resolver is a stub returning the seeded Temnia organization and user; staging sits behind Cloudflare Access; `organization` and `user` are created at S1 in Better Auth's column shape. Verified 2026-09-05 against the current Better Auth docs: the CLI diffs an existing database and adds missing tables and columns, and both tables accept custom fields, so S24 is additive. M0 drops PRD §2.
5. **Gates renumbered M0–M5.** M2 is now "the editing core is complete and dogfooded". The PRD's private alpha, beta, and GA become M3, M4, M5. Design partners are onboarded onto a finished editor.
6. **Roster lanes are numbered sprints in Phase 1, not a track interleaved with product work.** The ordering rule (§6) is kept; the interleaving is not.
7. **No cutover phase.** The legacy planned a per-organization flag to migrate from its TypeScript intelligence layer. This repository has no such layer; the harness is the only intelligence path from S3. The PRD's `[B3]` `[B4]` `[Phase D]` items are retired.
8. **Sprint length.** A sprint is a scope unit assumed at roughly a week for one founder working AI-assisted, not two weeks for a three-person team. The legacy's measured velocity (six nominal sprints in three weeks) is the basis. Thirty-eight sprints at that pace is about nine months to GA; the sequence, not the calendar, is the commitment.
9. **Written on the three stack decisions accepted 2026-09-05** (§9, AGENTS.md): one durable runtime, media in Python, one schema owner. Had any gone the other way, S1's ingest would have been a TypeScript worker on the same runtime, S3 would have added a second migration tool, and S0's compose a second job system; the sequence would not have changed.
10. **The tech-stack document was written after this plan** at Rajesh's request, with every slot researched under the 2026-09-05 rule, and accepted the same day as v1.0. S0 builds from it rather than producing it.
11. **Transcription is WhisperX on Modal from S2** (Rajesh, 2026-09-05, on reviewing the tech-stack research). The PRD's Deepgram-primary line and the legacy's hosted-first order are retired; S12 becomes a calibration round rather than an ownership A/B, and a hosted adapter is built only as fallback. PRD §6 is updated at its next revision.

## 9. Open decisions this plan depends on

| Decision | Decided at | Default assumed here |
|---|---|---|
| One durable runtime (Temporal, polyglot workers) versus Temporal plus Trigger.dev | **Decided 2026-09-05** (AGENTS.md) | One runtime |
| Media and ingest in Python versus a TypeScript worker | **Decided 2026-09-05** (AGENTS.md) | Python |
| One DDL owner (Drizzle, policies declared on the table) versus Alembic for the pipeline schema | **Decided 2026-09-05** (AGENTS.md) | One owner; Drizzle pinned to 0.45 until Better Auth supports 1.0 |
| The three fresh sources for M1 (never seen by any prompt or fixture) | S4 | To be chosen by Rajesh; hour-plus interviews or podcasts with two or more speakers |
| Review-signal shape (long-open workflow versus complete-at-ready plus a finalize workflow) | top of S5 | Complete-at-ready with signal-with-start finalize; pinned versioning makes either workable |
| Transcription ownership | **Decided 2026-09-05** (AGENTS.md): WhisperX on Modal from S2 | S12 calibrates the settings |
| Design studio build-versus-buy (Polotno spike) | S23 | Spike, then decide |
| Pulling identity forward | any time a second person needs an account | Stays at S24 |
| Temporal hosting at first external users | S27 | Self-hosted through M3; Temporal Cloud Essentials evaluated at M3 |
| Notification vendor (Knock versus Novu) | S28 | Novu self-hosted unless complexity outruns ops appetite |
| Remotion company licence | when total headcount reaches four | Note only |

## 10. Legacy tag map

The PRD's inherited tags refer to the legacy sprint plan and pipeline implementation plan. This table is the re-baseline; the PRD is retagged from it at its next revision.

| Legacy tag | This plan |
|---|---|
| S0, S1 (platform, tenancy, sign-in) | S0 (platform), S1 (scope + RLS minimum), S24 (identity), S25 (hierarchy, authz, audit) |
| S2, S3 (ingest, transcription) | S1, S2 |
| A1, A3 (eval parity, substrate) | S2 |
| S4, A2 (harness, chassis, economics) | S3 |
| S5 (source intelligence) | S7 |
| S6, B1 (segments lane, cutting room) | S4, S5 |
| S7 (steerable discovery, brand) | S6 (brief), S21 (brand) |
| B2 (moments lane) | S6 |
| B3, B4, Phase D (cutover) | retired (§8.7) |
| S8, C3 captions | S8 |
| S9 (render tiers, NLE handoff) | S9 |
| S10 (source groups, reframe, multicam surfaces, crop override, per-speaker mute) | S14 (reframe, crop override), S15 (source groups, mute), S16 (multicam) |
| S11 (episode package, written studio) | S7 (package), S22 (written studio) |
| S12 (asset families, quote cards, audiograms, cross-source, Polotno) | S9 (audiograms), S23 |
| S13 (canvas), S14 (collaboration, notifications) | S28 |
| S15 (review and approval, authz package, guest links) | S25 (authz), S26 |
| S16 (portal, connectors, intake, scanning, batch) | S27 (portal), S31 (connectors, intake, scanning, batch) |
| S17 (publishing, Nango, rights blocking, secrets) | S29 (publishing, secrets), S25 (rights tables) |
| S18 (delivery, calendar, export packages, codecs) | S27 (delivery), S20 (export packages), S30 (calendar), post-GA (codec ladders) |
| S19 (billing) | S32 |
| S20 (analytics) | S30 (collection, lineage v1), S34 (dashboards, reports) |
| S21 (recipes, brand memory, consent surface) | S33; consent gate itself at S17 |
| S22, S23, S24 (hardening, onboarding, GA) | S35, S36, S37 |
| C1 (tighten, audio enhancement) | S10, S11 |
| C2 (mid-roll) | S13 |
| C3 (translation, dubbing, voice fixes) | S17 |
| C4 (transcription A/B) | S12 (calibration; ownership decided 2026-09-05) |
| C5 (reframe) | S14 |
| C6 (multicam) | S16 |
| C7 (best-take) | S19 |
| C8 (trailer) | S18 |
| PRD gates M2, M3, M4 | M3, M4, M5 (M2 is now the editing core) |
