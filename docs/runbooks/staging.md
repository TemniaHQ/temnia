# Staging runbook

**Status:** v0.1, 2026-09-06, written with S0. Staging is the one deployed environment until M3.
It runs on the existing Hostinger KVM 8 (Mumbai), reinstalled on 2026-09-06 with Ubuntu 26.04 and Dokploy 0.30.5 on Docker 29.8, reached only through a
Cloudflare Tunnel, with Cloudflare Access in front of every hostname. Nothing on the box is
published to the internet: ports 80 and 443 close when the tunnel goes live, and SSH stays the
recovery path. Facts that are only true on Rajesh's machine (SSH alias, key path) live in Claude's
memory, not here.

The additive PR #24 chapter/checkpointed-speech rollout and recovery procedure is in
[Chapter harness rollout and recovery](chapter-harness.md). Its deployment is independent of the
historical protocol-3-to-4 procedure below; consult the implementation-status record before claiming
that either opt-in feature is enabled on shared staging.

## 1. Topology

| Target | Dokploy service type | Source | Reached as |
|---|---|---|---|
| `web` | Application, Dockerfile `apps/web/Dockerfile`, context `.` | `TemniaHQ/temnia` `main`, watch paths `apps/web/**`, `packages/**`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `package.json`, `turbo.json` | `staging.temnia.dev` |
| `pipeline` | Application, Dockerfile `apps/pipeline/Dockerfile`, context `apps/pipeline` | same repo, watch paths `apps/pipeline/**` | no hostname; Temporal worker only |
| `temporal` | Compose, `deploy/temporal/compose.yaml` | same repo, watch path `deploy/temporal/**`, `infra/temporal/**` | `temporal.temnia.dev` (UI); `temporal-staging:7233` inside `dokploy-network` |
| `postgres` | Database, Postgres 18 with pgvector (image `pgvector/pgvector:0.8.6-pg18-trixie`) | Dokploy-managed, volume on the VPS | `temnia-staging-postgres-<suffix>:5432` inside `dokploy-network` (Dokploy appends a random suffix to every service name; today it is `temnia-staging-postgres-tucueg`; look it up with `docker service ls` on the box) |
| `cloudflared` | Application, image `cloudflare/cloudflared:2026.8.3` | Dokploy-managed | outbound only |

Object storage is Cloudflare R2 (bucket `temnia-staging-media`); Garage is local development only.
R2 one-time setup (Rajesh, dashboard or `wrangler`): create the bucket; an API token with object read
and write on it; the CORS rule below (Uppy PUTs upload parts straight to R2 and GETs the part list when it
resumes; Complete and Abort go to the app, so no POST or DELETE is needed; without
`ExposeHeaders: ETag` every multipart completes with no part tags); and confirm the bucket's
"Default Multipart Abort Rule" (7 days) is enabled, which is the backstop behind the reaper.

```json
[{"AllowedOrigins": ["https://staging.temnia.dev"], "AllowedMethods": ["PUT", "GET", "HEAD"],
  "AllowedHeaders": ["content-type"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]
```

Runtime env per target (set in Dokploy, never in the image). The database hostname carries Dokploy's
suffix: the first S1 deploy failed with `getaddrinfo ENOTFOUND temnia-staging-postgres` because the
runbook had assumed the bare app name.

- `web`: `TEMPORAL_ADDRESS=temporal-staging:7233`, `TEMPORAL_NAMESPACE=default`,
  `DATABASE_URL=postgres://temnia_app:<pw>@temnia-staging-postgres-<suffix>:5432/temnia`,
  `MIGRATE_DATABASE_URL=postgres://temnia:<pw>@temnia-staging-postgres-<suffix>:5432/temnia` (required: the
  container refuses to boot without it and applies migrations before serving), `STORAGE_ENDPOINT`
  (the R2 S3 endpoint `https://<account>.r2.cloudflarestorage.com`), `STORAGE_PUBLIC_ENDPOINT` (same
  for R2), `STORAGE_REGION=auto`, `STORAGE_BUCKET=temnia-staging-media`, `STORAGE_ACCESS_KEY_ID`,
  `STORAGE_SECRET_ACCESS_KEY` (an R2 API token scoped to the bucket, object read and write).
