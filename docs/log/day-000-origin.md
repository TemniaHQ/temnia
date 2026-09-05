# Day 0 — the origin post

**Status:** draft v8, 2026-09-03. Do not post until `temnia.com` is registered and the X/LinkedIn handles are reserved (build-in-public.md §11). Pin on both platforms once posted.
**Framing:** the series opens as a challenge — can coding agents (Claude Code, Codex, Cursor) build a real, production-grade product when a senior engineer sets the architecture, security bar, and review gates? Then what Temnia is, then why this idea after studying the existing categories. The rebuild story is a late disclosure, not the opener. X gets three standalone posts on Day 0 (not a thread; each must read on its own), spaced a few hours apart, with the first one pinned. The second-attempt disclosure is its own post on Day 1 with the first commit. LinkedIn gets one post. Both are written in Rajesh's first-person voice: contractions, asides, plain sentences, no section headers, no tidy punchlines, nothing that reads as generated.
**Facts used:** 13 years of software development; legacy repo 120 commits; M1 failed 2026-08-28 with 7 of 40 clips accepted (18%); competitor observations from the legacy `clipping-landscape.md` dossier (2026-08) and 2026 reviews checked 2026-09-04: Opus Clip 20–40% discard in independent tests (ScaleReach, Ssemble, Techpresso), one test 10 of 76 unusable; Riverside Magic Clips "do okay at identifying topics, but the boundaries of the clip and the editing make them not directly useful" (Capterra/Cleanvoice); Vizard clips too short on conversational content, Munch weak on multi-speaker (Choppity, Reap benchmark); Eddie AI black-box selection, single-editor; the dossier's four recurring failures (boundaries, selection≠performance, under-tooled review, billing hostility) and its open flank (Vizard workflow without selection quality, Eddie coverage without workflow, Butter client workspaces without clipping); YouTube Studio suggesting key moments for podcasts; the "LLMs find moments well and delimit them badly" consensus (StreamYard lead, PodReels, Repurpose-10K); PRD differentiators used: segments coverage lane (§9), provenance (JTBD 2), version-pinned approvals (§18), recipes (§22), fair-billing rule (§23, worded as "doesn't cost you twice" to match the PRD's "must never feel like paying twice", not a refund guarantee). Positioning leads with the coverage lane (AGENTS.md 2026-09-04): exact-cover chapter partition with keep/drop (PRD §9), shared-boundary rule (one cut point in a shared silence gap, edits cascade to the neighbor), chapters.json and timestamped description at finalize (§9), episode package (§7), mid-roll insertion re-scoring segment boundaries (§10); the moments lane still exists and is described as what hangs off the chapter spine. Pitch structure follows build-in-public.md §6b: status-quo alternative as a scene, the shift, why nobody built it, value not features, category in plain words, wedge, one narrow question. The status-quo scene (spreadsheet, email approvals) is a typical-agency composite, not a specific customer. Tool names stay in this facts line for verification only; the posts describe categories, never named products (build-in-public.md §8, 2026-09-04); Temnia from *temnō*. Do not name the other trademark applicant. Name categories, not products, when describing a weakness (build-in-public.md §8).

## X — origin posts (independent posts, not a thread)

Three posts on Day 0, a few hours apart, each complete on its own. Pin A. Post D goes out on Day 1 with the first commit. The split is so each post gets read, not so each post gets shorter than it needs to be; the voice comes first.

### A — the challenge (pin this one)

I'm starting something today and I want to write down why before I talk myself out of it.

For a while now I've had a question I can't answer by reading other people's threads: how far, and how fast, can one experienced engineer go with coding agents? Not a demo. Agents can make a demo. I mean the sort of product a real business trusts with its data and its daily work, with tenants and permissions and a security model, the kind of thing I've spent the last 13 years building the slow way, with teams, sprints, architecture debates and production incidents.

So I'm going to try, and I'm going to post about it every day.

