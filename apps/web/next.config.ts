import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Dockerfile copies .next/standalone; the image carries no node_modules of its own.
  output: "standalone",
  // The release phase (instrumentation.ts) applies migrations from the db
  // package's SQL folder, which no import traces; include it in the
  // standalone output at its mirrored path.
  outputFileTracingIncludes: { "/": ["../../packages/db/drizzle/**"] },
  // Trace from the monorepo root so workspace packages land in the standalone output.
  outputFileTracingRoot: join(
    dirname(fileURLToPath(import.meta.url)),
    "../../"
  ),
  reactCompiler: true,
  // The Temporal client loads native gRPC bindings; it must not be bundled.
  serverExternalPackages: ["@temporalio/client", "pg"],
  // Workspace packages ship TypeScript source; Next compiles them in place.
  transpilePackages: ["@temnia/contracts", "@temnia/db"],
  typedRoutes: true,
};

export default nextConfig;
