/**
 * Driving the Uppy Dashboard from Playwright.
 *
 * The Dashboard does not auto-proceed: picking a file only stages it, and the
 * upload starts when its own "Upload N files" button is clicked. A spec that
 * calls `setInputFiles` and then waits for `upload-done` waits for something
 * that was never started, which is a 60-second timeout and nothing else. Both
 * specs go through here so that step cannot be forgotten again.
 */
import { expect, type Page } from "@playwright/test";

const UPLOAD_BUTTON = /^Upload \d+ files?$/;
const UPLOAD_DONE_TIMEOUT_MS = 60_000;

/**
 * A fixed identity for generated files (name, size, lastModified) so the
 * server-side fingerprint matches across picks.
 */
export const FIXED_LAST_MODIFIED = 1_700_000_000_000;

/** Uppy's Dashboard keeps its file input hidden; Playwright can still set it. */
export function dashboardInput(page: Page) {
  return page.locator("input.uppy-Dashboard-input").first();
}

/**
 * Picks a generated file with a fixed identity (name, size, lastModified) so
 * the server-side fingerprint matches across picks. A DataTransfer, because
 * setInputFiles stamps lastModified with the current time.
 */
export async function pickGenerated(
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

/** The Dashboard's own start button; nothing uploads until it is clicked. */
export function clickUpload(page: Page) {
  return page.getByRole("button", { name: UPLOAD_BUTTON }).click();
}

/** The line the uploader writes once the source row exists and is queued. */
export function expectUploadDone(
  page: Page,
  timeout: number = UPLOAD_DONE_TIMEOUT_MS
) {
  return expect(page.getByTestId("upload-done")).toBeVisible({ timeout });
}

/** Pick a fixture from disk, start the upload, and wait for it to finish. */
export async function uploadFixture(
  page: Page,
  fixture: string,
  timeout: number = UPLOAD_DONE_TIMEOUT_MS
): Promise<void> {
  await dashboardInput(page).setInputFiles(fixture);
  await clickUpload(page);
  await expectUploadDone(page, timeout);
}