- `pipeline`: `TEMPORAL_ADDRESS=temporal-staging:7233`, `TEMPORAL_NAMESPACE=default`,
  `TEMPORAL_TASK_QUEUE=temnia-pipeline`,
  `PIPELINE_DATABASE_URL=postgres://temnia_pipeline:<pw>@temnia-staging-postgres-<suffix>:5432/temnia`, the same
  `STORAGE_*` (without `STORAGE_PUBLIC_ENDPOINT`), and a volume on `/var/lib/temnia/work` sized for
  the largest master plus its ladder (the worker refuses a download without 1.5x the master free).
  From S2 it also carries `TRANSCODE_BACKEND=modal`, `TRANSCRIPTION_PROVIDER=modal`,
  `MODAL_ENVIRONMENT=staging`, `MODAL_TOKEN_ID`, and `MODAL_TOKEN_SECRET` (§2c). With either of
  those two set to `modal` the worker calls the deployed `version` function before it serves the
  queue and exits non-zero on a bad token or an incompatible media protocol, so Dokploy reports
  a failed deploy and keeps the previous container. This checks compatibility, not an exact commit
  or every function's health; the deployed speech smoke below checks the selected build. A worker
  already running does not repeat the boot probe, so incompatible protocol changes require the
  drain-and-replace sequence in §2c.
  The work volume no longer holds ladders once the backend is `modal`: only the master and the audio
  extract stay on it, and transcription reads the extract from R2 inside the Modal function rather
  than from the volume.
  `TRANSCRIPTION_PROVIDER` has no unconfigured state. Leaving it unset means `recorded`, which
  replays a committed response and never calls a GPU; that is right on a laptop and wrong on
  staging, so set it explicitly there.
- `temporal`: `TEMPORAL_DB_PASSWORD`, `TEMPORAL_HOST=temporal-staging` (the alias the others dial; a
  production stack gets its own).
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
   name carries none), with cloud-init told to preserve it: otherwise every reboot reapplies whatever the
   Hostinger panel calls the VPS, which is how the box came back as `temnia` after the first reboot; full package upgrade; unattended security upgrades on; `ufw` removed
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
   `TUNNEL_TOKEN=<token>`, Run Command `cloudflared tunnel --no-autoupdate run` (the field replaces the image entrypoint, so the binary name is required). Deploy; the log must show four
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

## 2c. Modal (Rajesh once, then per deploy)

The HLS ladder runs on a Modal L4 from S2 (AGENTS.md decision 9, corrected in `docs/plans/s2-360-view.md`
§11). Modal functions are scope-blind compute: they are handed a storage prefix the worker has already
decided belongs to an organization, and never an organization id.

1. **Environment.** Modal dashboard → Environments → create `staging`. Deployments and lookups are
   scoped to it, so a later `production` cannot be reached by a staging token.
2. **Token.** Settings → API tokens & service users → API Tokens → New Token, named
   `temnia-pipeline-staging` (created 2026-09-07). Its `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` go
   into the pipeline service's env in Dokploy, nowhere else. Service users, which are scoped to one
   environment, need Modal's Team plan; the workspace is on Starter, and a workspace API token reaches
   every environment, which is fine while `staging` is the only one that matters. For a laptop,
   `uv run modal token new` from `apps/pipeline` stores your own member credentials instead.
