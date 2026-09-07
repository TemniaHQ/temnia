# Building Temnia in Public

**Status:** v1.0, 2026-09-03 — the operating strategy for the daily X and LinkedIn series that runs alongside the rebuild
**Owner:** Rajesh. Claude drafts, Rajesh edits and posts. Nothing goes out in a voice that is not Rajesh's.
**Companion:** [prd.md](prd.md) is what we are building; this file is how we talk about building it.

## 1. Why we are doing this

The series opens as a challenge: can coding agents (Claude Code, Codex, Cursor) build a real, production-grade, multi-tenant product when a 13-year engineer sets the architecture, security bar, tests, and review gates? Not vibe coding — agents as the team, Rajesh as the tech lead. That premise carries the daily posts; the product and its positioning come second, and the rebuild story third.

The rebuild is a second attempt. The first codebase (`mitosia-legacy`, 120 commits) reached a walking skeleton, then failed its "magic moment" gate at 18% clip acceptance. Restarting as a monorepo with that lesson in hand is a better story than a clean launch would ever be, and it is true. Telling it daily does three things:

1. **Audience for Temnia** — agency owners, podcast producers, and content leads who recognise the problem ("clips start too early, end too late", "the client approved version 3 and we published version 4") before the product exists to sell them.
2. **Audience for Rajesh** — builders and AI engineers on X who want to see a real harness (model proposes, code disposes) built in the open, not a demo.
3. **Discipline for the project** — a daily post is a daily forcing function to ship something legible, name what broke, and keep the PRD honest.

## 2. Two platforms, two audiences, one substance

| | X | LinkedIn |
|---|---|---|
| Who reads | Builders, indie hackers, AI engineers, people who follow Claude Code and agent-harness work | Agency owners, podcast producers, marketing leads, potential design partners |
| What they want | The engineering: the bug, the design choice, the number, the diff | The why: what this means for how an agency runs client work |
| Length | Rajesh is on X Premium+, so long-form posts are available. Default to ≤ 280 chars; use one long post (not a thread) when there is a real story, with the hook in the first ~280 chars since that is all that shows before "Show more" | 100–200 words, hard cap 3,000 characters (LinkedIn refuses longer posts; the Day 0 draft hit this at 3,869); first line is the hook; short paragraphs; no hashtag walls |
| Media | Screenshot, terminal capture, 10–20 s screen recording | One clean image or a short clip; carousels for the weekly review |
| Voice | First person, terse, specific, a little dry. Reads like Rajesh typing, not like a generated post | First person, plain, direct, no corporate gloss |

Same day, same substance, different framing. Never paste the X post into LinkedIn or vice versa. LinkedIn always answers "why should an agency care"; X always includes the concrete technical detail.

## 3. Content pillars

Every post belongs to exactly one pillar. Rotate so no pillar goes more than four days without a post.

1. **Shipped** — what got built today, shown, not described. The screen recording of the thing working beats any sentence about it.
2. **How it works** — one design decision, explained at the level of a smart colleague. The PRD is a bottomless source: *models never emit timestamps*, *the verifier is a different model family*, *a retry re-pays zero tokens*, *prompts carry no numbers*, *every row is scoped by RLS and probed cross-tenant on every PR*.
3. **What went wrong** — the bug, the wrong assumption, the failed gate. The most-shared pillar and the one that makes the rest credible. Always with the cause and the fix (or "still open"). Legacy already has a backlog: ffmpeg reading a dropped connection as EOF and exiting 0; hydration bugs invisible under `next dev`; M1 failing at 18% on boundaries; the nine-model audition where most cheap models did not qualify.
4. **Numbers** — cost per source-hour, acceptance rate, boundary-adjustment magnitude, latency, tokens per run. Real measurements only, dated. Never a projection.
5. **The bet** — positioning and product principle posts. Why agencies, why upload-first, why no virality scores, why not an aggregator, why no free-running agents. One a week at most; these are the LinkedIn top performers.

Meta-angle that runs through everything: **Temnia is being built with Claude Code.** Show the workflow when it is interesting — how a session ends, what the PR gate looks like, when the AI got it wrong and the human caught it. It is a draw on X and it is honest.

## 4. The daily loop

Every working session ends with a log entry and two drafts. This is part of the definition of done for the day, the same way a PR description is.

```
docs/log/YYYY-MM-DD.md
```

