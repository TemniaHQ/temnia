# Temnia Tech Stack

**Status:** v1.1, 2026-09-06 (S0 build revisions, listed in §14); v1.0 **accepted by Rajesh on 2026-09-05** — every slot researched on this date under the "legacy is reference only" rule (AGENTS.md, 2026-09-05). The three structural decisions (one runtime, media in Python, one schema owner), the icon and transcription slots, and the no-default-vendor rule are recorded as dated decisions in AGENTS.md; every other slot here is the default for its sprint until a revisit trigger (§13) fires or the sprint's build list re-researches it. Version numbers are as published on the research date; they are pins to start from, not commitments. Changes to this file are made in the PR that acts on them, with what was compared.
**Inputs:** [prd.md](prd.md) v1.2, [sprint-plan.md](sprint-plan.md) v0.2, the dated decisions in [AGENTS.md](../AGENTS.md), and the session record in [log/2026-09-05.md](log/2026-09-05.md). The legacy `tech-stack.md` was read once for the list of slots and never for the answers; where a legacy incident is evidence it is cited as an incident.
**Precedence:** the PRD owns *what*; the sprint plan owns *sequence*; this file owns *system design*. Where a later architecture document (pipeline, clip-cut) disagrees on its own subject, it wins and this file is updated.

## 0. What changed, and why

Twenty-five slots differ from the legacy stack. Rows 1–3 were decided with the runtime; rows 18 and 27 were decided by Rajesh on review; row 8 records a proposal he reversed; the rest were accepted with the document on 2026-09-05.

