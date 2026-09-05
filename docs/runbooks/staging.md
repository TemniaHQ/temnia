# Staging runbook

**Status:** v0.1, 2026-09-06, written with S0. Staging is the one deployed environment until M3.
It runs on the existing Hostinger KVM 8 (Mumbai) under Dokploy 0.29, reached only through a
Cloudflare Tunnel, with Cloudflare Access in front of every hostname. Nothing on the box is
published to the internet: ports 80 and 443 close when the tunnel goes live, and SSH stays the
recovery path. Facts that are only true on Rajesh's machine (SSH alias, key path) live in Claude's
memory, not here.

## 1. Topology

| Target | Dokploy service type | Source | Reached as |
|---|---|---|---|
| `web` | Application, Dockerfile `apps/web/Dockerfile`, context `.` | `TemniaHQ/temnia` `main`, watch paths `apps/web/**`, `packages/**`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `package.json`, `turbo.json` | `staging.temnia.com` |
| `pipeline` | Application, Dockerfile `apps/pipeline/Dockerfile`, context `apps/pipeline` | same repo, watch paths `apps/pipeline/**` | no hostname; Temporal worker only |
| `temporal` | Compose, `deploy/temporal/compose.yaml` | same repo, watch path `deploy/temporal/**`, `infra/temporal/**` | `temporal.staging.temnia.com` (UI); `temporal:7233` inside `dokploy-network` |
| `postgres` | Database, Postgres 18 with pgvector (image `pgvector/pgvector:0.8.6-pg18-trixie`) | Dokploy-managed, volume on the VPS | `temnia-staging-postgres:5432` inside `dokploy-network` |
| `cloudflared` | Application, image `cloudflare/cloudflared:2026.8.3` | Dokploy-managed | outbound only |

Object storage is Cloudflare R2 (bucket `temnia-staging-media`); Garage is local development only.

Runtime env per target (set in Dokploy, never in the image):

- `web`: `TEMPORAL_ADDRESS=temporal:7233`, `TEMPORAL_NAMESPACE=default`. From S1: `DATABASE_URL` (app role), `MIGRATE_DATABASE_URL` (owner), `STORAGE_*`.
- `pipeline`: `TEMPORAL_ADDRESS=temporal:7233`, `TEMPORAL_NAMESPACE=default`, `TEMPORAL_TASK_QUEUE=temnia-pipeline`. From S1: `PIPELINE_DATABASE_URL` (pipeline role), `STORAGE_*`.
- `temporal`: `TEMPORAL_DB_PASSWORD`.
- `cloudflared`: `TUNNEL_TOKEN`.

An env change reaches a container only through a deploy that changes the image, and a container
that then exits non-zero is rolled back with its old env, invisibly. Verify against the running
service, never the API response:

```bash
docker service inspect <service> --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}'
```

## 2. One-time setup (Rajesh, dashboards)

These steps need the Cloudflare and Dokploy dashboards and the GitHub org owner. Each is done once.

1. **Zone.** Add `temnia.com` to the Cloudflare account and move its nameservers at Spaceship to
   the two Cloudflare assigns. Wait for the zone to become active. SSL/TLS mode **Full (strict)**.
2. **Tunnel.** Zero Trust → Networks → Connectors → Create a tunnel → Cloudflared. Name it
   `temnia-staging`. Copy the token. Public hostnames, all of type HTTP with service
   `http://dokploy-traefik:80`: `staging.temnia.com`, `temporal.staging.temnia.com`,
   `dokploy.temnia.com`.
3. **Access.** Zero Trust → Access → Applications → self-hosted, one application per hostname
   above. Policy `Rajesh only`: Allow, include the login email. Identity provider: the built-in
   Cloudflare login (account MFA). The Dokploy application keeps two extra **Bypass → Everyone**
   policies scoped to paths `/api/deploy*` and `/api/webhook*`, because GitHub's webhook cannot
   authenticate. Never widen those paths; give scripts a service token and a Service Auth policy.
4. **Dokploy GitHub App.** Dokploy → Settings → Git → GitHub: install Dokploy's GitHub App on the
   `TemniaHQ` organization with access to `temnia`. (The legacy install lives on the archived
   organization and does not carry over.)
5. **cloudflared on the box.** Dokploy → new project `temnia` → environment `staging` →
   Application `cloudflared`: provider Docker, image `cloudflare/cloudflared:2026.8.3`, env
   `TUNNEL_TOKEN=<token>`, command `tunnel --no-autoupdate run`. Deploy; the log must show four
   registered connections.
