# Staging runbook

**Status:** v0.1, 2026-09-06, written with S0. Staging is the one deployed environment until M3.
It runs on the existing Hostinger KVM 8 (Mumbai), reinstalled on 2026-09-06 with Ubuntu 26.04 and Dokploy 0.30.5 on Docker 29.8, reached only through a
Cloudflare Tunnel, with Cloudflare Access in front of every hostname. Nothing on the box is
published to the internet: ports 80 and 443 close when the tunnel goes live, and SSH stays the
recovery path. Facts that are only true on Rajesh's machine (SSH alias, key path) live in Claude's
memory, not here.

## 1. Topology

| Target | Dokploy service type | Source | Reached as |
|---|---|---|---|
| `web` | Application, Dockerfile `apps/web/Dockerfile`, context `.` | `TemniaHQ/temnia` `main`, watch paths `apps/web/**`, `packages/**`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `package.json`, `turbo.json` | `staging.temnia.dev` |
| `pipeline` | Application, Dockerfile `apps/pipeline/Dockerfile`, context `apps/pipeline` | same repo, watch paths `apps/pipeline/**` | no hostname; Temporal worker only |
| `temporal` | Compose, `deploy/temporal/compose.yaml` | same repo, watch path `deploy/temporal/**`, `infra/temporal/**` | `temporal.temnia.dev` (UI); `temporal:7233` inside `dokploy-network` |
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

## 2. The box

The VPS was reinstalled from the Hostinger panel on 2026-09-06 with plain Ubuntu 26.04 LTS, then
built by [`infra/vps/build.sh`](../../infra/vps/build.sh), copied to root and started as a detached
systemd unit (the commands are in the script's header; running it through the SSH session fails at
step 3 because the sshd restart ends the session). Nothing on the box was inherited from the legacy
install. What the
script does, in order:

1. hostname `temnia-vps` (the machine carries every environment of the `temnia` Dokploy project, so its
   name carries none); full package upgrade; unattended security upgrades on; `ufw` removed
   (it cannot see Docker-published ports) in favour of raw iptables saved by `netfilter-persistent`.
2. SSH: key-only root (`/etc/ssh/sshd_config.d/10-temnia.conf`, sorted before cloud-init's drop-in so
   it wins), password and keyboard-interactive off, three tries.
3. Docker log rotation defaults (`/etc/docker/daemon.json`) written before Dokploy's installer
   installs Docker.
4. Docker from Docker's own repository (the 26.04 channel), then Dokploy's official installer: a
   single-node swarm, `dokploy-network`, Postgres, Dokploy, Traefik. The installer's own Docker pin
   (28.5.0 via get.docker.com) does not exist in the 26.04 channel, which is why Docker comes first.
5. Firewall. `INPUT` (v4 and v6): loopback, established, ICMP, SSH rate-limited to six new
   connections per thirty seconds per source, everything else new dropped. `DOCKER-USER`: every new
   connection arriving on the public interface to a Docker-published port is dropped, so Traefik's 80
   and 443 and the panel's 3000 are unreachable from the internet even though Docker publishes them.
   Traffic through the Cloudflare tunnel arrives from the `cloudflared` container over the overlay
   network and never touches that rule.

Consequences: the Dokploy panel is reachable in exactly two ways, the tunnel hostname behind Access,
and an SSH port forward (`ssh -N -L 3300:127.0.0.1:3000 temnia-vps`, then `http://localhost:3300`; the local side is 3300 so it never looks like `next dev`).
The forward is the recovery path if Cloudflare is ever misconfigured; SSH itself is the recovery path
for everything else, and the box has a root password set in the Hostinger panel for its web console.

Rebuilding from scratch is: reinstall in the panel, add the SSH key to root, run the script, do the
first-run form through the port forward, redo §2b and §3. About twenty minutes plus image pulls.

## 2b. One-time setup (Rajesh, dashboards)

These steps need the Cloudflare and Dokploy dashboards and the GitHub org owner. Each is done once.

