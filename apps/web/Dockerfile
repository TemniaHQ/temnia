# syntax=docker/dockerfile:1
# Build context is the repository root: `docker build -f apps/web/Dockerfile .`
# turbo prune keeps only what @temnia/web needs, so a pipeline-only change does
# not invalidate the web image's dependency layer.
ARG NODE_IMAGE=node:24-alpine3.22

FROM ${NODE_IMAGE} AS base
ARG PNPM_VERSION=12.3.4
# pnpm 12 is a native binary; its npm package carries the musl build as an
# optional dependency, so the registry is the only network the base stage needs.
RUN apk add --no-cache libc6-compat \
  && npm install -g "pnpm@${PNPM_VERSION}" --loglevel=error \
  && pnpm --version
ENV PNPM_HOME=/pnpm
ENV PATH=${PNPM_HOME}:${PATH}
WORKDIR /app

FROM base AS pruner
COPY . .
RUN pnpm dlx turbo@2.10.12 prune @temnia/web --docker

FROM base AS installer
# Manifests first so the dependency layer caches across source changes.
COPY --from=pruner /app/out/json/ .
COPY --from=pruner /app/out/pnpm-lock.yaml ./pnpm-lock.yaml
COPY --from=pruner /app/out/pnpm-workspace.yaml ./pnpm-workspace.yaml
# Root lifecycle scripts must exist before install or `prepare` fails the build.
COPY scripts/install-git-hooks.mjs ./scripts/install-git-hooks.mjs
RUN pnpm install --frozen-lockfile
COPY --from=pruner /app/out/full/ .
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm turbo run build --filter=@temnia/web

FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S temnia && adduser -S temnia -G temnia
COPY --from=installer --chown=temnia:temnia /app/apps/web/.next/standalone ./
COPY --from=installer --chown=temnia:temnia /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=installer --chown=temnia:temnia /app/apps/web/public ./apps/web/public
USER temnia
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/api/health" >/dev/null || exit 1
CMD ["node", "apps/web/server.js"]
