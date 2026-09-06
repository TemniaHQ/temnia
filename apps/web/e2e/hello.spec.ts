import { expect, test } from "@playwright/test";

const SEEDED_ORGANIZATION_ID = "0192e8a0-0000-7000-8000-000000000001";

test("a server action runs the hello workflow on the Python worker", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Name").fill("gate");
  await page.getByRole("button", { name: "Run the hello workflow" }).click();

  const result = page.getByTestId("hello-result");
  await expect(result).toContainText("Hello, gate.", { timeout: 30_000 });
  await expect(result).toContainText(SEEDED_ORGANIZATION_ID);
  await expect(result).toContainText("python");
});

test("the health route answers", async ({ request }) => {
  const response = await request.get("/api/health");
  expect(response.ok()).toBe(true);
  expect(await response.json()).toEqual({ ok: true, service: "temnia-web" });
});
