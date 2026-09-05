# Temnia

The cutting room for the whole episode. Private repository; the product record is
[docs/prd.md](docs/prd.md), the sequence is [docs/sprint-plan.md](docs/sprint-plan.md), the system
design is [docs/tech-stack.md](docs/tech-stack.md), and the working rules are [AGENTS.md](AGENTS.md).

## Layout

| Path | What |
|---|---|
| `apps/web` | Next.js 16 app. A Temporal client; runs no workflow code |
| `apps/pipeline` | Python 3.13 uv project: the Temporal worker for media and editorial work |
| `packages/contracts` | Zod schemas shared across the seam; JSON Schema and pydantic models are generated from them |
| `packages/db` | Drizzle schema, migrations, and the tenant isolation suite |
| `compose.yaml`, `infra/dev` | Local Postgres + pgvector, Garage, Temporal + UI |
| `deploy/` | Compose files Dokploy deploys on staging |
| `scripts/` | The exact-commit local gate and verified delivery |
| `docs/` | Product, plan, stack, runbooks, the daily build-in-public log |

## Run it

```bash
docker compose up -d --wait
pnpm install
pnpm --filter @temnia/pipeline sync
pnpm --filter @temnia/pipeline worker
```

In a second terminal:

```bash
pnpm dev
```

Open http://localhost:3000 and run the hello workflow. The Temporal UI is at http://localhost:56080.

## Deliver a change

```bash
pnpm ci:local
pnpm pr:verified -- --title "…" --body "…"
```

The gate validates the exact commit and records a receipt; the PR's required check accepts only
that receipt's status. Details in AGENTS.md.