3. **Secret `temnia-r2`.** Modal dashboard → Secrets → Custom, name `temnia-r2`, in the `staging`
   environment, with the five keys the function reads: `STORAGE_ENDPOINT`, `STORAGE_REGION`,
   `STORAGE_BUCKET`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`. Use a **second** R2 API
   token scoped to `temnia-staging-media` with object read and write, so it can be revoked without
   touching the worker's.
3b. **Secret `temnia-hf`**, and the model gate. whisperx 3.8.6 diarizes with pyannote-audio 4's
   default pipeline, `pyannote/speaker-diarization-community-1`, which is gated on Hugging Face
   (CC-BY-4.0, commercial use allowed with attribution).
   1. Create a Hugging Face account, open
      [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1),
      and accept its terms with that account. Without this the download 401s inside the function.
   2. Settings → Access Tokens → a **read** token.
   3. Modal dashboard → Secrets → Custom, name `temnia-hf`, in the `staging` environment, one key
      `HF_TOKEN`. The token exists only inside Modal; the worker never holds it, and neither does
      the image.

   The function refuses to start when `HF_TOKEN` is missing or empty, by name and before the audio
   is downloaded, so a forgotten secret is a fast failure rather than one minutes into a billing GPU.
3c. **Volume `temnia-models`.** Created automatically on the first deploy
   (`modal.Volume.from_name(..., create_if_missing=True)`) and mounted at `/models`, which is
   `HF_HOME` and `TORCH_HOME` inside the container. It holds large-v3 (about 3 GB), the alignment
   model for each language seen, and the diarization pipeline. The first transcription fills it and
   every later cold start reads from it; paying for that download on every call would be most of a
   short episode's cost. To force a re-download, delete the Volume in the dashboard.
4. **Probe the GPU first**, from `apps/pipeline`, before trusting anything else:

   ```bash
   uv run modal run --env staging -m temnia_pipeline.modal_app::probe
   ```

   It builds the image, lists the `nvenc` encoders, and times a ten-second 1080p encode. The image is
   `nvidia/cuda:12.4.1-runtime-ubuntu22.04` plus BtbN's `ffmpeg-n8.1-latest-linux64-gpl-8.1` tarball,
   pinned by sha256 and verified with `sha256sum -c` during the build: the worker's own ffmpeg is a
   static musl build with no NVENC, and BtbN rebuilds the `latest` tag in place, so a moved build
   fails the image rather than encoding with something else. A checksum failure here means the build
   moved: download it, recompute, and change `FFMPEG_SHA256` and the URL together.
4b. **Decode is on the GPU too, for the sources that allow it.** The first staging ladder ran on
   2026-09-07 with decode and scale on the CPU and only the rungs on `h264_nvenc`, and took about 22
   minutes for a 2:31 1080p25 H.264 master: the cores were pinned at the function's request, the L4
   sat near twenty percent, and the encoder was starved by the decoder in front of it. The function
   now reads the codec and pixel format the worker probed and, for h264, hevc, or av1 at 8-bit 4:2:0,
   decodes with `-hwaccel cuda -hwaccel_output_format cuda` and scales every rung with `scale_cuda`,
   so a frame never leaves the card until the I-frame rendition pulls its half a frame a second back
   for libx264. Everything else, ProRes and 10-bit and 4:2:2 among them, keeps the CPU graph, and so
   does a run whose CUDA attempt failed: that one is encoded again on the CPU and costs the GPU
   minutes already spent. Which decoder produced a ladder is recorded in `hls/manifest.json` and in
   the hls artifact's metadata as `decoder`, so a slow run can be read rather than guessed at.
   `"decoder": "cpu"` on an H.264 master means the fallback fired, and the container's log says why.
5. **Before deploying protocol 4, drain protocol 3.** Pause new uploads/ingest, let the old workers
   finish their activities, and verify that no running or retrying workflow retains a version-3
   GPU call handle. Resolve uncertain calls while the old deployment still exists. Stop the old
   workers only after this drain. Deploy Modal from the exact checkout intended for the new pipeline,
   run the deployed smoke below, then deploy/start the matching pipeline image and resume ingest.
   Version 4 uses result envelopes; version-3 workers cannot parse them, and version 4 refuses
   unframed old results. Deploying Modal first while old workers keep polling is unsafe.
   If work cannot drain, defer this rollout. A separate Modal environment alone does not isolate
   Temporal: workers on the same namespace/task queue could still pick up incompatible retries.
   Concurrent versions require explicit Temporal version/queue routing and source ownership, which
   this release does not add. Do not abandon an unknown paid call merely to complete the rollout.

5a. **Deploy**, using the same checkout as the matching pipeline image:

   ```bash
   uv run modal deploy --env staging -m temnia_pipeline.modal_app
   ```

   One deploy publishes `ladder`, `transcribe`, `version`, `deployment_identity`, and `smoke_transcribe`. The first build is long: it adds
   torch 2.8 from PyTorch's cu126 index (the oldest index that carries torch 2.8; the wheels bundle their own CUDA libraries, so the image's 12.4 runtime only has to provide the driver, and no second copy
   of the CUDA libraries comes along) and whisperx 3.8.6 on top of the NVENC ffmpeg. There is no
   deploying one function without the other: they share `CONTRACT_VERSION`, and the worker's boot
   probe refuses a version it does not speak.

5b. **Smoke the speech path**, after every deploy of the Modal app and before a real source is
   trusted to it:

   ```bash
   uv run --frozen python -m temnia_pipeline.modal_smoke --app temnia-media --environment staging
   ```

   Run this from the deployment checkout. The CLI resolves existing deployed functions by app
   and environment; it does not create a `modal run` ephemeral app. It checks the deployed protocol
   and source/config fingerprint before and after execution, and verifies the actual GPU outcome
   carries the expected build identity. A stale, mixed, or changed deployment fails the smoke.
   The fingerprint covers package Python source, dependency lock/config, and the pipeline Dockerfile;
   it identifies those inputs rather than attesting mutable upstream package/model bytes.
   It uploads a nine-second real-speech sample under a throwaway `smoke/` prefix, runs the deployed
   `transcribe` through its real image, secrets, gated diarization model, and GPU,
   normalises the result with the worker's own normaliser, checks that "chapters" and "smoke" were
   heard, removes what it wrote, and prints the words, the language, the speakers, the GPU seconds
   and the wall seconds. It exits non-zero on a miss. This is the step the boot probe cannot be:
   the probe checks a version constant on a CPU, and on 2026-09-07 a green probe sat beside an
   image that could not build and a diarization call that could not run (S2 review, I29). Paste
   the numbers into the day's log; they are the first measured GPU seconds per audio second.

6. **If the worker will not start**, its log carries one line naming the variables that put it on
   Modal, for example `TRANSCODE_BACKEND=modal and TRANSCRIPTION_PROVIDER=modal:`.
   `cannot reach the Modal app …` is a token or a missing deployment; `… speaks media contract 'x'
   and this worker speaks 'y'` means the media protocols differ. Follow the paired rollout above;
   do not replace a live incompatible app underneath its old workers.

## 2d. Record the gate's transcription fixture (once, after the first staging run)

`apps/pipeline/tests/fixtures/transcripts/speech-40s.whisperx.json` is **hand-authored**. It is in
WhisperX's output shape and its word timings follow the measured turn boundaries of
`apps/web/e2e/fixtures/speech-40s.mp4`, but no engine produced it; it exists so the normaliser, the
cue builder, and the gate had something with real speech in them before a Modal account did. Its
`_note` says so. Replace it with a real recording the first time the deployed function runs, so the
gate replays what WhisperX actually emits rather than what we guessed it emits.

1. Upload `apps/web/e2e/fixtures/speech-40s.mp4` to staging and let the ingest finish:

   ```bash
   node scripts/upload-master.mjs apps/web/e2e/fixtures/speech-40s.mp4 --project <id>
   ```

2. Transcription starts on its own after the ingest finalizes. When the transcript row is `ready`,
   fetch the engine's own response, which the function wrote beside the revision:

   ```bash
   # org/<organization>/source/<source>/transcript/raw-1.json
   aws s3 cp "s3://temnia-staging-media/org/<org>/source/<source>/transcript/raw-1.json" \
     apps/pipeline/tests/fixtures/transcripts/speech-40s.whisperx.json \
     --endpoint-url "$STORAGE_ENDPOINT"
   ```

3. Add the three fields the recorded provider and the reader need, keeping everything else byte for
   byte as the engine wrote it (including any bare `NaN` alignment score, which is real and which
   `normalize.py` turns into a null confidence):

   - `"_note"`: that this is a real recording, from which commit and which date.
   - `"_durationMs"`: `40116`, the probed duration of `speech-40s.mp4`. **This is the field the gate
     matches on**, because it does not move when the pipeline image's ffmpeg is bumped.
   - `"_audioSha256"`: the sha256 of the audio extract it was made from. The worker logs it, and the
     provider's failure message repeats it. It is tried before the duration, so a recording made
     against one specific stored object still wins; the gate does not depend on it, because the
     extract is re-encoded by whichever ffmpeg the image carries and the checksum moves with it.

4. Re-run the gate. `pnpm ci:local` sets `TRANSCRIPTION_RECORDINGS_DIR` at the fixtures directory and
   pins no single file: the transcript e2e drives `speech-40s.mp4` to Ready, `master-24s.mp4` to
   Retrying and `master-12s.mp4` to Failed in the same run, and each is matched by the duration its
   recording declares.

5. Update the word count in `apps/pipeline/tests/test_transcription_normalize.py` and regenerate
   `apps/web/tests/fixtures/speech-40s.transcript.json`, which is that response put through the
   normaliser and is what the caption tests read.

## 3. Deploy targets (Rajesh once, then automatic)

The four services live in the `temnia` project's `staging` environment. They were created by
[`infra/dokploy/create-staging.py`](../../infra/dokploy/create-staging.py), run on the box against the
local API with the key in `/root/.dokploy-api-key` (`ssh temnia-vps python3 - < infra/dokploy/create-staging.py`);
it is idempotent by name. What it creates, and what to enter if doing it by hand:

- **postgres**: Dokploy Database → PostgreSQL, image `pgvector/pgvector:0.8.6-pg18-trixie`,
  database `temnia`, user `temnia`, generated password, name `temnia-staging-postgres`, env
  `PGDATA=/var/lib/postgresql/data/pgdata` (Dokploy mounts the volume at the pre-18 path and the
  Postgres 18 image refuses it otherwise). Never read the record back through the API in a way that
  prints it: the response carries the password. After the
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
3a. Deploy order matters when a migration ships with pipeline code that writes the new columns
   (the S2 hardening's `transcript.run_id` and `usage_ledger.idempotency_key`, migration 0002):
   the web's release phase applies migrations, so the web deploys first. A pipeline container that
   starts before it will fail its claims retryably until the release line below has printed; a
   worker that keeps failing them after that is on the wrong commit.
3b. From S1: the web container's log opens with `release: migrations applied, seed rows present`;
   `/projects` lists projects; a master uploaded on a project page reaches `Ready` and plays on its
   source page with the waveform painted. The sprint's scale run is
   `node scripts/upload-master.mjs <2h master> --project <id> --base https://staging.temnia.dev --header "CF-Access-Client-Id: <id>" --header "CF-Access-Client-Secret: <secret>"`
   (a Cloudflare Access service token behind a Service Auth policy on the `staging.temnia.dev`
   application; the browser session cookie is not usable from a script).