```markdown
# Day N — YYYY-MM-DD

## Done
- one line per shipped thing, linking the PR or commit

## How
- the one decision or mechanism worth explaining today

## Went wrong
- what broke, why, fixed or open

## Numbers
- anything measured today, with the exact number and the condition it was measured under

## Tomorrow
- one line

## Posts
### X 1 (pillar) · image: <which>
<post>
### X 2 (pillar)
<post>
### LinkedIn 1 (pillar) · image: <which>
<post>
```

Rules for the loop:

- The log is committed to the repo. It is the source of truth for the posts, and later it is the project's own history.
- **Several posts a day are fine; one idea each** (Rajesh, 2026-09-06). A full day usually yields a shipped post, a how-it-works post, and a went-wrong post on X, and one or two on LinkedIn. Each post block in the log names its pillar and its image so the order and the media are decided when the log is written, not at posting time.
- **Day N is the day of posting** (Rajesh, 2026-09-07). Work that closed late the night before opens
  with "Sprint one closed last night", still under the new day's number; a second post the same day
  says "second post". Readers follow the calendar, and a repeated day number reads as a repost.
- **Short points, not paragraphs** (Rajesh, 2026-09-07): one idea per line, a blank line between,
  plain one-line labels ("What shipped", "What broke", "The numbers"), a number on every line that
  can carry one, the result in the first line, the next step as the last. No bold headers, no emoji
  bullets, no agency angle until Rajesh calls it.
- **Title** (Rajesh, 2026-09-06): every post on both platforms opens with **`Day N of building Temnia.`** and nothing more; the tools get named in the body where they did something, never in the title. Day 0 is the origin posts; Day 1 is the foundation. (This file's own name, "Building Temnia in public", stays as the strategy's name, not a post opener.)
- Claude writes the log and both drafts from what actually happened in the session — no embellishing, no rounding numbers up.
- Rajesh edits for voice and posts. If the edit changes a fact, the log gets corrected too.
- Days with nothing shippable still post: a "how it works" or "what went wrong" from the log's backlog, or a "numbers" post. The day counter never skips.
- If a day was genuinely bad — nothing worked, spent the day on tooling — that *is* the post. "Day 14: lost the whole day to X. Here's what I now know." Those posts outperform.

## 5. Weekly rhythm

| Day | X | LinkedIn |
|---|---|---|
| Mon | The week's goal in one post | The week's goal and why it matters for agencies |
| Tue–Thu | Build days: shipped / how / went wrong, whichever is strongest | Same substance, agency framing |
| Fri | **Week in review thread**: what shipped, what broke, the numbers, what's next | **Week in review** as a carousel or a longer post; the strongest post of the week |
| Sat | Lighter: a "how it works" from the backlog, or a reply-driven post ("what should I show next week?") | Skip or repost the Friday review with one new line |
| Sun | Next week's plan, one post | Skip |

Seven posts a week on X, five on LinkedIn. The Friday review is the anchor; if only one post gets real effort in a week, it is that one.

## 6. Post shapes that work (use these, don't reinvent daily)

**Shipped (X)**
> Day 12. Upload → HLS ladder → waveform → transcript, end to end, on a 2h podcast.
> 4m11s wall time. $0.31.
> [screen recording]

**How it works (X thread opener)**
> The model that picks clips in Temnia never sees a timestamp. It picks sentence IDs. Code turns IDs into time.
> Here's why that one rule fixed most of our boundary bugs. 🧵

**Went wrong (X)**
> Day 9. Every transcode passed. Every output was truncated.
> ffmpeg treats a dropped connection as end-of-file and exits 0.
> Fix: verify output duration against the probe before a source can go "ready". Never trust exit codes on remote input.

**Numbers (LinkedIn opener)**
> Our AI picked 40 clips from a 90-minute episode. A human editor accepted 7.
> That's 18%. That's a failing grade, and it's the most useful number this project has produced so far.
> Here's what it taught us about where clips should start and end.

**The bet (LinkedIn opener)**
> Every clipping tool sells "virality scores". We decided not to build one.
> Not because we can't. Because zero-shot models barely beat a coin flip at predicting engagement, and an agency can't put a coin flip in front of a client.

### 6b. Pitching the gap (2026-09-04)

How product teams frame a gap, distilled from Dunford's positioning method, Raskin's strategic narrative, and the "why now" literature, and adapted to a first-person post. Use this shape whenever a post explains why Temnia exists; the Day 0 post C is the reference.