6. **Cut over Dokploy itself.** Dokploy → Web Server → Server Domain: `dokploy.temnia.com`, HTTPS
   off, certificate none. Open it through the tunnel and confirm Access prompts, then delete the
   old panel hostname from the legacy zone.
7. **Close the origin.** On the VPS, remove the Cloudflare origin-lock rules in `DOCKER-USER`
   (they allowed 80/443 from Cloudflare ranges) and replace them with a single
   `-A DOCKER-USER -i eth0 -p tcp -m multiport --dports 80,443 -j DROP`, then
   `netfilter-persistent save`. Traefik keeps listening; nothing can reach it except the tunnel.
   Leave `INPUT` alone: SSH rate limiting lives there and SSH is the recovery path.
8. **Stop issuing certificates.** Remove the `letsencrypt` and `letsencrypt-dns` resolvers and the
   wildcard default cert from Traefik's config (`/etc/dokploy/traefik/traefik.yml`,
   `dynamic/wildcard-default.yml`); Cloudflare terminates TLS. Restart Traefik. Delete the
   `CF_DNS_API_TOKEN` from Traefik's env and revoke it in Cloudflare.
9. **Retire the legacy staging.** `mitosia-staging-uxa95i` and `mitosia-stagingdb-mo4ted` are
   the archived repository's services. Stop both in Dokploy; keep the database volume until the
   `/root/backups` dumps have been copied off the box, then remove the services.

## 3. Deploy targets (Rajesh once, then automatic)

Create the four Temnia services in the `temnia` project's `staging` environment:

- **postgres**: Dokploy Database → PostgreSQL, image `pgvector/pgvector:0.8.6-pg18-trixie`,
  database `temnia`, user `temnia`, generated password, name `temnia-staging-postgres`. After the
  first start, create the two application roles the same way `infra/dev/postgres-init/01-roles.sql`
  does, with generated passwords (`temnia_app`, `temnia_pipeline`; the `temporal` role is not
  needed here because the Temporal stack has its own Postgres).
- **temporal**: Compose, repository `TemniaHQ/temnia`, branch `main`, compose path
  `deploy/temporal/compose.yaml`, env `TEMPORAL_DB_PASSWORD=<generated>`. Domain for service
  `temporal-ui`, host `temporal.staging.temnia.com`, container port 8080, HTTPS off, certificate
  none.
- **web**: Application, repository `TemniaHQ/temnia`, branch `main`, build type Dockerfile,
  Dockerfile path `apps/web/Dockerfile`, build context `.`, watch paths as in §1, env as in §1.
  Domain `staging.temnia.com`, container port 3000, HTTPS off, certificate none.
- **pipeline**: Application, same repository and branch, Dockerfile path
  `apps/pipeline/Dockerfile`, build context `apps/pipeline`, watch paths `apps/pipeline/**`, env as
  in §1. No domain.

Enable auto-deploy on `web`, `pipeline`, and `temporal`. With watch paths set, a merge to `main`
rebuilds only the targets whose files changed.

## 4. Verify a deploy

1. GitHub → the merge commit → Dokploy's deployment shows `done` for each affected target.
2. `https://staging.temnia.com/api/health` returns `{"ok":true,"service":"temnia-web"}` after the
   Access login.
3. On the page, run the hello workflow: the result names the seeded organization id and a Python
   worker host. In the Temporal UI the workflow shows one completed activity on task queue
   `temnia-pipeline`.
4. If a target "deployed" but behaves as before, read the dead container's log before anything
   else:

```bash
docker logs $(docker ps -a --filter name=<service> --format '{{.ID}} {{.Status}}' | grep -i exited | head -1 | cut -d' ' -f1)
```

## 5. Rules that follow from this setup

- No hostname of the staging box appears in a post or a screenshot ([build-in-public.md](../build-in-public.md) §8).
- Port 22 is rate-limited (six new connections per thirty seconds). Poll over HTTPS, never in an
  SSH loop.
- The Dokploy API answers scripts with a 302 to the Access login, which a naive script reads as
  success. Use a Cloudflare service token or do the one-off by hand.
- `production` does not exist yet. It is promoted from `main` by fast-forward at M3, on Neon and
  its own Dokploy environment.
