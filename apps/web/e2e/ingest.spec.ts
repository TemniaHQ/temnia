import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Playwright runs specs from apps/web.
const FIXTURE = resolve(process.cwd(), "e2e/fixtures/master-24s.mp4");
const INGEST_TIMEOUT_MS = 180_000;
const PROJECT_URL = /\/projects\/[0-9a-f-]{36}$/;
const PROJECTS_URL = /\/projects$/;
const SOURCE_URL = /\/sources\//;
const PART_URL = /\/temnia-media\//;
const PART_NUMBER = /partNumber=(\d+)/;
const UPLOAD_ROW_URL = /\/api\/uploads\/[0-9a-f-]{36}$/;
const STARTED = /uploaded|processing/;
const UPLOADED_IN = /Uploaded in\s*\d+s/;
const INGESTED_IN = /Ingested in\s*\d+s/;
const UPLOAD_BUTTON = /^Upload \d+ files?$/;
const FIXED_LAST_MODIFIED = 1_700_000_000_000;

async function createProject(page: Page, name: string): Promise<string> {
  await page.goto("/projects");
  await page.getByLabel("New project").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.waitForURL(PROJECT_URL);
  return page.url().split("/").pop() ?? "";
}

/** Uppy's Dashboard keeps its file input hidden; Playwright can still set it. */
function dashboardInput(page: Page) {
  return page.locator("input.uppy-Dashboard-input").first();
}

/**
 * Picks a generated file with a fixed identity (name, size, lastModified) so
 * the server-side fingerprint matches across picks. A DataTransfer, because
 * setInputFiles stamps lastModified with the current time.
 */
async function pickGenerated(
  page: Page,
  file: { name: string; size: number }
): Promise<void> {
  await dashboardInput(page).evaluate(
    (input: HTMLInputElement, { name, size, lastModified }) => {
      const bytes = new Uint8Array(size);
      for (let i = 0; i < bytes.length; i += 4096) {
        bytes[i] = i % 251;
      }
      const generated = new File([bytes], name, {
        lastModified,
        type: "video/mp4",
      });
      const transfer = new DataTransfer();
      transfer.items.add(generated);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { lastModified: FIXED_LAST_MODIFIED, name: file.name, size: file.size }
  );
}

function clickUpload(page: Page) {
  return page.getByRole("button", { name: UPLOAD_BUTTON }).click();
}

function partSettings() {
  const partSize = Number(process.env.UPLOAD_PART_SIZE_BYTES);
  const grace = Number(process.env.UPLOAD_ADOPT_GRACE_SECONDS);
  test.skip(
    !(partSize && grace),
    "needs UPLOAD_PART_SIZE_BYTES and UPLOAD_ADOPT_GRACE_SECONDS: a 12 MB file with three parts and a short adoption grace"
  );
  return { grace, partSize };
}

test("a master uploaded in parts becomes a playable source", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 60_000);
  await createProject(page, `ingest ${Date.now()}`);

  await dashboardInput(page).setInputFiles(FIXTURE);
  await clickUpload(page);
  await expect(page.getByTestId("upload-done")).toBeVisible({
    timeout: 60_000,
  });

  const row = page.locator("[data-source-id]").first();
  await expect(row).toHaveAttribute("data-status", "ready", {
    timeout: INGEST_TIMEOUT_MS,
  });
  await expect(row).toContainText("0:24");

  await row.getByRole("link").click();
  await page.waitForURL(SOURCE_URL);
  await expect(page.getByTestId("source-details")).toContainText("640×360");
  await expect(page.getByTestId("source-details")).toContainText("25 fps");
  await expect(page.getByTestId("source-details")).toContainText(UPLOADED_IN);
  await expect(page.getByTestId("source-details")).toContainText(INGESTED_IN);

  const video = page.locator("video");
  await expect
    .poll(async () => video.evaluate((v: HTMLVideoElement) => v.readyState), {
      timeout: 30_000,
    })
    .toBeGreaterThanOrEqual(1);
  const duration = await video.evaluate((v: HTMLVideoElement) => v.duration);
  expect(duration).toBeGreaterThan(23);
  expect(duration).toBeLessThan(25);

  await expect(page.getByTestId("waveform-overview")).toHaveAttribute(
    "data-waveform-state",
    "ready",
    {
      timeout: 30_000,
    }
  );
  const painted = await page.getByTestId("waveform-overview").evaluate((el) => {
    const canvas = el.querySelector("canvas");
    if (!canvas) {
      return 0;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return 0;
    }
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    let nonBlank = 0;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] !== 0) {
        nonBlank += 1;
      }
    }
    return nonBlank;
  });
  expect(painted).toBeGreaterThan(100);

  await page.getByRole("tab", { name: "Artifacts" }).click();
  const artifacts = page.getByTestId("artifacts-table");
  for (const kind of ["hls", "peaks", "thumbnails", "audio", "shots"]) {
    // biome-ignore lint/performance/noAwaitInLoops: assertions read better in order
    await expect(artifacts).toContainText(kind);
  }
});