1. **Zone.** `temnia.dev` is the infra domain (registered 2026-09-06; `temnia.com` stays the product
   domain and stays on Spaceship until the product needs it). Add it to the Cloudflare account, delete
   the two parking A records the import scanned, move the nameservers at Spaceship, wait for the zone
   to become active. SSL/TLS mode **Full (strict)**, Always Use HTTPS on. `.dev` is HSTS-preloaded in
   every browser, so nothing on it can be served over plain HTTP; Cloudflare terminates TLS, so that
   costs nothing here.
2. **Tunnel.** Zero Trust → Networks → Connectors → Create a tunnel → Cloudflared. Name it
   `temnia-vps`. Copy the token. Public hostnames, all of type HTTP with service
   `http://dokploy-traefik:80`: `dokploy.temnia.dev`, `staging.temnia.dev`, `temporal.temnia.dev`.
3. **Access.** Zero Trust → Access → Applications → self-hosted, one application per hostname
   above. Policy `Rajesh only`: Allow, include the login email. Identity provider: the built-in
   Cloudflare login (account MFA). The Dokploy application keeps two extra **Bypass → Everyone**
   policies scoped to paths `/api/deploy*` and `/api/webhook*`, because GitHub's webhook cannot
   authenticate. Never widen those paths; give scripts a service token and a Service Auth policy.
4. **First run.** Through the SSH port forward, create the Dokploy admin account.
5. **cloudflared on the box.** Dokploy → new project `temnia` → environment `staging` →
   Application `cloudflared`: provider Docker, image `cloudflare/cloudflared:2026.8.3`, env
   `TUNNEL_TOKEN=<token>`, command `tunnel --no-autoupdate run`. Deploy; the log must show four
   registered connections.
6. **Panel domain.** Dokploy → Web Server → Server Domain: `dokploy.temnia.dev`, HTTPS off,
   certificate none. Open it through the tunnel and confirm Access prompts.
7. **Dokploy GitHub App.** Dokploy → Settings → Git → GitHub: install Dokploy's GitHub App on the
   `TemniaHQ` organization with access to `temnia`. (The legacy install lived on the archived
   organization and did not carry over.)
8. **Old zone.** Delete the `dokploy` and `staging` records and the Access applications on
   `mitosia.cloud`; revoke the DNS API token the old Traefik used. The domain lapses next year.
9. **Hostinger firewall (optional second layer).** One rule set allowing only TCP 22 inbound. The
   box's own iptables already enforce this; the panel firewall just makes it true even if a future
   change to those rules gets it wrong.

## 3. Deploy targets (Rajesh once, then automatic)

Create the four Temnia services in the `temnia` project's `staging` environment:

- **postgres**: Dokploy Database → PostgreSQL, image `pgvector/pgvector:0.8.6-pg18-trixie`,
  database `temnia`, user `temnia`, generated password, name `temnia-staging-postgres`. After the
  first start, create the two application roles the same way `infra/dev/postgres-init/01-roles.sql`
  does, with generated passwords (`temnia_app`, `temnia_pipeline`; the `temporal` role is not
  needed here because the Temporal stack has its own Postgres).
- **temporal**: Compose, repository `TemniaHQ/temnia`, branch `main`, compose path
  `deploy/temporal/compose.yaml`, env `TEMPORAL_DB_PASSWORD=<generated>`. Domain for service
  `temporal-ui`, host `temporal.temnia.dev`, container port 8080, HTTPS off, certificate
  none.
- **web**: Application, repository `TemniaHQ/temnia`, branch `main`, build type Dockerfile,
  Dockerfile path `apps/web/Dockerfile`, build context `.`, watch paths as in §1, env as in §1.
  Domain `staging.temnia.dev`, container port 3000, HTTPS off, certificate none.
- **pipeline**: Application, same repository and branch, Dockerfile path
  `apps/pipeline/Dockerfile`, build context `apps/pipeline`, watch paths `apps/pipeline/**`, env as
  in §1. No domain.

Enable auto-deploy on `web`, `pipeline`, and `temporal`. With watch paths set, a merge to `main`
rebuilds only the targets whose files changed.

## 4. Verify a deploy

1. GitHub → the merge commit → Dokploy's deployment shows `done` for each affected target.
2. `https://staging.temnia.dev/api/health` returns `{"ok":true,"service":"temnia-web"}` after the
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