1. **Start from the alternative, not the competitor.** The thing an agency would do without Temnia is a stitched-together week: a clipping tool for the first pass, an editor redoing the cuts, a spreadsheet, email approvals, a re-export. Describe that as a scene with a role in it so the right reader self-identifies. This is also how the no-competitor-names rule gets honored for free.
2. **Name the shift, not the problem.** Every tool is playing the highlights game, and the episode itself, cut into chapters, is the agency's real deliverable and is still done by hand. A highlight is forgiving; a chapter partition is not (every second in exactly one chapter or a deliberate drop, one cut point per shared silence, a wrong boundary breaks both neighbors). That strictness is why the harness design exists.
3. **Answer "why hasn't someone built this".** Everyone built for the solo creator and bolted agency features on later; the boundary problem is structural and needs code, not a bigger model.
4. **Value, not features.** Each capability gets its "so what": the whole episode comes out as chapters that are right and everything else hangs off that spine, any piece plays back to its exact seconds, the wrong version can't ship, the weekly show sets itself up. Shorts are described as what falls out of the chapter cut, never as the headline.
5. **Category in plain words.** "Less a clipping tool than a cutting room for the whole episode." An existing idea the reader already understands, not an invented category.
6. **The wedge.** Start with the cutting room, and specifically the chapter cut, the hardest piece and the one no clipping tool attempts.
7. **One narrow question at the end.** "If you cut episodes for clients, tell me what I've got wrong." Replies are where design partners come from.

Evidence comes last and, on Day 0, honestly: there is none yet, only the number being chased (post D).

## 7. Launch sequence (first seven days)

| Day | Post |
|---|---|
| 0 | **Origin, as three standalone posts** (not a thread), a few hours apart: A the challenge (pinned), B the product and the harness, C why this idea (the engineering difficulty first, then the market gap). Each reads on its own. Drafts in `docs/log/day-000-origin.md`. |
| 1 | **The disclosure and the first commit.** Attempt two: 120 commits, 40 clips picked, 7 accepted, 18%. Started over with what was learned. Post D in the origin file, plus the first commit shown. |
| 2 | **The one rule.** Model proposes, code disposes — models select IDs, never timestamps. The harness as the product. |
| 3 | **First shipped thing** from the new repo, whatever it is, shown running. |
| 4 | **What went wrong, from legacy:** the ffmpeg truncation story. Establishes the "we tell you when it breaks" contract early. |
| 5 | **Week in review** — thread and carousel. |
| 6 | **Ask:** "Agencies: what's the step in your clip workflow you'd pay to delete?" Replies feed the roadmap and surface design partners. |

## 8. Rules

**Always**
- **Posts are a developer's account of the day, in order, and the division of labour is the story** (Rajesh, 2026-09-06, after the first drafts read like release notes). Say what the agent did on its own, where Rajesh stepped in and why, and what Rajesh did by hand at a dashboard. Name the agent's mistakes and who caught them, the agent or the human. **No clock times and no play-by-play** (Rajesh, 2026-09-06: "cheap and boring"); a post is a summary with a line of explanation where it earns its place. Measurements and counts that carry meaning stay (a round trip, a merge-to-live, a slot count); when something happened does not. The stack appears wherever it carries meaning, with the reasoning stated in the first person (Rajesh, 2026-09-06): readers should see an engineer who understands the system, not someone dependent on the agent. A post that could have been written by the product's marketing team is a rewrite.
- Write in Rajesh's own voice (2026-09-03). First person, contractions, the odd aside, plain sentences of uneven length. Say "I" not "we" unless there is actually a we. If a reader could guess an AI drafted it, redraft.
- Real numbers with dates and conditions. "18% on a fresh 90-min source, 2026-08-28" not "low".
- Say what went wrong the day it went wrong, or the day after. Never bury a failure in a Friday review.
- One idea per post. If it needs two, it's a thread.
- Show, don't tell: a recording of the feature beats a description of it.
- Reply to every substantive reply for the first 90 days. The replies are where design partners come from.

**Never**
- The tells of generated text: bold section headers inside a post, "Not X. Not Y." openers, groups of three, a tidy punchline closing every paragraph, "Here's the thing", "Let's dive in", em-dashes as the default join.
- Secrets, keys, tokens, customer names, customer media, or transcript content — screenshots are checked before posting, every time.
- Cost numbers that expose a specific provider's negotiated pricing.
- Screenshots of the admin panel, Dokploy, Cloudflare, or any infra console with hostnames visible.
- Predictions dressed as results. "We expect" is fine; "we achieved" only after measurement.
- Engagement bait, fake countdowns, "big announcement soon". The premise of the series is that there are no surprises.
- Naming a competitor in a post, even neutrally (2026-09-04). Name the category problem; keep product names in the log's facts line for verification only. Rajesh's call: naming invites controversy the series does not need.

