/**
 * The transcript tab, driven by the recorded provider.
 *
 * One upload carries the whole reading surface, because an ingest is the
 * expensive part and every assertion below wants the same source: the waiting
 * states, click-to-seek, follow, search, a correction and the revision it
 * writes, a speaker rename that reaches the export, the stale-revision refusal
 * between two contexts, and the tooltip on an uncertain word. The failure
 * states get a source each, because the recording that produces them is chosen
 * by the fixture's duration: the 24-second master selects a retryable failure
 * and the 12-second one a failure the runner classifies as terminal. That is
 * how the retrying state and the failed state are each reached in seconds,
 * without a test sitting out the 30/60/120-second retry ladder that production
 * really walks.
 *
 * The console listener is the hydration assertion. Under `next dev` a mismatch
 * is invisible (the S1 React #418 lesson), but this same spec is what the gate
 * runs against the web image, which is a production build, and there it is
 * real. There is no `pnpm e2e:prod` script in this repository to run it under.
 */
import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import speech from "../tests/fixtures/speech-40s.transcript.json" with {
  type: "json",
};

const SPEECH = resolve(process.cwd(), "e2e/fixtures/speech-40s.mp4");
// Two silent masters, and the duration is the whole difference between them:
// 24 s matches `unavailable.whisperx.json`, whose failure is retried, and
// 12 s matches `contract-error.whisperx.json`, whose failure is not.
const RETRIED = resolve(process.cwd(), "e2e/fixtures/master-24s.mp4");
const TERMINAL = resolve(process.cwd(), "e2e/fixtures/master-12s.mp4");
const PROJECT_URL = /\/projects\/[0-9a-f-]{36}$/;
const SOURCE_URL = /\/sources\/([0-9a-f-]{36})/;
const REVISION_URL = /transcript\/rev-\d+\.json/;
// The last two are what a nesting mistake looks like: the parser closes the
// offending tag and the server HTML and the client tree stop agreeing, which
// on a production build is React #418 and nothing else (the S1 lesson).
const HYDRATION =
  /Minified React error #(418|423|425)|Hydration failed|cannot contain a nested|validateDOMNesting/;
// Every state a healthy run passes through before Ready: on a fast machine
// the tab can be caught in any of them.
const WAITING_STATE = /notReady|pending|processing/;
const WAITING_WORDS =
  /The transcript starts after processing finishes\.|Queued for transcription\.|Transcribing/;
const RESTARTED_STATE = /pending|processing/;
const INGEST_TIMEOUT_MS = 180_000;
// Either failure state is on the row within a second or two of the ingest
// finishing; the rest of this is the tab's own 3.5 s poll and a slow machine.
const FAILURE_TIMEOUT_MS = 60_000;

const WORDS = speech.words;

/** The last word that had started by `ms`; the viewer's own rule. */
function wordAt(ms: number): number {
  let found = -1;
  for (const [index, word] of WORDS.entries()) {
    if (word.startMs <= ms) {
      found = index;
    }
  }
  return found;
}

async function createProject(page: Page, name: string): Promise<void> {
  await page.goto("/projects");
  await page.getByLabel("New project").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.waitForURL(PROJECT_URL);
}

/** Upload a master, wait out the ingest, and land on its source page. */
async function ingest(page: Page, fixture: string): Promise<string> {
  await page
    .locator("input.uppy-Dashboard-input")
    .first()
    .setInputFiles(fixture);
  await expect(page.getByTestId("upload-done")).toBeVisible({
    timeout: 60_000,
  });
  const row = page.locator("[data-source-id]").first();
  await expect(row).toHaveAttribute("data-status", "ready", {
    timeout: INGEST_TIMEOUT_MS,
  });
  await row.getByRole("link").click();
  await page.waitForURL(SOURCE_URL);
  return SOURCE_URL.exec(page.url())?.[1] ?? "";
}