test("the media proxy refuses keys outside the caller's organization", async ({
  request,
}) => {
  const other = "0192e8a0-0000-7000-8000-000000000003";
  const response = await request.get(
    `/api/media/org/${other}/source/x/hls/master.m3u8`
  );
  expect(response.status()).toBe(404);
  const traversal = await request.get(
    `/api/media/org/0192e8a0-0000-7000-8000-000000000001/../${other}/x`
  );
  expect(traversal.status()).toBe(404);
});

test("an upload whose first part is already stored resumes from it", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const { partSize } = partSettings();
  const projectId = await createProject(page, `resume ${Date.now()}`);
  const name = `resume-${projectId.slice(0, 8)}.mp4`;
  const size = Math.floor(2.4 * partSize);

  // Half one: a browser started this upload and put exactly one part in the
  // store before it went away. Done through the real endpoints from the page,
  // the way Uppy's signRequest does it, with a File whose identity is fixed.
  await page.evaluate(
    async ({
      projectId: project,
      name: fileName,
      size: fileSize,
      partSize: part,
      lastModified,
    }) => {
      const bytes = new Uint8Array(fileSize);
      for (let i = 0; i < bytes.length; i += 4096) {
        bytes[i] = i % 251;
      }
      const file = new File([bytes], fileName, {
        lastModified,
        type: "video/mp4",
      });
      const session = await fetch("/api/uploads", {
        body: JSON.stringify({
          lastModified: file.lastModified,
          name: file.name,
          projectId: project,
          size: file.size,
          type: file.type,
        }),
        headers: { "content-type": "application/json" },
        method: "POST",
      }).then(
        (r) =>
          r.json() as Promise<{
            key: string;
            multipartUploadId: string;
            partCount: number;
            uploadId: string;
          }>
      );
      if (session.partCount !== 3) {
        throw new Error(`expected 3 parts, got ${session.partCount}`);
      }
      const { url } = await fetch(`/api/uploads/${session.uploadId}/sign`, {
        body: JSON.stringify({
          key: session.key,
          method: "PUT",
          partNumber: 1,
          uploadId: session.multipartUploadId,
        }),
        headers: { "content-type": "application/json" },
        method: "POST",
      }).then((r) => r.json() as Promise<{ url: string }>);
      const put = await fetch(url, {
        body: file.slice(0, part),
        method: "PUT",
      });
      if (!put.ok) {
        throw new Error(`part 1 PUT failed with ${put.status}`);
      }
    },
    {
      lastModified: FIXED_LAST_MODIFIED,
      name,
      partSize,
      projectId,
      size,
    }
  );
  // No waiting here: the re-pick lands inside the grace window on purpose,
  // so the widget must show the countdown and then adopt on its own.

  // Half two: the same file is picked again while the first upload is still
  // inside the grace window; the widget waits it out, adopts the upload,
  // lists the store's parts, and PUTs only parts 2 and 3.
  const puts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && PART_URL.test(request.url())) {
      puts.push(request.url());
    }
  });
  await page.reload();
  await pickGenerated(page, { name, size });
  await clickUpload(page);
  await expect(page.getByTestId("upload-waiting")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("upload-done")).toBeVisible({
    timeout: 60_000,
  });
  expect(puts.map((u) => u.match(PART_NUMBER)?.[1]).sort()).toEqual(["2", "3"]);

  // The store's part 1 plus the browser's 2 and 3 completed the upload; the
  // random bytes then fail the probe, which is the terminal failure path.
  const row = page.locator("[data-source-id]").first();
  await expect(row).toHaveAttribute("data-status", "failed", {
    timeout: 60_000,
  });
  await expect(row).toContainText("could not be read");

  // The row menu: retry runs the ingest again (and fails again, same bytes),
  // delete removes the row and everything under its prefix.
  await row.getByTestId("source-actions").click();
  await page.getByRole("menuitem", { name: "Retry ingest" }).click();
  await expect(row).toHaveAttribute("data-status", STARTED, {
    timeout: 15_000,
  });
  await expect(row).toHaveAttribute("data-status", "failed", {
    timeout: 60_000,
  });

  await row.getByTestId("source-actions").click();
  await page.getByRole("menuitem", { name: "Delete source" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByTestId("confirm-delete").click();
  await expect(page.locator("[data-source-id]")).toHaveCount(0, {
    timeout: 30_000,
  });
});