## 9. What we measure

Followers are the vanity metric. Track weekly, in the Friday review's log entry:

| Signal | Why it matters |
|---|---|
| Replies and DMs from agency owners / producers | The actual target audience is showing up |
| Waitlist sign-ups (once the landing page exists) | The only number that converts to design partners |
| Repo stars / PRD views (if public) | Builder audience |
| Which pillar's posts got saved/shared most | Steers the rotation |
| Posts published / 7 | The discipline metric; the streak is the promise |

Review the mix monthly. If "the bet" posts outperform on LinkedIn and "went wrong" on X — which is the expected shape — lean in, don't rebalance for symmetry.

## 10. Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Repo visibility | **Private** (2026-09-03). Share snippets, diffs, and the PRD freely; revisit at M2. |
| 2 | Waitlist landing page in week 1–2 | Open — decide later |
| 3 | Day counter | **Day 0 = the origin post**, not the first commit (2026-09-03) |
| 4 | Posting time | Open |
| 5 | Reveal the name from Day 0 | **Yes**, after the §11 checklist is done (2026-09-03). Building under a codename accrues audience to a name we would throw away. |
| 6 | Post title | **`Day N of building Temnia.`** on both platforms (2026-09-06). Earlier that day: X `Day N.` and LinkedIn `Building Temnia in public, day N.`; replaced the same evening for one simple form. |
| 7 | Posts per day | **Several, one idea each** (2026-09-06); the log lists them with pillar and image. |

## 11. Protect the name before Day 0

> **2026-09-03 — The product is now Temnia** (see [naming.md](naming.md) Decision: cleared IP India 42 + 9, USPTO, WIPO the same day). Day 0 remains gated on the §11 lock-in — `temnia.com` registered, handles reserved, TM-A filed — not on the name. The India trademark search found an identical `Mitosia` mark in class 42 (application 7804137, filed 20/06/2026, prior use claimed from 10/10/2024) belonging to a design studio. We are the junior party. See [trademark-filing.md](trademark-filing.md) §8. The original name is being replaced rather than fought for; the replacement must clear its own searches before the first post — publishing under a contested name is how you build an audience for someone else's trademark.

Squatters target names with traction, not names nobody has heard of — so the window to lock this down cheaply is *before* the first post, and it is a one-day job. None of it is optional; the origin post links to the canonical domain and pinned "official channels" post from the start.

**Status 2026-09-03:** the product was renamed from Mitosia to Temnia after the class-42 conflict (see [trademark-filing.md](trademark-filing.md) §8 and [naming.md](naming.md)). Temnia cleared IP India (42 + 9, phonetic and Start-With), USPTO, and WIPO the same day. `temnia.com` (canonical) and `temnea.com` (the homophone) are registered on Spaceship. `mitosia.com` is kept for a year as a redirect, then lapses; `mitosia.ai` / `.io` are never registered.

1. **Domains.** `temnia.com` is the canonical domain; `temnea.com` redirects to it. Done. Skip the long tail; typo-squats are not worth chasing.
2. **Handles.** X **@TemniaHQ** and LinkedIn **/company/temnia** — reserved 2026-09-03; display name "Temnia" on both. GitHub org `TemniaHQ` (github.com/TemniaHQ/temnia), renamed from TemniaAI on 2026-09-05. Grab YouTube, Instagram, Threads, TikTok next. Names are not unique on any platform; the trademark filing, LinkedIn page verification on the domain, and a monthly search are what deal with copycats.
3. **Trademark.** File a word-mark application for TEMNIA with IP India in class 42 (SaaS) and class 9 (software). ₹4,500 per class filed online as an individual, and the *filing date* is the priority date that beats a later launcher. Full procedure, specification wording, and deadlines: **[trademark-filing.md](trademark-filing.md)**. A US filing can follow within the 6-month Paris Convention priority window.
4. **One canonical URL, one pinned post.** Day 0 pins "These are the only official Temnia channels: temnia.com, [X], [LinkedIn], [GitHub org]. We will never DM you for payment." That single post is what protects the *audience* from an impersonator — it gives them something to check against.
5. **Consistency as the defence.** Every post links the same domain; the landing page (when it exists) links the same handles. An impersonator cannot fake a four-month-old consistent trail.

What this does not do: stop someone from registering `temnia.xyz` and calling something Temnia. Nothing stops that. The trademark filing is what makes it *their* problem rather than ours, and the pinned post is what keeps the audience safe in the meantime.