function watchConsole(page: Page): string[] {
  const complaints: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && HYDRATION.test(message.text())) {
      complaints.push(message.text());
    }
  });
  page.on("pageerror", (error) => {
    if (HYDRATION.test(error.message)) {
      complaints.push(error.message);
    }
  });
  return complaints;
}

async function openTranscript(page: Page) {
  await page.getByRole("tab", { name: "Transcript" }).click();
  return page.getByTestId("transcript-tab");
}

test("a recorded transcript is read, searched, corrected, and exported", async ({
  browser,
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 180_000);
  const complaints = watchConsole(page);
  await createProject(page, `transcript ${Date.now()}`);

  // The tab has words while the ingest is still running, and never a spinner
  // with nothing to read.
  await page
    .locator("input.uppy-Dashboard-input")
    .first()
    .setInputFiles(SPEECH);
  await expect(page.getByTestId("upload-done")).toBeVisible({
    timeout: 60_000,
  });
  const listRow = page.locator("[data-source-id]").first();
  await listRow.getByRole("link").click();
  await page.waitForURL(SOURCE_URL);
  const sourceId = SOURCE_URL.exec(page.url())?.[1] ?? "";
  const tab = await openTranscript(page);
  // The ingest of this fixture takes about two seconds on a developer's
  // machine, so the waiting state can be gone before the page is even open;
  // its words are asserted while it is still there. The failed test below
  // watches a waiting state (retrying) with no race in it at all.
  if ((await tab.getAttribute("data-state")) !== "ready") {
    await expect(tab).toHaveAttribute("data-state", WAITING_STATE);
    await expect(tab).toContainText(WAITING_WORDS);
  }

  // …and it gets there on its own, without a reload: the tab polls.
  await expect(tab).toHaveAttribute("data-state", "ready", {
    timeout: INGEST_TIMEOUT_MS,
  });
  await expect(page.getByTestId("transcript-words")).toBeVisible();
  await expect(page.locator("[data-word]").first()).toHaveText(
    WORDS[0]?.text ?? ""
  );

  const video = page.locator("video");
  await expect
    .poll(async () => video.evaluate((v: HTMLVideoElement) => v.readyState), {
      timeout: 30_000,
    })
    .toBeGreaterThanOrEqual(1);

  // Click a word: the player is the one beside the tab, not a second element.
  const target = 12;
  await page.locator(`[data-word="${target}"]`).click();
  await expect
    .poll(async () =>
      video.evaluate((v: HTMLVideoElement) => Math.abs(v.currentTime))
    )
    .toBeGreaterThan(0);
  const seeked = await video.evaluate((v: HTMLVideoElement) => v.currentTime);
  expect(
    Math.abs(seeked - (WORDS[target]?.startMs ?? 0) / 1000)
  ).toBeLessThanOrEqual(0.25);

  // Play to five seconds: the word being spoken there is the highlighted one.
  await video.evaluate((v: HTMLVideoElement) => {
    v.muted = true;
    v.currentTime = 0;
    return v.play();
  });
  await expect
    .poll(async () => video.evaluate((v: HTMLVideoElement) => v.currentTime), {
      timeout: 30_000,
    })
    .toBeGreaterThan(5);
  await video.evaluate((v: HTMLVideoElement) => v.pause());
  const at = await video.evaluate((v: HTMLVideoElement) => v.currentTime);
  const active = page.locator('[data-word][data-active="true"]');
  await expect(active).toHaveCount(1);
  expect(Number(await active.getAttribute("data-word"))).toBe(
    wordAt(at * 1000)
  );

  // Search: the count is the transcript's, and Next moves the focus.
  const hits = WORDS.filter((word) =>
    word.text.toLowerCase().includes("about")
  ).length;
  expect(hits).toBeGreaterThan(1);
  await page.getByTestId("transcript-search").fill("about");
  await expect(page.getByTestId("transcript-match-count")).toHaveText(
    `1/${hits}`
  );
  const first = await page
    .locator('[data-word][data-focused="true"]')
    .getAttribute("data-word");
  await page.getByTestId("transcript-next").click();
  await expect(page.getByTestId("transcript-match-count")).toHaveText(
    `2/${hits}`
  );
  await expect(
    page.locator('[data-word][data-focused="true"]')
  ).not.toHaveAttribute("data-word", first ?? "");
  await page.getByTestId("transcript-search").press("Escape");
  await expect(page.getByTestId("transcript-match-count")).toHaveCount(0);

  // A search and follow-scroll both want the pane, and the search asked for it
  // first: follow is off, and Jump to current is how it starts again.
  const follow = page.getByTestId("transcript-follow");
  await expect(follow).toHaveAttribute("aria-pressed", "false");
  await page.getByTestId("transcript-jump").click();
  await expect(follow).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("transcript-jump")).toHaveCount(0);

  // A wheel of the reader's own stops it too, without touching the toggle.
  await page.getByTestId("transcript-words").hover();
  await page.mouse.wheel(0, 240);
  await expect(follow).toHaveAttribute("aria-pressed", "false");
  await follow.click();
  await expect(follow).toHaveAttribute("aria-pressed", "true");

  // A correction writes the next revision, and it is there after a reload.
  await page.getByTestId("transcript-edit-mode").click();
  await page.locator('[data-word="0"]').click();
  await page.getByTestId("transcript-word-input").fill("Welcome!");
  await page.getByTestId("transcript-word-save").click();
  await expect(page.locator('[data-word="0"]')).toHaveText("Welcome!", {
    timeout: 20_000,
  });
  await page.reload();
  await openTranscript(page);
  await expect(page.locator('[data-word="0"]')).toHaveText("Welcome!", {
    timeout: 20_000,
  });
  const exported = await page.request.get(
    `/api/sources/${sourceId}/transcript.vtt`
  );
  expect(exported.headers().etag).toBe('"rev-2"');

  // The speaker chip's menu, and the dialog behind its Rename item.
  await page.getByTestId("speaker-chip").first().click();
  await expect(page.getByRole("menuitem", { name: "Rename…" })).toBeVisible();
  await page.getByRole("menuitem", { name: "Rename…" }).click();
  const dialog = page.getByTestId("speakers-dialog");
  await expect(dialog).toBeVisible();
  await page.getByTestId("speaker-name-0").fill("Priya");
  await page.getByTestId("speaker-name-1").fill("Priya");
  await expect(page.getByTestId("speakers-merge-notice")).toBeVisible();
  await page.getByTestId("speaker-name-1").fill("Sam");
  await expect(page.getByTestId("speakers-merge-notice")).toHaveCount(0);
  await page.getByTestId("speakers-save").click();
  await expect(dialog).toBeHidden({ timeout: 20_000 });
  await expect(page.getByTestId("speaker-chip").first()).toHaveText("Priya");

  const vtt = await page.request.get(`/api/sources/${sourceId}/transcript.vtt`);
  expect(await vtt.text()).toContain("<v Priya>");

  // Reassigning a turn is a correction like any other: revision three.
  await page.getByTestId("speaker-chip").first().click();
  await page.getByRole("menuitem", { name: "Sam" }).click();
  await expect
    .poll(
      async () =>
        (
          await page.request.get(`/api/sources/${sourceId}/transcript.srt`)
        ).headers().etag,
      { timeout: 20_000 }
    )
    .toBe('"rev-3"');

  // Two contexts on the same revision: the second save is refused with words.
  const other = await browser.newContext();
  const second = await other.newPage();
  await second.goto(`/sources/${sourceId}`);
  await openTranscript(second);
  await expect(second.getByTestId("transcript-words")).toBeVisible({
    timeout: 20_000,
  });
  await page.reload();
  await openTranscript(page);
  await expect(page.getByTestId("transcript-words")).toBeVisible({
    timeout: 20_000,
  });

  await page.getByTestId("transcript-edit-mode").click();
  await page.locator('[data-word="1"]').click();
  await page.getByTestId("transcript-word-input").fill("again");
  await page.getByTestId("transcript-word-save").click();
  await expect(page.locator('[data-word="1"]')).toHaveText("again", {
    timeout: 20_000,
  });

  await second.getByTestId("transcript-edit-mode").click();
  await second.locator('[data-word="2"]').click();
  await second.getByTestId("transcript-word-input").fill("towards");
  await second.getByTestId("transcript-word-save").click();
  await expect(second.getByTestId("transcript-stale")).toBeVisible({
    timeout: 20_000,
  });
  await expect(second.getByTestId("transcript-stale")).toContainText(
    "Someone changed this transcript since you opened it."
  );
  await second.getByTestId("transcript-reload").click();
  await expect(second.getByTestId("transcript-stale")).toHaveCount(0);
  await other.close();

  expect(complaints).toEqual([]);
});

