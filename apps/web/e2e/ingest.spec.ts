import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Playwright runs specs from apps/web.
const FIXTURE = resolve(process.cwd(), "e2e/fixtures/master-24s.mp4");
const INGEST_TIMEOUT_MS = 180_000;
const PROJECT_URL = /\/projects\/[0-9a-f-]{36}$/;
const SOURCE_URL = /\/sources\//;
const PART_URL = /\/temnia-media\//;
const PART_NUMBER = /partNumber=(\d+)/;
const STARTED = /uploaded|processing/;

async function createProject(page: Page, name: string): Promise<string> {
  await page.goto("/projects");
  await page.getByLabel("New project").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.waitForURL(PROJECT_URL);
  return page.url().split("/").pop() ?? "";
}

test("a master uploaded in parts becomes a playable source", async ({
  page,
}) => {
  test.setTimeout(INGEST_TIMEOUT_MS + 60_000);
  await createProject(page, `ingest ${Date.now()}`);

  await page.getByTestId("master-file-input").setInputFiles(FIXTURE);
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
  const partSize = Number(process.env.UPLOAD_PART_SIZE_BYTES);
  const grace = Number(process.env.UPLOAD_ADOPT_GRACE_SECONDS);
  test.skip(
    !(partSize && grace),
    "needs UPLOAD_PART_SIZE_BYTES and UPLOAD_ADOPT_GRACE_SECONDS: a 12 MB file with three parts and a short adoption grace"
  );
  const projectId = await createProject(page, `resume ${Date.now()}`);
  const name = `resume-${projectId.slice(0, 8)}.mp4`;
  const size = Math.floor(2.4 * partSize);

  // Half one: a browser started this upload and put exactly one part in the
  // store before it went away. Done through the real endpoints from the page,
  // with a File whose identity (name, size, lastModified) is fixed.
  await page.evaluate(
    async ({
      projectId: project,
      name: fileName,
      size: fileSize,
      partSize: part,
    }) => {
      const bytes = new Uint8Array(fileSize);
      for (let i = 0; i < bytes.length; i += 4096) {
        bytes[i] = i % 251;
      }
      const file = new File([bytes], fileName, {
        lastModified: 1_700_000_000_000,
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
        (r) => r.json() as Promise<{ uploadId: string; partCount: number }>
      );
      if (session.partCount !== 3) {
        throw new Error(`expected 3 parts, got ${session.partCount}`);
      }
      const { urls } = await fetch(`/api/uploads/${session.uploadId}/sign`, {
        body: JSON.stringify({ partNumbers: [1] }),
        headers: { "content-type": "application/json" },
        method: "POST",
      }).then((r) => r.json() as Promise<{ urls: { url: string }[] }>);
      const put = await fetch(urls[0]?.url ?? "", {
        body: file.slice(0, part),
        method: "PUT",
      });
      if (!put.ok) {
        throw new Error(`part 1 PUT failed with ${put.status}`);
      }
    },
    { name, partSize, projectId, size }
  );
  await page.waitForTimeout((grace + 1) * 1000);

  // Half two: the same file is picked again (a DataTransfer, so lastModified
  // is the same); the uploader must adopt the upload and PUT only parts 2 and 3.
  const puts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && PART_URL.test(request.url())) {
      puts.push(request.url());
    }
  });
  await page.reload();
  await page.getByTestId("master-file-input").evaluate(
    (input: HTMLInputElement, { name: fileName, size: fileSize }) => {
      const bytes = new Uint8Array(fileSize);
      for (let i = 0; i < bytes.length; i += 4096) {
        bytes[i] = i % 251;
      }
      const file = new File([bytes], fileName, {
        lastModified: 1_700_000_000_000,
        type: "video/mp4",
      });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { name, size }
  );
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