4. If a target "deployed" but behaves as before, read the dead container's log before anything
   else:

```bash
docker logs $(docker ps -a --filter name=<service> --format '{{.ID}} {{.Status}}' | grep -i exited | head -1 | cut -d' ' -f1)
```

## 4b. Apply runtime configuration without a code change

Verified on the installed Dokploy v0.30.5 on 2026-09-09. Saving environment variables alone does
not update a running container. For code changes, use the normal Git deployment. For configuration
changes against an already verified image, Dokploy's `application.reload` applies the saved
environment and mounts and replaces the task without rebuilding the image. The pipeline and web
both passed this path during harness enablement. The earlier image-changing-only guidance was
incorrect.

Before changing either service, check active work and retain its previous Dokploy configuration
privately. Keep the gateway key only on the pipeline. For harness enablement, apply and verify the
worker first, then apply the matching nonsecret web settings. Do not reload a worker underneath an
active qualification run.

The authenticated API uses these routes and request shapes:

- `GET application.one?applicationId=...` reads the current configuration. Its result contains
  secrets: do not print it or copy it into a report.
- `POST application.saveEnvironment` requires `applicationId`, `env`, `buildArgs`, `buildSecrets`
  and `createEnvFile`. Preserve the latter three values and every unrelated environment entry.