A few things up front, because I know how this sounds.

It's not vibe coding. I'm not going to describe a feature and accept whatever comes back. I'll decide the architecture, the data model, where the security boundaries sit, what gets tested and what has to pass before a PR merges. Claude Code, Codex and Cursor will write most of the code. I'll read all of it. If bad code gets through, that's my failure. I don't get to blame the agent.

Same rule for these posts. The agents draft them from the day's log, I edit every one, and nothing goes out I wouldn't say myself. The experiment is how far and how fast I can go with agents, and that includes the writing.

The honest way to put it: I'm the tech lead, the agents are the team. What I don't know is whether that holds up six months in, when I can no longer keep the whole codebase in my head and the agents have to change systems they didn't originally write. That's the part I'm after.

What you'll get from me is one post a day, numbered. What shipped, shown running. What broke. Where the agents helped and where they wasted my time. Real numbers with the date and how I measured. If I lose a whole day to a stupid bug, that's the post.

No teasers, no "big announcement coming". The whole point is that there are no surprises.

Day 1 is tomorrow. Come along, or tell me where you think it'll fall over.

### B — the product

What I'm building. It's called Temnia.

You run a content agency. A client sends you a two-hour podcast, and by the end of the week it has to be a clean episode, chapters, segment videos, shorts, quotes, show notes and posts. Temnia cuts the episode into complete chapters first, and everything else hangs off that spine, each piece with a path back to the exact seconds it came from, so you can check that a clip actually says what its caption claims. Nothing publishes because a model felt confident. A person approves it. Lots of clients, one workspace, with the approvals an agency actually needs and never gets.

The part I care most about isn't the model, it's the harness around it. Models are good at judgment and bad at precision. I've watched one pick a genuinely great moment and then miss the start of the sentence by half a second, which is the difference between a clip you post and a clip you throw away. So I'm building a system where the model gets to say what matters and code gets to say where the cut lands, checks it against the transcript, and keeps a record of every decision along the way. Every step is checkpointed, so a retry never re-pays for work already done. Every output gets graded by a different model from the one that produced it, because a model marking its own homework is worth nothing. Every proposal is exactly that, a proposal, until a person accepts it.

Models will keep getting swapped out. Better ones will arrive every few months and I want to be able to plug them in on a Tuesday afternoon. The harness is the thing that has to last.

