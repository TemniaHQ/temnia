# Temnia: the build-in-public playbook

**Version:** 2.1 · **Reviewed:** 2026-09-08 · **Timezone:** Asia/Kolkata

This revises the supplied `TEMNIA_BUILD_IN_PUBLIC_GUIDE_v1_1.md` after reviewing its sources and Temnia's repository. Rajesh explicitly asked to reassess the earlier, unresearched posting habits. This is the current editorial guide; the [research review](RESEARCH_REVIEW_2026-09-08.md) records the reasoning and limits.

The aim is relevant attention, useful conversations and trust in the work. Posting volume is one variable to test. It is not the definition of progress.

## 1. What changes now

| Earlier habit or supplied recommendation | Current practice |
|---|---|
| Every post opens with `Day N of building Temnia.` | Lead with a concrete result, problem or insight. Keep `Day N` as an optional continuity label. |
| One post per platform is a hard cap; combine unrelated updates. | One original post has one takeaway. Split independent stories when useful; save the rest. |
| Public posts reproduce the day's engineering log. | Logs record the work; posts answer a reader's question about it. |
| Customer framing must wait for a separate instruction. | Explain real editor/creator consequences now, without claiming customer validation. |
| Default to 2–4 combined X and 1–2 LinkedIn posts every day. | Start with the bounded trial below; higher output is an earned expansion. |
| Specific formats, failures or exact posting times will perform best. | Treat these as hypotheses until Temnia's own response supports them. |
| Create a separate event store, JSON packs, config and publication ledger. | Use the existing daily log with a small review block. Add tooling only if it solves a demonstrated problem. |

Retain truthful evidence, an identifiable founder voice, clear account ownership, useful media and honest availability. The [two-account guide](TEMNIA_TWO_ACCOUNT_STRATEGY.md) owns account roles and profile examples; it is not duplicated here.

## 2. The story people should understand

**Product direction:** Temnia is being built to turn long recordings into complete chapters and segments, with moments and shorts drawn from that structure.

The practical difficulty is a good topic for both audiences: a chapter's ending is its neighbour's beginning. Moving that cut changes both sections. The intended coverage rule gives every source second one chapter or an explicit decision to drop it. In public, explain this with a visible boundary example before using terms such as “exact cover.”

**Founder story:** Rajesh is building the product with coding agents and engineering review. Show a consequential decision, what an agent attempted, what Rajesh corrected, and how the result was checked when those facts are available. The lesson should remain useful to someone who has never followed the series.

The rebuild and its failed legacy quality gate are relevant context, not a required opening on every post. Attribute old numbers to the old implementation and its test conditions. Name the actual agent used in a session; do not attribute every result to one tool by default.

**Audience:** editors, podcast/video producers, agencies and creators who work with long recordings; developers and founders interested in how the system is built. Choose one primary reader per post. Do not force a percentage split across these groups.

| Reader | A useful question to answer |
|---|---|
| Editor or podcast producer | How do I decide where this chapter ends without damaging the next one? |
| Agency operator | Which decision or handoff repeatedly needs manual correction? |
| Long-form creator | How can I check that a proposed segment keeps the context I intended? |
| Developer or founder | What failure occurred, what assumption caused it, and what proved the repair? |

This positioning describes intent. It does not say the complete chapter workflow, studio, agency approvals or public access already exist. The editing core precedes the SaaS layer in [the sprint plan](../sprint-plan.md). The [PRD](../prd.md) describes product intent, [dated decisions](../../AGENTS.md) resolve design changes, and current tests and runtime records establish behaviour.

For example, the [2026-09-08 implementation audit](../design/harness-implementation-status-2026-09-08.md) separates a short deployed speech smoke from unfinished editing architecture and representative quality/cost proof. That is a dated observation, not a permanently current feature inventory. Recheck the relevant revision before drafting a new claim.

## 3. Cadence: a practical trial

Start on the first full week using this guide. Record that actual start date in the daily log; reviewing a strategy is not evidence that its posts were published.

| Account | Initial target | Optional expansion |
|---|---|---|
| `x_personal` — @RajeshBuilds | One original X post daily: 7/week. | A second founder post when there is an independent, supported takeaway. |
| `x_product` — @TemniaHQ | Three original posts/week, initially Monday, Wednesday and Friday. | Add a fourth or fifth weekly post when previews, questions and review capacity support them. |
| `linkedin_personal` — Rajesh | One original post each weekday: 5/week. | A separate milestone story occasionally; two daily posts are not the baseline. |

