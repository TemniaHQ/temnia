# Temnia — repo rules

## Decisions

**2026-09-03 — Legacy docs are inspiration, not inheritance.** `docs/prd.md` is the
only document carried over from `Mitosia/mitosia-legacy` and is the product's
source of truth for *what* it does. The legacy `tech-stack.md`, `sprint-plan.md`,
`pipeline-architecture.md`, `pipeline-implementation-plan.md`,
`clip-cut-architecture.md`, `episode-to-clips.md`, `clipping-landscape.md`, and
`editor-study.md` are deliberately **not** copied here. The new tech stack, sprint
plan, and pipeline architecture get decided fresh for the monorepo, consulting the
legacy files at `../mitosia-legacy/docs/` for reference when a specific question
comes up. The PRD's header links to those siblings; they will not resolve locally
until the corresponding new document is written.

**2026-09-03 — Build in public is part of the workflow.** Temnia (Mitosia until the 2026-09-03 rename) is rebuilt in public
with a daily post on X and LinkedIn. The strategy is `docs/build-in-public.md`. Every
working session ends by writing `docs/log/YYYY-MM-DD.md` (done / how / went wrong /
numbers / tomorrow / X draft / LinkedIn draft) from what actually happened — real
numbers, dated, no embellishment. Claude drafts; Rajesh edits and posts. The log is
committed with the day's work and is the source of truth for the post.

**2026-09-03 — The product is Temnia, not Mitosia.** The IP India search found an identical
`Mitosia` mark in class 42 (appl. 7804137, filed 20/06/2026, prior use claimed from
10/10/2024) held by a design studio; we were the junior party on every axis and chose to
rename rather than fight. **Temnia** (Greek *temnō*, "I cut" — the root of *atom* and
*epitome*) cleared IP India phonetic and Start-With in classes 42 and 9, USPTO, and WIPO the
same day. Record: `docs/naming.md`, `docs/trademark-filing.md` §8. Use "Temnia" in all new
docs, code, and posts; "Mitosia" survives only in historical records and the legacy repo name.
The GitHub org/repo were recreated under the Temnia name on 2026-09-03; the local directory
and Claude memory key may still say `mitosia` — treat that as a pending mechanical task, not
a naming ambiguity.

**2026-09-04 — Positioning leads with the coverage lane, not moments.** Temnia's public story is
cutting the whole long-form source into correct chapters and segments (the exact-cover coverage
lane, PRD §9), not mining it for short highlight clips. Competitors are content with imperfect
moment clips because a highlight is forgiving; a chapter partition is not (every second in exactly
one chapter or a deliberate drop, one cut point per shared silence, a wrong boundary breaks both
neighbours), and that strictness is the reason the harness design exists. Moments remain a lane and
are described as what hangs off the chapter spine. Rajesh's call during the Day 0 draft review.
Every post, the landing page, and the pitch shape in `docs/build-in-public.md` §6b follow it. PRD
§1.2 still lists the coverage lane last in the clipping-tools bullet and should lead with it at the
next PRD revision.