(Temnia is from temnō, Greek for "I cut". Same root as atom and epitome. I spent far too long on the name and there's a story there for another day.)

If you do this work every week, tell me which part I'm underestimating.

### C — why this idea

Why this and not something else. Two reasons, and the first one is that it's hard.

A video editor is a miserable thing to build well. Ten-gigabyte uploads over hotel wifi that have to resume, not restart. Transcodes that run for hours and can't lose their place when a worker dies. A transcript aligned to the audio to the word, because a cut a hundred milliseconds late chops the first syllable and everyone hears it. A two-hour timeline that doesn't stutter in a browser. Edits stored as data, so the same edit renders the same way twice. Every client's footage walled off from every other's.

None of it is glamorous, and all of it fails quietly. I've already had a transcoder read a dropped connection as the end of the file and exit with a success code. Every output was short. Nothing complained.

That's the kind of problem I want to watch agents handle, with me setting the bar. A todo app wouldn't tell me anything.

The second reason is the gap, and it's not where everyone is looking.

Every AI video tool right now is playing the same game: mine a two-hour episode for a handful of thirty-second highlights. That's the smallest part of what an agency actually delivers. The episode itself goes out as chapters. The chapters become the segment videos, the timestamps under the upload, the show notes, the mid-roll points. The shorts are garnish. And the chapter cut is still done by hand, because the few tools that try it are built for one editor with a black box in the middle.

Nobody's cracked it because a highlight is forgiving. Start it a bit early, end it a bit late, nobody checks what you left out, the editor fixes it. A chapter cut isn't. Every second of the episode has to land in exactly one chapter or be a deliberate drop. Two chapters share one boundary, one cut point inside one silence, and if it's wrong, both chapters are wrong. You can't hide a bad cut in a chapter cut. Which is the whole reason for the design: a model can't be trusted to partition two hours of speech, so code checks the cover and a person owns it.

So Temnia is less a clipping tool than a cutting room for the whole episode. The full episode comes out as chapters that are right, and everything else hangs off that spine, every piece playable back to the exact seconds it came from. The client signs off on one version. The weekly show is set up once.

That's the hardest piece of all of it, so that's where I'm starting. If you cut episodes for clients, tell me which part I'm underestimating.

### D — the disclosure (Day 1, with the first commit)

One more thing, and I'd rather say it now than have it come out later. This is my second attempt.

The first version got to 120 commits and then, on 28 August, I ran the test that mattered: it picked 40 clips from a fresh 90-minute episode and I put them in front of editors. They accepted 7. Eighteen percent. The model kept finding an interesting idea, then starting the clip before the thought had begun or ending it after the energy was gone. I had rebuilt the exact complaint I was trying to solve.

I could have spent another month patching around it. Instead I archived the repo. 18% wasn't a rough edge, the system had the wrong shape. So the rebuild starts with the evaluation harness and the boundary problem, not the dashboard.

Eighteen percent is the number on the wall. When I run that test again I'll post the result, even if it's still embarrassing. First commit goes in today.

## LinkedIn

I'm starting something today, and I'm going to write about it every day until it either works or it doesn't.

The question I can't answer by reading other people's posts: how far, and how fast, can one experienced engineer go with AI coding agents? Not a demo. Something a business would trust with its client work, with real security, an architecture that survives growth, and a person accountable for every line. The kind of thing I've spent 13 years building the slow way.

It isn't vibe coding. I decide the architecture, the data model, the security boundaries, and what has to pass before anything merges. Claude Code, Codex and Cursor write most of the code. I read all of it. If bad code gets through, that's my failure, not the tool's. That applies to these posts too: the agents draft them from my daily log and I edit every one. The real question is whether this holds up six months in, when I can no longer keep the whole codebase in my head and the agents are changing systems they didn't write.

The product is Temnia. If you run a content agency, you know the problem. A client sends a two-hour podcast and someone spends the afternoon cutting it into chapters by hand, because that's what really goes out: the chaptered episode, the segments, the timestamps, the show notes. Then the client approves version three and version four ships. Temnia cuts the whole episode into chapters that are right, hangs everything else off that spine, and gives every piece a path back to the exact seconds it came from. Nothing publishes because a model felt confident. A person approves one version.

Under the hood, models are good at judgment and bad at precision, and a chapter cut punishes imprecision: every second lands in exactly one chapter or is a deliberate drop, and a wrong boundary breaks the chapters on both sides. So a model says what matters, code decides where the cut lands and checks it, and a person has the final say. Models will keep getting swapped for better ones. The system around them is what has to last.

Why this? First, it's hard, which is the point. Ten-gigabyte uploads that have to resume, transcodes that run for hours, transcripts aligned to the word, every client's footage walled off from every other's. All of it fails quietly. I've already had a transcoder treat a dropped connection as the end of the file and report success.

Second, the gap. Every AI video tool is mining the episode for thirty-second highlights, and a highlight is forgiving. Start it early, end it late, nobody checks what you left out. The episode itself, cut properly, is still an editor's afternoon, and nobody is building that for an agency with ten clients and a sign-off on every asset.

One post a day from here: what shipped, what broke, where the agents helped and where they wasted my time. Day 1 is tomorrow.

If you cut episodes for clients every week, tell me which part I'm underestimating.

## Pinned "official channels" post (both platforms, same day)

These are the only official Temnia channels: temnia.com · X @[handle] · LinkedIn /company/[handle] · GitHub [org]. We will never DM you asking for payment.
