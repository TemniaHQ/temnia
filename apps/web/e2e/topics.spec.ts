/** Real action, workflow, detector and media; synthetic model judgments only. */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import {
  ChapterRendersSchema,
  TopicAssessmentSchema,
  TopicEditSpecSchema,
  TopicExportSchema,
  TopicRendersSchema,
} from "@temnia/contracts";
import type { ChapterView } from "../lib/harness/queries";
import {
  DEFAULT_TOPIC_BRIEF_VERSION,
  resolveTopicBrief,
} from "../lib/harness/topic-defaults";
import { uploadFixture } from "./helpers/upload";

const SPEECH = resolve(process.cwd(), "e2e/fixtures/speech-40s.mp4");
const PROJECT_URL = /\/projects\/[0-9a-f-]{36}$/;
const SOURCE_URL = /\/sources\/([0-9a-f-]{36})/;
const STAGE_TIMEOUT = 120_000;

async function readView(page: Page, sourceId: string): Promise<ChapterView> {
  const response = await page.request.get(`/api/sources/${sourceId}/topics`);
  expect(response.ok()).toBe(true);
  return response.json();
}

async function artifact(page: Page, view: ChapterView, id: string) {
  const ref = view.artifacts.find((item) => item.id === id);
  expect(ref).toBeDefined();
  const response = await page.request.get(ref?.url ?? "");
  expect(response.ok()).toBe(true);
  return response.json();
}

test("default topic discovery renders and exports only after a human decision", async ({
  page,
}) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/projects");
  await page.getByLabel("New project").fill(`topic harness ${Date.now()}`);
  await page
    .getByRole("button", { exact: true, name: "Create project" })
    .click();
  await page.waitForURL(PROJECT_URL);
  await uploadFixture(page, SPEECH);
  const source = page.locator("[data-source-id]").first();
  await expect(source).toHaveAttribute("data-status", "ready", {
    timeout: STAGE_TIMEOUT,
  });
  await source.getByRole("link").click();
  await page.waitForURL(SOURCE_URL);
  const sourceId = SOURCE_URL.exec(page.url())?.[1] ?? "";
  await page.getByRole("tab", { exact: true, name: "Transcript" }).click();
  await expect(page.getByTestId("transcript-tab")).toHaveAttribute(
    "data-state",
    "ready",
    {
      timeout: STAGE_TIMEOUT,
    }
  );
  await page.getByRole("tab", { exact: true, name: "Topic videos" }).click();
  const panel = page.getByTestId("topic-panel");
  await expect(panel.getByLabel("Topic instructions")).toBeHidden();
  await panel
    .getByRole("button", { exact: true, name: "Find topic videos" })
    .click();
  await expect
    .poll(
      async () => {
        const view = await readView(page, sourceId);
        return view.run?.status;
      },
      { timeout: STAGE_TIMEOUT }
    )
    .toBe("needs_review");

  const view = await readView(page, sourceId);
  expect(view.run?.brief).toBe(
    resolveTopicBrief({
      defaultBriefVersion: DEFAULT_TOPIC_BRIEF_VERSION,
    })
  );
  expect(view.run?.synthetic).toBe(true);
  expect(view.run?.dispatchCount).toBe(3);
  expect(view.run?.acceptedRevision).toBeNull();
  const edit = TopicEditSpecSchema.parse(
    await artifact(page, view, view.currentEdit?.id ?? "")
  );
  expect(edit.videos).toHaveLength(1);
  const assessmentRef = view.artifacts.find(
    (item) => item.metadata.format === "topic-assessment/1"
  );
  const assessment = TopicAssessmentSchema.parse(
    await artifact(page, view, assessmentRef?.id ?? "")
  );
  expect(assessment.proposerFamily).not.toBe(assessment.verifierFamily);
  expect(assessment.candidates[0]?.coldReview?.completeDiscussion.status).toBe(
    "unknown"
  );
  expect(assessment.candidates[0]?.sourceReview).not.toBeNull();
  const renderRef = view.artifacts.find(
    (item) => item.metadata.format === "topic-renders/1"
  );
  const renders = TopicRendersSchema.parse(
    await artifact(page, view, renderRef?.id ?? "")
  );
  expect(renders.editSha256).toBe(view.currentEdit?.sha256);
  expect(renders.videos).toHaveLength(1);

  const video = panel.locator("article");
  await expect(video).toHaveCount(1);
  await expect(
    video.getByText("Technical checks passed", { exact: true })
  ).toBeVisible();
  await expect(
    panel.getByRole("link", { name: "Download accepted manifest" })
  ).toHaveCount(0);
  await expect(
    video.getByRole("button", { exact: true, name: "Accept video" })
  ).toBeDisabled();
  await video
    .getByRole("textbox")
    .fill("Synthetic mechanics test only; no editorial quality claim.");
  await video
    .getByRole("button", { exact: true, name: "Accept video" })
    .click();
  await expect
    .poll(async () => (await readView(page, sourceId)).run?.status, {
      timeout: STAGE_TIMEOUT,
    })
    .toBe("ready");

  const accepted = await readView(page, sourceId);
  expect(accepted.run?.dispatchCount).toBe(view.run?.dispatchCount);
  expect(accepted.run?.acceptedRevision).toBe(2);
  const manifestLink = panel.getByRole("link", {
    exact: true,
    name: "Download accepted manifest",
  });
  await expect(manifestLink).toBeVisible({ timeout: STAGE_TIMEOUT });
  const manifestUrl = await manifestLink.getAttribute("href");
  const manifest = TopicExportSchema.parse(
    await (await page.request.get(manifestUrl ?? "")).json()
  );
  expect(manifest.editSha256).toBe(accepted.currentEdit?.sha256);
  expect(manifest.videos.map((item) => item.candidateId)).toEqual(
    edit.videos.map((item) => item.candidate.id)
  );
  const childResponse = await page.request.get(
    `/api/media/${manifest.videos[0]?.descriptor.storageKey}`
  );
  expect(childResponse.ok()).toBe(true);
  const child = ChapterRendersSchema.parse(await childResponse.json());
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    panel.getByRole("link", { name: "Download accepted video:" }).click(),
  ]);
  const bytes = await readFile((await download.path()) ?? "");
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(
    child.renders[0]?.media.sha256
  );
  expect(bytes.length).toBe(child.renders[0]?.media.sizeBytes);
  await page.reload();
  await page.getByRole("tab", { exact: true, name: "Topic videos" }).click();
  await expect(panel.getByText("Accepted by you", { exact: true })).toBeVisible(
    { timeout: STAGE_TIMEOUT }
  );
  const chapters = await page.request.get(`/api/sources/${sourceId}/chapters`);
  expect(((await chapters.json()) as ChapterView).runs).toHaveLength(0);
  expect(errors).toEqual([]);
});
