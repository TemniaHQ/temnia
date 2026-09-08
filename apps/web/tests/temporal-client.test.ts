import { expect, it, vi } from "vitest";

const connect = vi.hoisted(() => vi.fn());
vi.mock("@temporalio/client", () => ({
  Client: class MockClient {},
  Connection: { connect },
}));

it("reconnects after a rejected connection instead of caching the failure", async () => {
  vi.resetModules();
  connect
    .mockRejectedValueOnce(new Error("Temporal temporarily unavailable"))
    .mockResolvedValueOnce({});
  const { getTemporalClient } = await import("@/lib/temporal/client");
  await expect(getTemporalClient()).rejects.toThrow("temporarily unavailable");
  const recovered = await getTemporalClient();
  expect(await getTemporalClient()).toBe(recovered);
  expect(connect).toHaveBeenCalledTimes(2);
});