Baseline: **10 combined X posts + 5 LinkedIn posts per week**, or **20 + 10 across two weeks**. Replies, ordinary reposts, variants and unused drafts are tracked separately. Count every part of an X thread as a post; count a substantive quote post once.

The supplied **2 personal + 1 product X + 1 LinkedIn daily** allocation is a possible later mode. It is not proven superior by the research. Try it only after the baseline has shown enough independent material, time to respond and manageable effort. Four X posts or a second LinkedIn post can suit a real milestone; do not turn them into a daily obligation.

**Time budget:** begin with about 30 minutes/day for final editing, publishing and useful replies, roughly 15 minutes for copy and 15 for conversations. Batch a few public-safe captures during existing product checks. Include extra capture, research, verification and analytics-review time in the weekly total. This is a resource allocation, not a claim that content production always takes 30 minutes.

If content repeatedly exceeds the budget or delays the build, remove optional posts first, then reduce product X to twice weekly or LinkedIn to three times weekly. Record the change. If even the baseline requires filler, publish less.

No compulsory catch-up bursts or unbroken streak. On a quiet day use a still-relevant older lesson with its original chronology, an actual unresolved question, or no post. Keep the engineering log even when public output pauses.

**Timing:** pick convenient windows when Rajesh can return to the conversation. For an initial operational schedule, try lunch and early evening in Asia/Kolkata, separating independent posts by a few hours. Record actual times; these are not global “best times.” Keep windows stable during the first trial instead of changing topic, format, cadence and time together.

## 4. Voice and hooks

Start with what the reader can learn. “The model's answer arrived. The worker crashed before saving it.” is a stronger topic opening than a sprint number, when that event actually happened. This is an editorial judgment to test, not an algorithm rule.

Use first person for Rajesh's decisions and experiences. Short paragraphs or short points are both fine. Prefer concrete language and natural variation over a required line-break pattern. Use stack names when they explain a choice; translate the effect when writing for an editor.

Keep `Day N` when it helps continuity, usually as a closing line such as `Building Temnia · Day N`. Multiple posts on the same editorial date share the same day label. Do not reset the journey for the product account or invent a publication date from a commit date. If the day number is unresolved, omit it.

Avoid canned excitement, fake struggle, universal claims, engagement bait and invented founder opinions. A tool result can establish a test outcome; it cannot establish that Rajesh was surprised, lost sleep or spoke to a customer. Existing founder notes and dated decisions are enough to draft from; they do not need repeated approval before every paraphrase.