test("cancel aborts the upload; leaving the page keeps it resumable", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const { partSize } = partSettings();
  const projectId = await createProject(page, `cancel ${Date.now()}`);
  const projectUrl = page.url();
  const name = `cancel-${projectId.slice(0, 8)}.mp4`;
  const size = Math.floor(2.4 * partSize);

  // Every part takes a while, so there is time to act mid-upload.
  await page.route(PART_URL, async (route) => {
    if (route.request().method() === "PUT") {
      await new Promise((r) => setTimeout(r, 2500));
    }
    await route.continue();
  });
  const deletes: string[] = [];
  const listings: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "DELETE" && UPLOAD_ROW_URL.test(request.url())) {
      deletes.push(request.url());
    }
    if (request.method() === "GET" && PART_URL.test(request.url())) {
      listings.push(request.url());
    }
  });

  // Cancel from the Dashboard: Uppy's abort is the app's own DELETE route,
  // which aborts at the store and removes the row that never became content.
  await pickGenerated(page, { name, size });
  await clickUpload(page);
  await page.waitForRequest(
    (request) => request.method() === "PUT" && PART_URL.test(request.url())
  );
  await expect(page.locator("[data-source-id]")).toHaveCount(1, {
    timeout: 15_000,
  });
  const aborted = page.waitForResponse(
    (response) =>
      response.request().method() === "DELETE" &&
      UPLOAD_ROW_URL.test(response.url())
  );
  // The status bar's cancel (the file card and the row menu have their own).
  await page.locator('button[data-cy="cancel"]').click();
  expect((await aborted).status()).toBe(204);
  await expect(page.locator("[data-source-id]")).toHaveCount(0, {
    timeout: 15_000,
  });

  // Leave mid-upload by an in-app link: the component unmounts, Uppy tries
  // to abort, and the client refuses to sign it. No DELETE reaches the app,
  // the row is still uploading, and the same file picked again carries on.
  await pickGenerated(page, { name, size });
  await clickUpload(page);
  await page.waitForRequest(
    (request) => request.method() === "PUT" && PART_URL.test(request.url())
  );
  const deletesBefore = deletes.length;
  await page.getByRole("link", { name: "Projects" }).first().click();
  await page.waitForURL(PROJECTS_URL);
  await page.waitForTimeout(1500);
  expect(deletes.length).toBe(deletesBefore);

  await page.goto(projectUrl);
  await expect(page.locator("[data-source-id]").first()).toHaveAttribute(
    "data-status",
    "uploading"
  );
  // The grace window (2 s in the gate) has passed by now, so adoption is
  // immediate; the proof of resume is Uppy listing the store's parts first.
  const listingsBefore = listings.length;
  await pickGenerated(page, { name, size });
  await clickUpload(page);
  await expect(page.getByTestId("upload-done")).toBeVisible({
    timeout: 90_000,
  });
  expect(listings.length).toBeGreaterThan(listingsBefore);
  await expect(page.locator("[data-source-id]")).toHaveCount(1);
  await expect(page.locator("[data-source-id]").first()).not.toHaveAttribute(
    "data-status",
    "uploading"
  );
});
