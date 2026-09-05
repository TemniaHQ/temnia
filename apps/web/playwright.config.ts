import { defineConfig, devices } from "@playwright/test";

// The gate sets E2E_BASE_URL to the web image running against the compose
// Temporal server and a worker from the pipeline image. Without it, Playwright
// starts `next dev` and expects a worker to be running already (`pnpm worker`).
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  forbidOnly: Boolean(process.env.CI),
  fullyParallel: true,
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  reporter: process.env.CI ? "list" : "html",
  retries: 0,
  testDir: "./e2e",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  ...(process.env.E2E_BASE_URL
    ? {}
    : {
        webServer: {
          command: "pnpm dev",
          reuseExistingServer: true,
          timeout: 120_000,
          url: baseURL,
        },
      }),
});
