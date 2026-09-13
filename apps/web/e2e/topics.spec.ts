/** Real action, workflow, detector and media; synthetic model judgments only. */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import {
  ChapterRendersSchema,
  TopicEditSpecSchema,
  TopicExportSchema,
  TopicRendersSchema,
  TopicSelectionAssessmentSchema,
} from "@temnia/contracts";
import type { ChapterView } from "../lib/harness/queries";
import { TOPIC_POLICY } from "../lib/harness/topic-defaults";
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

/** One button, one program: the panel starts `standalone-topics/3` and nothing else. */
const EXPECTED_DISPATCHES = 4;
const INSTRUCTIONS = "Keep the speaker's original language in every title.";

test(`${TOPIC_POLICY} generation, human correction and exact accepted exports`, async ({
  page,
}) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/projects");
  await page
    .getByLabel("New project", { exact: true })
    .fill(`topic harness ${Date.now()}`);
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
  await expect(
    panel.getByLabel("Topic instructions", { exact: true })
  ).toBeHidden();
  await panel.getByText("Optional instructions", { exact: true }).click();
  const instructions = panel.getByLabel("Topic instructions", {
    exact: true,
  });
  await expect(instructions).toBeVisible();
  await instructions.fill(`  ${INSTRUCTIONS} `);
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
  expect(view.run?.brief).toBe(INSTRUCTIONS);
  expect(view.run?.synthetic).toBe(true);
  expect(view.run?.dispatchCount).toBe(EXPECTED_DISPATCHES);
  expect(view.run?.acceptedRevision).toBeNull();
  const edit = TopicEditSpecSchema.parse(
    await artifact(page, view, view.currentEdit?.id ?? "")
  );
  expect(edit.videos).toHaveLength(1);
  const assessment = TopicSelectionAssessmentSchema.parse(
    await artifact(
      page,
      view,
      String(view.currentEdit?.metadata.assessmentArtifactId)
    )
  );
  expect(assessment.selectionSha256).toBe(
    view.currentEdit?.metadata.selectionSha256
  );
  expect(assessment.evidenceSha256).toBe(edit.evidenceSha256);
  expect(assessment.proposerFamily).not.toBe(assessment.verifierFamily);
  expect(assessment.coldReviews[0]?.value.deliveredValue.status).toBe(
    "unknown"
  );
  expect(assessment.portfolioReview?.selection[0]?.disposition).toBe("select");
  expect(assessment.executionStatus).toBe("needs_review");
  expect(edit.compilerVersion).toBe("topic-compiler/3");
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
  ).toBeVisible({ timeout: STAGE_TIMEOUT });
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
  await panel
    .getByRole("button", { exact: true, name: "Edit topic selections" })
    .click();
  await expect(
    panel.getByLabel("Editorial correction", { exact: true })
  ).toBeVisible();
  await panel
    .getByLabel("Editorial correction", { exact: true })
    .selectOption("retitle");
  await panel
    .getByRole("checkbox", {
      exact: true,
      name: edit.videos[0]?.candidate.title ?? "",
    })
    .check();
  await panel
    .getByLabel("Video 1 title", { exact: true })
    .fill("Human-corrected faithful topic title");
  await panel
    .getByLabel("Editorial correction reason", { exact: true })
    .fill(
      "Correct the title without changing the speech. Synthetic validation only."
    );
  await panel
    .getByRole("button", { exact: true, name: "Save editorial correction" })
    .click();
  await expect
    .poll(
      async () => {
        const current = await readView(page, sourceId);
        return `${current.run?.currentRevision}:${current.run?.status}`;
      },
      { timeout: STAGE_TIMEOUT }
    )
    .toBe("3:needs_review");
  const corrected = await readView(page, sourceId);
  expect(corrected.run?.dispatchCount).toBe(view.run?.dispatchCount);
  const correctedEdit = TopicEditSpecSchema.parse(
    await artifact(page, corrected, corrected.currentEdit?.id ?? "")
  );
  expect(correctedEdit.videos[0]?.candidate.title).toBe(
    "Human-corrected faithful topic title"
  );
  expect(
    correctedEdit.videos[0]?.edit.sections.find(
      (section) => section.id === correctedEdit.videos[0]?.keptSectionId
    )?.reviewState
  ).toBe("proposed");
  const correctedDescriptorRef = corrected.artifacts.find(
    (item) =>
      item.metadata.format === "topic-renders/1" &&
      item.metadata.editSha256 === corrected.currentEdit?.sha256
  );
  const correctedDescriptor = TopicRendersSchema.parse(
    await artifact(page, corrected, correctedDescriptorRef?.id ?? "")
  );
  const correctedChild = ChapterRendersSchema.parse(
    await (
      await page.request.get(
        `/api/media/${correctedDescriptor.videos[0]?.descriptor.storageKey}`
      )
    ).json()
  );
  expect(correctedChild.renders[0]?.media.id).toBe(child.renders[0]?.media.id);
  expect(correctedChild.renders[0]?.editSha256).not.toBe(
    child.renders[0]?.editSha256
  );
  const sourceContext = await page.request.get(
    `/api/sources/${sourceId}/topics/context?runId=${view.run?.id}&revision=3`
  );
  expect(sourceContext.ok()).toBe(true);
  expect((await sourceContext.json()).edit.sha256).toBe(
    corrected.currentEdit?.sha256
  );
  const correctedAssessment = TopicSelectionAssessmentSchema.parse(
    await artifact(
      page,
      corrected,
      String(corrected.currentEdit?.metadata.assessmentArtifactId)
    )
  );
  expect(correctedAssessment.coldReviews).toEqual([]);
  expect(correctedAssessment.portfolioReview).toBeNull();
  expect(correctedAssessment.proposerFamily).toBe("human");
  expect(correctedAssessment.executionStatus).toBe("needs_review");
  await expect(
    video.getByText("Human-corrected faithful topic title", { exact: true })
  ).toBeVisible();
  await video
    .getByRole("textbox")
    .fill(
      "Reviewed the revised title and unchanged complete speech. Synthetic mechanics only."
    );
  await video
    .getByRole("button", { exact: true, name: "Accept video" })
    .click();
  await expect
    .poll(
      async () => {
        const current = await readView(page, sourceId);
        return `${current.run?.acceptedRevision}:${current.run?.status}`;
      },
      { timeout: STAGE_TIMEOUT }
    )
    .toBe("4:ready");
  const reaccepted = await readView(page, sourceId);
  expect(reaccepted.run?.dispatchCount).toBe(view.run?.dispatchCount);
  await expect(manifestLink).toBeVisible({ timeout: STAGE_TIMEOUT });
  await expect
    .poll(() => manifestLink.getAttribute("href"))
    .not.toBe(manifestUrl);
  const revisedManifest = TopicExportSchema.parse(
    await (
      await page.request.get((await manifestLink.getAttribute("href")) ?? "")
    ).json()
  );
  expect(revisedManifest.revision).toBe(4);
  expect(revisedManifest.editSha256).toBe(reaccepted.currentEdit?.sha256);
  expect(errors).toEqual([]);
});