test("an uncertain word is marked and says how sure the engine was", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 60_000);
  await createProject(page, `uncertain ${Date.now()}`);
  const sourceId = await ingest(page, SPEECH);

  // The committed recording has no low-confidence word in it, and a fixture is
  // not the place to invent one: the revision is rewritten on the way to the
  // browser instead, which is the only thing this assertion is about.
  await page.route(REVISION_URL, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as {
      words: { confidence: number | null; timing: string }[];
    };
    const [, , , word] = body.words;
    if (word) {
      word.confidence = 0.21;
      word.timing = "interpolated";
    }
    await route.fulfill({ json: body, response });
  });
  await page.goto(`/sources/${sourceId}`);
  await openTranscript(page);
  const uncertain = page.locator('[data-word="3"]');
  await expect(uncertain).toBeVisible({ timeout: 20_000 });
  await expect(uncertain).toHaveAttribute("data-uncertain", "true");
  await expect(uncertain).toHaveAttribute("data-timing", "interpolated");
  await uncertain.hover();
  // Base UI 1.8's tooltip popup carries no `role`, so it is found by the slot
  // the registry component stamps on it rather than by role.
  await expect(page.locator('[data-slot="tooltip-content"]')).toContainText(
    "21% sure"
  );
});

test("a transcription with another attempt coming says it is being retried", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + FAILURE_TIMEOUT_MS);
  const complaints = watchConsole(page);
  await createProject(page, `retrying ${Date.now()}`);
  await ingest(page, RETRIED);
  const tab = await openTranscript(page);

  // The row never flashes Failed between two attempts; it parks at retrying.
  await expect(tab).toHaveAttribute("data-state", "retrying", {
    timeout: FAILURE_TIMEOUT_MS,
  });
  await expect(tab).toContainText(
    "Transcription stopped unexpectedly and is being retried."
  );
  expect(complaints).toEqual([]);

  // This is where the retryable recording is left. Its remaining attempts are
  // 30, 60 and 120 seconds away, which is production's policy and stays that
  // way; the failed state it eventually reaches is the test below, from a
  // recording that gets there on attempt one.
});

test("a transcription that cannot succeed says so and offers a retry", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + FAILURE_TIMEOUT_MS);
  const complaints = watchConsole(page);
  await createProject(page, `failed ${Date.now()}`);
  await ingest(page, TERMINAL);
  const tab = await openTranscript(page);

  // `TranscriptContractError` is terminal in transcription/runner.py, so there
  // is no ladder to wait out: the workflow writes the failure to the row after
  // the first attempt, and the tab's next poll reads it.
  await expect(tab).toHaveAttribute("data-state", "failed", {
    timeout: FAILURE_TIMEOUT_MS,
  });
  await expect(tab).toContainText(
    "Transcription failed: the transcription service was unavailable."
  );
  expect(complaints).toEqual([]);

  // Retry is a second claim: the action parks the row back at pending and the
  // worker takes it again. What the tab shows next is that new attempt, and
  // this stops there rather than following it to the failure it repeats.
  await expect(page.getByTestId("transcript-retry")).toBeVisible();
  await page.getByTestId("transcript-retry").click();
  await expect(tab).toHaveAttribute("data-state", RESTARTED_STATE, {
    timeout: 30_000,
  });
});