Be straightforward about AI involvement. LinkedIn recommends disclosure when a post relies heavily on AI and that is not apparent; minor language editing does not create a blanket disclosure requirement. Rajesh still reviews and owns the published substance. [LinkedIn's AI-content guidance](https://www.linkedin.com/help/linkedin/answer/a1481496)

Useful opener shapes:

- A concrete failure and the condition that triggered it.
- A result with the workload and scope that make the number meaningful.
- A design tension an editor can recognise.
- A corrected assumption and the evidence that changed it.
- A specific question whose answer can influence the product.

A closing invitation is optional. End on the useful point when there is nothing meaningful to ask. A real question beats a compulsory “Thoughts?”; a clear explanation needs no question at all.

## 5. Evidence before copy

Keep the evidence in [daily logs](../log/), linked to the actual revision, test or permitted observation. Do not make a new second source of truth for the same event.

| Kind of statement | Support | Appropriate wording |
|---|---|---|
| Verified behaviour | Inspected implementation and relevant run result. | “In this staging test…” |
| Interpretation | Reasoning from evidence, without a measured outcome. | “This should make the boundary easier to inspect.” |
| Founder opinion | Rajesh's note or recorded decision. | “I chose this because…” |
| Plan or hypothesis | Current product/design intent. | “The next test is…” / “I'm exploring…” |

Implementation and availability are different. A local pass is not a public release. A merged PR is not proof that the matching build is running. A temporary deployed smoke is not proof that all staging users have the feature. A roadmap checkbox is not an acceptance test.

For each number, retain the date, revision, environment, sample, unit, method and limitations. Include a comparable baseline for improvement claims. Keep these distinctions explicit:

- Source duration, processing wall time and human editing time.
- Successful compute time, total attempted compute and invoiced cost.
- Candidate count, accepted chapter count and published deliverable count.
- A short smoke test, representative quality evaluation and a scale run.
- Legacy measurements, current measurements and targets.

Spell ambiguous durations out: `2 h 31 min`, not `2:31`. Do not turn 7 accepted out of 40 legacy candidates into an exact 18% without acknowledging rounding, or reuse it as current performance. Do not infer savings by multiplying an isolated short-sample time into a two-hour estimate.

The intended harness reuses committed results. Unknown provider outcomes still matter; do not promise “retries can never cost twice” or “zero repeated calls” from a design principle alone.

Questions also need checking. “Why do agencies lose 20 hours to revisions?” asserts an unsupported measurement. Ask where a particular decision becomes difficult instead.

## 6. Choose an account before choosing the wording

A founder post explains a decision or lesson. A product post explains a customer's task, a visible result, a useful workflow idea or current access. One event can serve both, but the reader should learn something different from each.

Before drafting, write a one-sentence takeaway for every candidate. If two X takeaways mean the same thing, give the message one owner. Changing pronouns or adding a new hook does not make it independent.

LinkedIn can explore the same underlying event with more context, a scenario or a meaningful tradeoff. It does not need an arbitrary number of extra paragraphs, and it should not be a padded X post. Another LinkedIn story on the same day needs another takeaway.

A weekly recap can reference previous work if it adds synthesis: what changed in the approach, what remains unresolved, or what the sequence of tests taught us. Label it as a recap, not new progress.

## 7. Daily workflow and record

1. **Capture:** update `docs/log/YYYY-MM-DD.md` with done, how, went wrong, numbers and next. Link the real evidence. Multiple sessions append or revise carefully without overwriting another session's work.
2. **Select:** read recent drafts and known publications, normally the past two weeks. Pick today's strongest founder story and the appropriate product/LinkedIn material from current work or backlog.
3. **Draft:** write the public copy first. Keep screenshots to capture, evidence notes and review questions outside that block.
4. **Verify:** check claims, chronology, account, length, assets, release state and duplicate takeaways. Missing evidence becomes a narrower claim or a held draft.
5. **Review:** Rajesh reviews and edits the exact account/copy/media combination. Normal drafting can continue from known facts; do not invent approval.
6. **Record publication:** after an actual post, preserve its final text, account, timestamp and URL when available. A saved draft or approval alone does not prove publication.

Use this small block within the daily log. It is a template, not a request to create today's posts merely because this guide is being reviewed.

```markdown
### X founder — <topic> — draft v1

Account: x_personal / @RajeshBuilds
Editorial date: YYYY-MM-DD, Asia/Kolkata
Takeaway: <one sentence>
Evidence: <revision and inspected test/record links>
State: <planned / implemented / verified environment>; <availability>
Asset: <existing approved asset, capture needed, or none>
Checks: <length method; factual/media issues remaining>
Review: pending
Publication: unknown / not published / <confirmed URL and time>

Public copy:

<text intended for the composer>
```

Use `x_product / @TemniaHQ` or `linkedin_personal / Rajesh` for the other destinations. Give concurrent versions explicit labels. Preserve approved or published copy; a substantive change to the account, wording or asset needs review of the changed version. Do not silently replace history.

Markdown is sufficient for this workflow. JSON sidecars, a schema version, new dependencies, state files, automation and a CRM are not prerequisites. If later requested, those tools should reference the logs and preserve existing history.

## 8. Format library

Choose a shape that fits the evidence. These are writing prompts, not claimed growth formulas or a weekly quota for every format.

### X formats

| Format | Structure | Best owner |
|---|---|---|
| Concrete progress | Task → verified change → bounded result. | Product or founder, with different takeaways. |
| One interaction | What the person does → what the preview shows. | Product. |
| Engineering constraint | Failure condition → invariant → test. | Founder. |
| Decision | Alternatives → deciding evidence → accepted tradeoff. | Founder. |
| Bug | Trigger → consequence → repair or open question. | Founder. |
| Corrected assumption | What was believed → probe → changed decision. | Founder. |
| Prototype boundary | What works in this preview → next proof needed. | Product. |
| Measurement | Result → workload/sample → caveat. | Either, if relevant to that reader. |
| Workflow observation | Specific friction → evidence or explicit hypothesis. | Product. |
| Editing control | A decision the editor needs to inspect/change → intended interaction. | Product. |
| Discovery question | One task → one genuinely unresolved question. | Product. |
| Scope choice | An option deferred → reason → revisit condition. | Founder. |
| Useful tip | A workflow suggestion the reader can try without Temnia. | Product. |
| Feedback and change | Permitted observation → actual response → consequence. | Either. |
| Weekly synthesis | A pattern across the week's work → what changes next. | Founder. |
| Access update | Verified eligibility → real access route → limitation. | Product. |

### LinkedIn formats

| Format | Develop this |
|---|---|
| Progress with a lesson | One concrete workflow, the change, the evidence and why it matters. |
| Workflow teardown | A recognisable editing task, its decision points and a useful suggestion. |
| Technical decision | Competing constraints and a tradeoff another builder can apply. |
| Mistake and correction | The initial assumption, what disproved it and the practical change. |
| Creator preview | The creative task, visible interaction and remaining boundary. |
| Bounded case study | Sample and baseline, measurement method, result and limits. |
| Product principle | A real choice that illustrates a principle; distinguish implemented from intended. |
| Feedback and revision | A permitted observation, a changed decision and what remains unknown. |
| Weekly synthesis | What several tests taught us, rather than an exhaustive changelog. |
| Access invitation | Intended user, available workflow, eligibility, next step and honest limitations. |

For LinkedIn, start around 100–200 words and use more only when the explanation earns it.

**Founder X:** Rajesh reconfirmed Premium+ on 2026-09-08 and prefers a little more explanation when useful, while keeping posts quick to read. Do not enforce a 280-character drafting cap on @RajeshBuilds. For an explanatory post, roughly 50–100 words is a flexible starting range: a concrete hook, the relevant change or evidence, and one useful lesson. Shorter is fine; add another paragraph only when it earns its place. Do not pad a complete thought, split it solely to fit 280 characters, or turn each post into a long recap. This range is an editorial choice, not a minimum, hard cap or measured engagement optimum.

**Product X:** @TemniaHQ's entitlement remains unknown. Keep its drafts within the standard limit until longer-post access is confirmed for that account. Longer founder access does not establish product-account access. Threads remain an option when a sequence benefits the reader.

### Short examples

These are illustrative drafts, not new reports of shipped functionality or publication approval.

**Founder design lesson, supported by the recorded coverage direction:**

```text
A chapter boundary belongs to two chapters.

Move it to improve one ending and you can damage the next opening.

That's why I'm designing Temnia to choose neighbouring cuts together.
```

**Product discovery question, independent of that design explanation:**

```text
When you turn a two-hour interview into chapters, what takes longer: deciding where the topic changes, or finding the exact place to cut?

I'm building Temnia around this workflow and want to understand that distinction.
```

**LinkedIn story skeleton:**

```text
<Specific editing or engineering problem.>

<One real example and the condition that caused it.>

<What I changed, with the relevant evidence.>

<What the test establishes and what still needs checking.>

<A useful takeaway or a genuinely open question.>
```

## 9. Media and public boundaries

Use media when it makes the evidence easier to understand. A readable still can explain a shared chapter boundary. A short recording can show an interaction. A diagram can explain a design, but must not masquerade as a running product. Text alone is valid for a precise lesson.

Prefer a focused 10–20 second preview when that is enough. Caption speech, make labels readable on a phone and describe the relevant action in text. Do not claim that video or carousels always outperform text. A carousel is worthwhile when a multi-step explanation needs it, not merely because it is Friday.

For a capture, write: intended viewer; starting state; action; observed result; environment/revision; what the recording proves; what remains outside it. If a recording is accelerated, edited, mocked or made from multiple runs, disclose the transformation when it affects interpretation. Do not make a slow task look instantaneous by hiding the wait.

Use footage and transcripts that are cleared for public use. Recheck every frame, thumbnail, address bar, caption and visible notification. Never include credentials, signed links, infrastructure hostnames, administrative panels/consoles, customer identities or media without appropriate permission, or negotiated provider pricing. Sanitized code snippets and principles can be shared within the repository's existing permissions.

Keep named product competitors out of public posts under the existing editorial rule. Explain the workflow problem without claims that every competitor fails it. A proposed technical limitation that decides a design must be checked against the real service, following the repository's probe rule; a search result alone is insufficient.

Do not invent “watch below,” a local asset path, a customer quote or a demo URL. If the copy depends on missing media, label it `needs asset`; otherwise make the text stand alone.

## 10. Quiet weeks and the idea reserve

Keep a short reserve of unused, verified lessons in the existing logs. An unfinished test, a rejected option or an honest scope decision can be useful without a new feature. Preserve its original date and current relevance.

These thirty prompts are a reserve, not a calendar promising thirty accomplishments:

| Theme | Two possible questions |
|---|---|
| Chapter coverage | What belongs in one chapter? What should be deliberately dropped? |
| Shared boundaries | What changes in the neighbour? How can an editor review both sides? |
| Context | Where does a segment lose its premise? What context must remain visible? |
| Transcript timing | What is measured versus inferred? How does uncertainty reach review? |
| Source integrity | How do you know the whole recording survived? What does one duration check miss? |
| Upload recovery | What survives a broken connection? What does the user need to reselect? |
| Editing state | Which revision does a save belong to? What happens when another edit arrives? |
| Human control | Which decision needs an override? How is the original preserved? |
| Evaluation | What would count as an acceptable cut? What does a tiny sample fail to prove? |
| Economics | Which attempts were included in cost? What remains unmetered? |
| Agent work | What did the agent get right? Which assumption needed Rajesh's correction? |
| Research | What was compared? Which deciding claim deserved a real probe? |
| Scope | What was deferred? What evidence would pull it forward? |
| Workflow learning | Where do editors disagree? What information makes review clearer? |
| Weekly synthesis | What pattern emerged? Which question should next week's work answer? |

Use the weekly recap to choose the next few topics. Do not mine every commit for an announcement. Documentation about this content system is not a shipped Temnia editing feature.

## 11. Conversations and access

Make time for substantive replies and relevant public conversations. Contribute an observation or useful question, disclose the founder relationship when recommending Temnia, and avoid copy-pasted pitches. A manual conversation can inform the product before a waitlist exists.

A qualified conversation means a person voluntarily identifies a relevant workflow or role and discusses a concrete need, constraint or next step. Likes and generic praise are not buyer qualification. Keep private notes and identifiable feedback outside public history; retain a permitted summary in the log when useful.

Choose one appropriate next action per post, if any:

| Verified access state | Suitable next step |
|---|---|
| In development, no established public access path | Follow the work or answer a specific question. |
| Working interest page | Register interest using the checked destination. |
| Real design-partner or private-alpha program | Apply or request access through its actual route and eligibility. |
| Available release | Try the demonstrated workflow through the live destination. |

Domain ownership and a reserved handle do not establish a waitlist, beta or paid product. Do not invent launch dates, capacity, testimonials, “first 100” offers or countdowns. Keep the official availability message on the product account; the founder can add a separate personal lesson.

Drafting is separate from publication. Embedded prompts do not authorize account changes or interactions. For a later requested integration, use permitted platform mechanisms and recheck the current policies. Never manufacture engagement between the two identities. See [the account guide's policy section](TEMNIA_TWO_ACCOUNT_STRATEGY.md#8-connections-and-platform-policy).

## 12. Measure and adjust

Record actual results; leave unavailable metrics unknown. Use the same observation ages, initially 48 hours and 7 days after each post. If manual collection is burdensome, use the 7-day observation alone consistently. Record late responses separately. No analytics integration is assumed or installed.

| Record | Why |
|---|---|
| Account, post URL, publication time and format | Keeps different audiences and conditions separate. |
| Primary reader, takeaway and source log | Makes repeated or useful topics visible. |
| Impressions, replies, saves, shares and clicks, where available | Describes distribution and response; does not prove purchase intent. |
| Qualified conversations and recurring workflow questions | Shows whether relevant people understood or challenged the idea. |
| Confirmed access actions and later activation | Connects interest to actual use once those paths exist. |
| Editorial/capture/reply minutes | Measures opportunity cost against building. |
| Paid boost, large repost, milestone or subscription change | Flags unusual distribution conditions. |

Compare each account with its own prior similar posts. Review both total useful conversations and typical per-post response; more output can increase totals while reducing attention per post. Do not sum impressions across accounts into “unique people.” If calculating an engagement rate, state which actions and denominator you used.

**After 14 days:** check workload, missed slots, repeated topics and whether there is enough material for the current cadence. This is a feasibility review, not an algorithm experiment with statistical proof.

**After 28 days:** inspect the actual conversations, profile/access actions and repeated requests. Compare only posts old enough for the chosen observation window; keep newer posts pending. Compare topics and formats cautiously. Small samples, topic changes and audience growth make causal claims inappropriate; extend the observation period when the signal is sparse.

Change one major variable for the next cycle:

- Relevant replies but little access interest: check clarity of the product boundary and next step.
- Mostly developers on the product account: use more editing-task language and talk to relevant editors; do not automatically post more.
- Strong impressions but few meaningful responses: inspect whether the hook attracted the intended reader and whether the body delivered on it.
- Low reach with useful responses: keep the substance, test a clearer opener or a relevant distribution conversation.
- Repeated independent stories and manageable time: try an extra founder post or product day.
- High effort, duplicate takeaways or distracted building: reduce output or simplify assets.

No reviewed source proves that a specific cadence, opener, asset or two-account setup will bring Temnia customers. This workflow retains enough evidence to learn from the attempt.

## 13. Pre-publication check

- Does the opening give this reader a reason to continue?
- Is there one clear takeaway, distinct from the other X account's copy?
- Is every feature, number, opinion and customer claim supported and correctly scoped?
- Is old work dated honestly, and are plans separated from availability?
- Does the selected asset exist, match the claim and pass the public-material check?
- Is the final text valid for the exact account's composer?
- Is any invitation backed by a functioning, appropriate destination?
- Has Rajesh reviewed this account, copy and media version?

X's standard limit uses weighted counting; normal string length is not enough for all Unicode, emoji and links. Use an existing compatible counter or validate in the composer. Apply that standard cap to accounts without confirmed longer-post access, not to Rajesh's Premium+ founder drafts. His September 8 confirmation is sufficient for drafting; validate the final text in the selected composer when publishing. The product account's entitlement remains unknown. LinkedIn's standard feed limit is 3,000 characters. The [source review](RESEARCH_REVIEW_2026-09-08.md#platform-reference-checks) links the official references.

An unchecked count should be labelled unchecked, not fabricated. Do not add application dependencies merely to count social copy.

## 14. Optional prompts

These are examples to invoke for a content task, not instructions activated by opening this file. Existing user authorization and repository delivery rules still apply.

### Draft the current day

```text
Use docs/build-in-public/TEMNIA_BUILD_IN_PUBLIC_GUIDE.md and the account strategy.
Read today's log, actual verification evidence and recent known posts.
Draft one founder X post and the appropriate weekday LinkedIn post.
If today is a product-account day, draft its independently useful story too.
Offer a second founder X only when it has a distinct supported takeaway.
Lead with reader value; Day N is optional. Keep chapters/coverage as the
product direction. Write copy and concise evidence/asset notes in the daily
log, preserving existing edits. Mark missing evidence or assets clearly.
Do not infer publication, configure accounts or publish from this prompt.
```

### Capture the work without another pack

```text
Update the current Temnia session log with this work's facts, revision,
verification, actual numbers, failures and next step. Note useful unused
angles. Preserve previous sessions and approved copy. Do not create another
competing daily pack or treat a content-only change as a shipped feature.
```

### Review the experiment

```text
Review the available Temnia posts and actual metrics for this trial.
Separate founder X, product X and personal LinkedIn; compare equivalent
observation windows and keep missing values unknown. Assess reader quality,
distinctness, useful conversations, verified access actions and time spent.
Recommend one change for the next cycle, with evidence and uncertainty.
Do not infer causal growth, increase volume by default or invent metrics.
```

## 15. Maintenance

Keep strategy changes in this guide, account roles in the companion and source assessments in the dated review. Record consequential editorial changes in `AGENTS.md` and the session log. Product status belongs in dated evidence, not an evergreen marketing inventory.

Recheck a platform limit or policy when it affects a real posting/integration decision, when the composer behaves differently, or when the source changes. Do not refresh a “research checked” date without actually revisiting the evidence.