- `POST application.reload` requires `applicationId` and `appName`. It applies configuration
  only; it does not pull new code.
- `POST application.deploy` accepts `applicationId` and optional `title` and `description`. It
  fetches the configured Git source and builds it. `redeploy` instead rebuilds the existing server
  checkout. A build keeps the configuration captured at its start, even if Environment is
  saved again while the build is running.

API success can have an empty response body. A JSON parse error after successful submission does
not mean the deployment failed. Inspect the existing deployment/task before submitting again.
For a Git deployment, wait for the exact deployment record. For a reload, verify a new running
task, the expected environment in both its service specification and container, the image
identity, and successful boot. Check web migrations/seed and `/api/health`, and the worker's
Temporal connection. A reload is expected to retain the image. Read a failed task's logs before
diagnosing rollback; the prior task may be running with its prior environment.

The topic harness no longer needs a mounted route file or `HARNESS_*` environment: both
images carry `apps/pipeline/harness/staging.json` and its snapshot
([topic-generation-staging.md](topic-generation-staging.md)). What follows describes the
earlier mount-based enablement and stays for the record.

For the harness route file, the public API namespace is **`mounts`**, plural:
`mounts.listByServiceId`, `mounts.create`, and `mounts.remove`. A bind creation uses `serviceId`,
`serviceType: "application"`, `type: "bind"`, `hostPath` and `mountPath`. Preserve existing mounts.
This version has no `readOnly` field; do not append `:ro` to a target path. The staging snapshot is
a nonsecret, content-addressed, root-owned `0444` file inside a root-owned directory, bound at
`/etc/temnia/harness-routes.json`. Verify that the nonroot worker can load it and that opening it
for writing raises `PermissionError`. Docker still reports a writable bind; the protection is the
file's ownership and permissions, not protection against container root. Retain the hash-addressed
file and create a new one for every route change.

## 4c. Topic generation

The committed harness configuration, the route snapshot and the first-run checklist are in
[topic-generation-staging.md](topic-generation-staging.md): merge, deploy, click.

## 5. Rules that follow from this setup

- The Hostinger panel's Reboot is a hard reset: no shutdown sequence in the guest, about a minute of
  502s from Cloudflare while containers restart, then everything returns on its own (restart policies,
  persisted firewall). Prefer `ssh temnia-vps reboot` when a reboot is needed, and expect the minute.
- Hostinger manages the VM through the QEMU guest agent: it truncates logs and drops a telemetry
  script on the box from time to time. Not ours, not a sign of compromise.

- No hostname of the staging box appears in a post or a screenshot ([build-in-public.md](../build-in-public.md) §8).
- Port 22 is rate-limited (six new connections per thirty seconds). Poll over HTTPS, never in an
  SSH loop.
- The Dokploy API answers scripts with a 302 to the Access login, which a naive script reads as
  success. Use a Cloudflare service token or do the one-off by hand.
- `production` does not exist yet. It is promoted from `main` by fast-forward at M3, on Neon and
  its own Dokploy environment.