| # | Slot | Legacy | Now | Why (evidence dated 2026-09-05) |
|---|---|---|---|---|
| 1 | Durable runtime | Trigger.dev for app jobs + Temporal planned for the harness | **Temporal only**, polyglot workers | Decided. Worker Versioning GA (Mar 2026), task-queue fairness GA (May 2026); Trigger.dev runs Python only as scripts inside TS tasks and its self-host drops checkpointed waits |
| 2 | Media language | TypeScript on Trigger.dev | **Python** on Temporal | Decided. One package owns every ffmpeg string; PyAV 18 wheels bundle ffmpeg 8.1.2 |
| 3 | Schema ownership | Drizzle + Alembic, joined by views | **Drizzle owns all DDL**; Python is DML-only with generated models | Decided. drizzle-kit emits policies and forced RLS from `pgPolicy` |
| 4 | Local object store | MinIO | **Garage 2.3** | MinIO's community repository was archived on 2026-04-25 after entering maintenance mode in Dec 2025; Garage is a single binary with auto single-node setup, and implements multipart, presigned URLs, CORS, and the abort-incomplete-multipart lifecycle rule |
| 5 | Staging database | Neon (Singapore) | **Postgres on the VPS**; Neon for production only | The legacy moved staging to Neon so Trigger.dev cloud workers could reach it. With every worker on the VPS that reason is gone, and Neon has no Mumbai region, so staging would pay the Mumbai–Singapore round trip for nothing |
| 6 | Origin exposure | iptables origin lock, two ACME resolvers, wildcard cert | **Cloudflare Tunnel** into Traefik | No open ports, no origin-IP lock to maintain, no DNS-01 wildcard. Dokploy documents the setup; Cloudflare terminates TLS |
| 7 | Secrets | Infisical (self-hosted) | **1Password** service accounts for humans, local `.env`, and CI; Dokploy env at runtime | Rajesh already runs 1Password; `op run` and the official Actions step cover local and CI. Infisical would be one more container. Per-tenant credential encryption at S29 is application-level either way |
| 8 | Icons | Hugeicons | **Hugeicons** (kept) | v0.1 proposed Lucide on registry friction. Rajesh kept Hugeicons on 2026-09-05 because the icons look better. **S0 correction:** shadcn's registry maps icons for six libraries including Hugeicons (`r/icons/index.json`), so `iconLibrary: hugeicons` in `components.json` makes every added component import `@hugeicons/react` directly; no swap step exists |
| 9 | Model gateway | OpenRouter, fixed | **Decided by the S3 transport probe**; Vercel AI Gateway first candidate, OpenRouter second | Vercel: zero token markup, per-request ZDR free on Pro, both OpenAI-compatible and Anthropic Messages-compatible endpoints, and image and video model types in the catalogue. OpenRouter: 5.5% credit fee, free ZDR, three documented outages in eight months, no SLA. Catalogue coverage in §9 |
| 10 | Python S3 client | boto3 | **obstore** | Rust-backed, 2.8× aioboto3 throughput, automatic multipart, presign, R2 documented, sync and async |
| 11 | Quote-card renderer | Satori | **Takumi** (S23) | Satori-compatible API, Rust, 2–10× faster, gradients and shadows |
| 12 | Drizzle line | latest | **0.45.x pinned** | 1.0 is at rc.4 with no stable; Better Auth's adapter has open issues against it |
| 13 | Python in Turborepo | not in the workspace | **package.json shim on stable 2.10.12**; native uv workspaces when the flag ships stable | Turborepo's uv-workspace support exists only in 2.10.13-canary.1 as of today (confirmed at S0 from the release notes: the uv metadata, virtualenv, and lockfile-scoping commits all land in that canary) |
| 14 | TypeScript | 5.x | **7.0** (native compiler) via Next's `experimental.useTypeScriptCli` | Stable since 2026-07-08, 8–12× faster full builds; Next 16.3 supports it |
| 15 | React Compiler | off | **on** (`reactCompiler: true`) | Compiler 1.0 stable since Oct 2025; Next 16 promotes the option to stable but leaves it off by default |
| 16 | pnpm | 10 | **12.3.4** (S0) | The `latest` tag was already 12.3.4 on 2026-09-05 (12.0.0 shipped 2026-08-26, four patch releases since), so the revisit trigger fired at S0. Drop-in as promised; two S0 findings: `allowBuilds` replaces `onlyBuiltDependencies`, and pnpm 10 cannot self-switch *up* to the native 12 binary (ENOEXEC), so 12 is installed with the official installer |
| 17 | Node | 24 | **24 LTS** now; 26 at its LTS in Oct 2026 | 26 ships the Temporal date API natively |
| 18 | Transcription | Deepgram, then AssemblyAI; WhisperX as a later A/B | **WhisperX on Modal from S2** (decided by Rajesh, 2026-09-05) | Independent March 2026 benchmark on podcasts and interviews: WhisperX (large-v3 + pyannote) best on both WER and diarization; AssemblyAI and Deepgram within a point of each other. whisperx 3.8.5 (May 2026), BSD-2; an L4 at about $0.28/hour handles large-v3 with alignment and diarization at roughly twenty audio-hours per GPU-hour. Known wheel issue: one `use_auth_token` path still breaks against pyannote 4.x (m-bain/whisperX#1406), so pin pyannote 3.x or patch. S12 becomes a calibration round, not an ownership A/B |
| 19 | Temporal local dev | `auto-setup` image | **`temporalio/server` + `admin-tools` schema setup** in compose; the CLI's `start-dev` for quick local runs | auto-setup is deprecated upstream |
| 20 | ffmpeg pin | 8.1.x, three places | **Re-examined at S3** | ffmpeg 9.0 shipped 2026-08-04 (9.0.1 on 08-12); PyAV wheels are on 8.1.2; BtbN keeps monthly builds two years, dailies fourteen days |
| 21 | Python | 3.12 | **3.13**; 3.14 once every wheel resolves | PyAV 18.1 ships cp314 and abi3 wheels; the Temporal SDK's 3.14 wheels were not confirmed today; 3.13 is supported to Oct 2029 |
| 22 | Video.js | v10 beta | **v10 beta, pinned** | Still beta.32 (2026-08-26); the mid-2026 GA slipped. It is the merger of Vidstack, Media Chrome, and Plyr, so there is no separate maintained alternative |
| 23 | Drawer | Vaul | **Base UI drawer** | Vaul is unmaintained; shadcn's base drawer moved to Base UI |
| 24 | Rendering licence | Remotion company licence "at four headcount" | **Remotion for Automators**: $0.01 per render, $100 minimum monthly, once the organisation exceeds three people | The licence changed shape; video-editor products fall under the Automators tier |
| 25 | Agent framework | Mastra + AI SDK | **none**; native structured outputs plus local pydantic validation | Decided with the runtime. pydantic-ai 2.0 and Instructor were compared and rejected because a framework owns control flow |
| 26 | Dates | date-fns + @internationalized/date | **Decide at S30** | The Temporal date API reached Stage 4 in March 2026 and ships in Chrome 144+, Firefox 139+, Node 26; Safari partial |
| 27 | Hosted transcription adapters | Deepgram and AssemblyAI at S3 | **Fallback only, built when needed** | With WhisperX primary, a hosted adapter exists for outage cover or throughput bursts; AssemblyAI Universal-3.5 Pro is the candidate on its diarization numbers |

Everything not in this table was checked and kept; the per-slot tables below say what was compared.

## 1. Principles

The PRD's binding principles (§1.5) are the design constraints; the ones that shape slot choices are: build the harness, buy the plumbing; every provider behind an adapter; tenant isolation structural in Postgres; unit economics from day one; no free-running agents. Two repository rules join them: legacy is reference only (AGENTS.md 2026-09-05), and every choice below names what it was compared against.

## 2. Repository and toolchain

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Monorepo | **Turborepo 2.10.12** + pnpm workspaces | Nx, moonrepo | Rajesh's choice; Vercel remote cache is free, self-hosted caches exist. The Python app is a workspace member through a `package.json` whose scripts call `uv run`, with `inputs` pinned to `src`, `tests`, `pyproject.toml`, `uv.lock`, until `experimentalPythonWorkspaces` leaves canary |
| Package manager | **pnpm 12.3.4**, `minimumReleaseAge: 1440`, `allowBuilds` allow-list | pnpm 11, Bun | `latest` was 12 on the research date (§0 row 16). The 24-hour release-age gate stays (supply-chain window) and bit on day one: Playwright 1.63.0 was excluded as too new |
| Node | **24 LTS**, pinned by `devEngines.runtime` with `onFail: download` | 22, 26; nvm/fnm/volta | 24 is Active LTS to 2028; 26 enters LTS in Oct 2026 and gets adopted then. pnpm downloads and runs the pinned Node itself, so no version manager is installed; the same field makes npm refuse to run in the repo, which is intended |
| TypeScript | **7.0.x** | 5.9, 6.x | Native compiler stable 2026-07-08; Next 16.3 runs it during `next build` behind `experimental.useTypeScriptCli`. Biome parses TS independently, so lint is unaffected |
| TS lint/format | **Ultracite 7.10 on Biome 2** | oxlint 1.80 + oxfmt 0.65 | oxlint is faster with more rules; Biome is one binary, one config, and Ultracite's preset carries the strictness. Keep; revisit if type-aware rules matter |
| Python toolchain | **uv 0.12**, **ruff 0.16** (`select = ["ALL"]`), **pyright strict**, **pytest 9.1** | ty (beta 0.0.78), Pyrefly 1.0 | Pyright is the reference implementation; ty is still beta; Pyrefly 1.0 (May 2026) is the challenger to re-check at S12 |
| Python | **3.13** | 3.12, 3.14 | See §0 row 21 |
| TS tests | **Vitest 5.0**, **Playwright 1.62** | Vitest 4.1, Playwright 1.63 | Vitest 5.0.0 shipped 2026-09-03 and S0 is a sprint boundary, so it was adopted; 1.63.0 shipped 2026-09-04 inside the 24-hour release-age window and stays out until it matures |
| Python tests | **pytest 9** with **VCR.py 8 / pytest-recording** cassettes | respx | VCR records httpx; the cassette is the CI contract for every model call |
| Validation | **Zod 4** | Valibot, ArkType, TypeBox | Zod 4 exports JSON Schema natively and has the ecosystem; bundle size is not the constraint here |
| Cross-language contracts | **Zod → JSON Schema → `datamodel-code-generator` → pydantic v2** | hand-mirrored pydantic, TypeSpec, protobuf | One source of truth in `packages/contracts`; Python models are generated in a Turborepo task and CI fails on drift. Maintained (July 2026) |
| Python models for tables | **sqlacodegen 4.0.4** from the migrated database | hand-written pydantic rows, SQLAlchemy reflection at runtime | Same drift-check pattern; SQLAlchemy 2.0.x until 2.1 final (rc1 2026-08-31) |
| Local gate | exact-commit local validation with per-SHA attestation; GitHub Actions for provenance only | hosted CI | The solo-developer trade-off the legacy made stands until a second committer; it is an ops policy, not a legacy code inheritance. Built at S0 as `scripts/local-ci.mjs`: its last stage runs the two deploy images together against the compose Temporal server and drives the hello workflow through Playwright |

## 3. Web application

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Framework | **Next.js 16.3.4**, App Router, standalone output | TanStack Start 1.x, React Router 7 | TanStack Start reached 1.x and is production-viable; Next keeps RSC streaming, the widest ecosystem, and self-host support. Client-first would not buy the studio anything it needs |
| React | **19.2.8** with the compiler on | — | Compiler 1.0 stable; enable in `next.config` |
| Styling | **Tailwind 4.3** | — | CSS-first config, Lightning CSS |
| Components | **shadcn CLI v4 on Base UI 1.8** | Radix variant | Base UI is shadcn's default since July 2026; Radix is not deprecated but new components land on Base UI first. The registry-first rule (AGENTS.md) applies |
| Icons | **Hugeicons** | Lucide | Rajesh's call (§0 row 8); ESM icon imports, swapped into vendored registry components |
| Server state | **TanStack Query 5** | — | Keep |
| Client state | **Zustand 5.0.15** | Jotai | Keep; editor and canvas UI state |
| URL state | **nuqs** | — | Tested against Next 16.3 and TS 7 |
| Forms | **react-hook-form + Zod 4** | TanStack Form, Conform | RHF remains the default with server actions; TanStack Form's type inference is not the constraint here |
| Tables | **TanStack Table + Virtual** | — | Keep |
| Drag and drop | **dnd-kit** | Pragmatic drag and drop | dnd-kit is maintained and the community default; Pragmatic only for file-drop or thousands-of-items scale |
| Command palette | **cmdk 1.1.1** via shadcn Command | — | Last published a year ago; shadcn still depends on it. Watch, do not replace |
| Toasts, drawer | **Sonner**, **Base UI drawer** | Vaul | Vaul is unmaintained (§0 row 23) |
| Animation | **Motion 13** | — | `motion/react`; the glass dock and lens transitions |
| Charts | **Recharts** now, ECharts at S34 | — | Unchanged; first chart surface is S30 |
| i18n | **next-intl** at S36 | Paraglide, Lingui | Re-research at S36 |
| Dates | decide at S30 | Temporal API + polyfill, date-fns 4, @internationalized/date | §0 row 26 |
| Rich text | **Tiptap 3** or **Plate**, decided at S22 | Lexical, BlockNote | Tiptap for a document surface inside a SaaS app; Plate for shadcn-native blocks. Both MIT core; Tiptap's paid cloud extensions are not required |
| Canvas | **React Flow 12.11** at S28 | — | Keep |
| Presence | **Yjs** via **Liveblocks** ($25 Pro) at S28; Hocuspocus as graduation | Loro | Loro is faster with movable trees but the ecosystem is early |

## 4. Media in the browser

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Player | **Video.js v10 React** (beta.32, pinned) with hls.js 1.7 | Vidstack, Media Chrome, Plyr | §0 row 22 |
| Waveform | **peaks.js 4** with server-generated peaks | wavesurfer.js 7 | peaks.js is built for pre-computed peaks over long sources; upstream is slow (our bbc/peaks.js#574 is open). wavesurfer with pre-decoded peaks is the fallback if the S8 timeline needs it |
| Frame access | **MediaBunny 1.55** (MPL) at S8 | — | Active (release 2026-09-04); keyframe-only decode through the I-frame playlists |
| Preview | **Remotion Player** at S8 | — | One composition for preview and render |

## 5. Identity, scope, and access

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Identity | **Better Auth 1.6** at S24 | Auth.js (maintenance mode under the Better Auth team), Clerk, WorkOS AuthKit | Self-hosted, organizations, passkeys, 2FA; `organization` and `user` created at S1 in its column shape so S24 is additive (verified: the CLI diffs existing tables) |
| Scope before identity | one resolver function returning the seeded organization and user | — | Sprint plan §1 |
| Authorization | in-house role × scope × action package at S25; OpenFGA or SpiceDB post-GA | — | Unchanged |
| Staging perimeter | **Cloudflare Access** (free to 50 users) | Basic auth, VPN | Already used for the Dokploy panel; Rajesh-only policy on staging until S24 |
| Enterprise | WorkOS for SSO/SCIM post-GA | — | Re-research when the first enterprise deal appears |

## 6. Data and storage

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Production database | **Neon** (Singapore), separate project per environment | Supabase, PlanetScale Postgres, self-host | Prices fell after the Databricks acquisition (storage ~$0.35/GB-month, minimum removed); PITR and branching; no Mumbai region |
| Staging and dev database | **Postgres 18 with pgvector on the VPS** (Dokploy service) and in compose | Neon | §0 row 5 |
| ORM and migrations | **Drizzle 0.45.x**, `pgPolicy` on every tenant table, `pgTable.withRLS()` | Atlas ($9/dev/month for RLS as code) | Decided; Atlas evaluated and set aside as a third tool for what drizzle-kit already emits |
| Vector search | **pgvector 0.8** (halfvec, iterative scans) | pgvectorscale, Turbopuffer | Keep; pgvectorscale 0.9 is the scale option |
| Full-text | Postgres FTS; Typesense or Meilisearch at scale | — | Unchanged |
| Cache and rate limits | **none at S0** | Upstash Redis | Temporal owns queues; Cloudflare owns edge rate limits; add Redis only when a measured need appears |
| Object storage | **Cloudflare R2** (zero egress, lifecycle rules, abort-incomplete-multipart) | S3, Backblaze B2 | Keep |
| Local object storage | **Garage 2.3** (`--single-node --default-bucket`) | RustFS 1.0.0-rc.5 (Apache 2.0, MinIO-compatible, but per-bucket CORS returns not-implemented on some builds), SeaweedFS 4.x (production-grade, more processes) | §0 row 4; RustFS is the candidate to re-check at its 1.0 |
| Custom domains | **Cloudflare for SaaS** at S27 | Domainee | 100 hostnames included, $0.10 each after |
| Analytics warehouse | ClickHouse post-GA | — | Unchanged |

## 7. Durable runtime

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Runtime | **Temporal server 1.31.x**, self-hosted via Dokploy compose with a local Postgres persistence volume | Hatchet, DBOS, Restate (BSL), Inngest (2 h step cap), Trigger.dev v4 | Decided 2026-09-05; runner-up Hatchet |
| Local | `temporalio/server` + `temporalio/admin-tools` schema setup in compose; `temporal server start-dev` for quick runs | `auto-setup` | §0 row 19 |
| Python worker | **temporalio 1.32** | — | Async activities await subprocesses and heartbeat; cancellation arrives on the heartbeat |
| TypeScript client | **@temporalio/client 1.22** in Next.js, listed in `serverExternalPackages` | HTTP control plane | Temporal is the API: start, query, signal |
| TypeScript worker | at S9 for Remotion; later for publishing adapters | — | Same cluster, own task queue |
| Versioning and fairness | Worker Versioning (pinned workflows) and task-queue fairness keys from S3 | — | Both GA in 2026 |
| Cloud | Temporal Cloud Essentials ($100/month, 1M actions) evaluated at M3 | — | Sprint plan §9 |

## 8. Media pipeline (Python)

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| ffmpeg | **8.1.x** pinned via the mirrored BtbN static build; re-examined at S3 against 9.0.1 | — | §0 row 20; PyAV wheels stay on 8.1.2 |
| Library access | **PyAV 18.1** where a library call beats the CLI (probe, keyframe maps) | ffmpeg-python | The CLI remains for the HLS ladder |
| Object storage client | **obstore** | boto3, aioboto3 | §0 row 10 |
| Peaks | own generator (port of the legacy rules: media-duration span, scaled samples-per-pixel) | audiowaveform (moved to Codeberg, April 2026 release) | Our rules are the value; audiowaveform is a fallback |
| Shot grid | ffprobe scene scores | PySceneDetect | Unchanged |
| Loudness and sensors | ffmpeg `loudnorm`, `blackdetect`, `freezedetect`; pyloudnorm for checks | — | Unchanged |
| Transcription | **WhisperX 3.8.5** (large-v3, wav2vec2 alignment, pyannote diarization) on **Modal**, behind the provider seam with a deterministic mock | AssemblyAI Universal-3.5 Pro, Deepgram Nova-3, ElevenLabs Scribe, Gladia, Parakeet | Decided (§0 row 18). Word timings and speaker ids land in the same canonical transcript JSON; the adapter contract is unchanged, so a hosted fallback slots in without touching the substrate |
| Transcription calibration | model size, VAD, alignment, and diarization settings tuned against the sentence grid at S12 | — | Between lanes, never during one; a hosted provider is compared only if the bar is missed |
| GPU compute | **Modal** | RunPod, Baseten | Keep |
| Voice, dubbing | ElevenLabs at S17 | — | Re-research at S17 |

## 9. AI layer

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Gateway | **decided by the S3 transport probe** (the legacy method: full request shape with ZDR and strict schema against every candidate model) | Vercel AI Gateway, OpenRouter, LiteLLM proxy, Portkey (acquired by Palo Alto Networks, May 2026) | §0 row 9. Vercel's Anthropic Messages-compatible endpoint lets the official `anthropic` SDK be used for Claude seats; LiteLLM is a self-hosted option if both hosted gateways fail the probe |
| Catalogue coverage (checked 2026-09-05) | both gateways cover the seats Temnia has named | — | **Kimi K3** (released 2026-07-16, 1M context, text, image, and video input): on Vercel with ZDR through US providers, and on OpenRouter via fifteen providers. **GLM-5.3** (2026-08-14): on Zhipu's API and OpenRouter; not confirmed on Vercel. **Nano Banana, Nano Banana 2, Nano Banana Pro** image models: on Vercel. **Video generation** (Veo, Kling 3.0, Wan, Grok Imagine, Seedance): on Vercel, with model types `image` and `video` filterable from its models endpoint; OpenRouter lists Seedance 2.5, Seedance 2.0 Mini, and Veo 3.1 Lite. If a needed media model is on neither, **fal.ai** (600+ image and video models, 30–50% cheaper than Replicate) is the specialist router. Sequencing: text seats are probed at S3; image generation is post-GA (PRD §15) and generative B-roll is a non-goal, so media-generation seats are probed when a sprint schedules them; Kimi K3's video input is the first candidate for frame-QC seats at S14 |
| Models | **No default vendor** (Rajesh, 2026-09-05). Every seat gets an ordered pool drawn from at least three model families, probed for transport at S3 and auditioned per seat at S4 on cost-per-correct; the verifier seat is always a different family from the generator (PRD §1.5 rule 8), so production runs at least two vendors by construction. Current-generation candidates on the research date: Anthropic Claude Opus 5 ($5/$25 per MTok) and Sonnet 5 ($2/$10); OpenAI GPT-5.6 Sol ($5/$30), Terra ($2/$12), and Luna ($0.20/$1.20); Google Gemini 3.8 Flash and the 3.x Pro line; Moonshot Kimi K3; Zhipu GLM-5.3 and GLM-5.3-Flash; DeepSeek V4 Pro and V4 Flash; Alibaba Qwen 3.8 Max and Flash; xAI Grok 4.5; MiniMax M3 | Claude Fable 5.1 ($10/$50) | Fable 5.1 is excluded by policy, not preference: it is not served under zero data retention without express authorisation. The legacy's August audition, in which the then-current Opus won both clip passes at $0.316 a run with GPT-5.6 Terra as the cheap challenger and Kimi K3 best on grounding, is evidence about a field that has since moved; it is re-run, not inherited. The gateway's models endpoint is the live catalogue; this list is the audition's starting roster, never a routing table |
| Reasoning controls | per seat, in each provider's own parameters: Anthropic adaptive thinking and `effort`; OpenAI reasoning effort, with Sol Ultra as a high-effort mode; Gemini thinking levels; open-weight models as their hosts expose them | — | Reasoning tokens are billed output, so the audition's cost-per-correct includes them; the legacy found a more capable model can be cheaper because it thinks less to decide |
| Structured output | native strict JSON Schema (portable subset) + local pydantic validation + one bounded repair | Instructor, pydantic-ai 2.0 | Decided with the runtime |
| Embeddings | **Voyage voyage-4** ($0.06/M, 200M free), single provider, 1024 dimensions baked into the migration; `voyage-context-4` as the contained upgrade | OpenAI, Cohere Embed 4, Gemini | Shared embedding space across the 4 family; Voyage is MongoDB-owned |
| Reranking | **Cohere Rerank 4 Pro** ($0.0025/search) at S7 if retrieval needs it | — | Measure first |
| Tracing and prompt registry | **Langfuse Cloud** (Hobby, then Core $29) | self-host v4 (MIT, six containers with ClickHouse), Braintrust, Phoenix (ELv2), Opik | Langfuse v4 GA Aug 2026; ClickHouse acquired Langfuse in Jan 2026 with pricing unchanged. Metadata-only production spans |
| Evals | in-house runner with the legacy parity chain | promptfoo | Unchanged |

## 10. Rendering and visuals

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Composition | **Remotion** on a TypeScript Temporal worker at S9 | ffmpeg-only overlays | §0 row 24 for the licence |
| Static cards | **Takumi** at S23 | Satori | §0 row 11 |
| Design studio | **Polotno** ($249/month) versus **Konva** (MIT), decided by the S23 spike | — | Unchanged |

## 11. Hosting and operations

| Slot | Pick | Compared | Verdict |
|---|---|---|---|
| Host | **Hostinger KVM 8** (Mumbai) with **Dokploy 0.29** | Coolify 4.0 (stable Apr 2026), Kamal 2 | Dokploy is compose-first on Swarm with rolling deploys, which suits three targets (web, pipeline, Temporal stack). Coolify is the credible alternative; the legacy's env-at-deploy and rollback gotchas are recorded incidents, not a reason to switch |
| Edge | **Cloudflare Tunnel** into Traefik; Cloudflare Access on staging | origin lock | §0 row 6 |
| Images | web: `node:24-alpine`, `turbo prune --docker`, standalone Next; pipeline: `python:3.13-slim` + pinned ffmpeg | — | The web image no longer needs ffmpeg |
| Secrets | **1Password** service accounts (`op run`, `load-secrets-action`); Dokploy env at runtime | Infisical, Doppler | §0 row 7 |
| Errors | **Sentry** free tier (5k errors/month) | GlitchTip (self-host, Sentry-protocol) | S35 |
| Product analytics and flags | **PostHog** free tier | — | S35 |
| Logs and traces | decide at S35 | Axiom, Grafana Cloud, Better Stack | Better Stack is the easiest start for one person; OTel either way |
| Uptime | Better Stack at S35 | — | Unchanged |
| Deploys | Dokploy watch paths per target on push to `main`; `production` promoted by fast-forward | GitHub Actions deploy | Unchanged |

## 12. Later-phase vendors (re-researched at their sprint)

| Slot | Default | Compared today | Sprint |
|---|---|---|---|
| Payments (merchant of record) | **Dodo Payments** (India MoR, UPI and RuPay, FEMA-aligned payouts) | Polar (5% + 50¢, $20/month lowers it), Paddle, Lemon Squeezy (post-acquisition drift) | S32 |
| OAuth and connectors | **Nango** ($50/month starter; Elastic License self-host) | — | S29 |
| Notifications | **Novu** self-hosted (MIT) | Knock (SaaS only) | S28 |
| Transactional email | **Resend** + react-email (free 3k/month) | Postmark, SES | S24 |
| Outbound webhooks | Svix | — | post-GA |
| Compliance | Vanta | — | post-GA |

## 13. Revisit triggers

- Turborepo ships `experimentalPythonWorkspaces` in a stable release: drop the package.json shim.
- Better Auth supports Drizzle 1.0: move Drizzle to 1.0 (migration folder v3, relations v2).
- pnpm `latest` becomes 12: switch.
- Node 26 LTS (Oct 2026): switch; re-decide the date library.
- Video.js v10 GA: unpin.
- RustFS 1.0 with per-bucket CORS: re-check against Garage.
- Temporal SDK publishes cp314 wheels and `uv sync` resolves everything: Python 3.14.
- ty stable, or Pyrefly earns it: re-check the type checker.
- The S3 transport probe: the gateway decision, recorded in AGENTS.md.
- The S12 calibration round: WhisperX settings, recorded in AGENTS.md; a hosted fallback adapter only if the bar is missed.
- whisperX ships a wheel without the `use_auth_token` path: unpin pyannote.

## 14. Revisions

| Date | Change | Basis |
|---|---|---|
| 2026-09-06 (S0) | pnpm 11 → **12.3.4**; `allowBuilds` replaces `onlyBuiltDependencies` | §13 trigger fired: `latest` was 12 on the research date. Installer, not `npm i -g`, because pnpm 10 cannot self-switch to the native binary |
| 2026-09-06 (S0) | Vitest 4.1 → **5.0.0** | Released 2026-09-03; sprint boundary; passed the release-age gate |
| 2026-09-06 (S0) | Playwright stays **1.62.1** | 1.63.0 (2026-09-04) was inside the 24-hour release-age window |
| 2026-09-06 (S0) | Hugeicons via shadcn's `iconLibrary`, no migration swap | Registry ships a Hugeicons icon map |
| 2026-09-06 (S0) | Temporal server compose uses `temporalio/server` + an idempotent `admin-tools` schema job and namespace job (POSIX sh: the images carry no bash) | §0 row 19; auto-setup is deprecated |
| 2026-09-06 (S0) | Garage 2.3 single-node with `--single-node --default-bucket` confirmed: layout, bucket, and key come from env on first boot | Quick start, verified in compose |
| 2026-09-06 (S0) | Contracts chain built: Zod 4 `z.toJSONSchema(z.globalRegistry)` with `$defs` refs → one `contracts.json` → `datamodel-code-generator` 0.76 → pydantic v2. Zod's uuid `pattern` is stripped because pydantic refuses a regex on a `UUID` field | §2 cross-language contracts |
| 2026-09-06 (S0) | `@temporalio/client` 1.23.0 (1.22 in v1.0 was superseded before S0 started) | npm `latest` |
