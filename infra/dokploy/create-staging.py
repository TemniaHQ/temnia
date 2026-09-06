#!/usr/bin/env python3
"""Create the Temnia staging targets in Dokploy through its API.

Runs on the VPS itself (`ssh temnia-vps python3 - < infra/dokploy/create-staging.py`),
reading the API key from /root/.dokploy-api-key so it never leaves the box. Creates,
in project `temnia` / environment `staging`:

  postgres  temnia-staging-postgres   Postgres 18 + pgvector, generated password
  temporal  temnia-staging-temporal   deploy/temporal/compose.yaml from main, UI at temporal.temnia.dev
  web       temnia-staging-web        apps/web/Dockerfile from main, staging.temnia.dev
  pipeline  temnia-staging-pipeline   apps/pipeline/Dockerfile from main, no domain

Only the database is deployed here. The other three auto-deploy from the first push
to main that touches their watch paths (that is the S0 merge). Idempotent: anything that
already exists by name in the environment is left alone and reported.
"""

from __future__ import annotations

import json
import secrets
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:3000/api"
KEY = open("/root/.dokploy-api-key", encoding="utf-8").read().strip()  # noqa: SIM115

OWNER, REPO, BRANCH = "TemniaHQ", "temnia", "main"
PROJECT, ENVIRONMENT = "temnia", "staging"
TEMPORAL_HOST = "temporal-staging"
TEMPORAL_ADDRESS = f"{TEMPORAL_HOST}:7233"
WEB_WATCH = ["apps/web/**", "packages/**", "pnpm-lock.yaml", "pnpm-workspace.yaml", "package.json", "turbo.json"]
PIPELINE_WATCH = ["apps/pipeline/**"]
TEMPORAL_WATCH = ["deploy/temporal/**", "infra/temporal/**"]


def call(path: str, body: dict | None = None, *, params: dict | None = None):
    url = f"{BASE}/{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("x-api-key", KEY)
    req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:  # noqa: S310
            raw = res.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"{path} -> HTTP {e.code}: {e.read().decode()[:400]}")
    return json.loads(raw) if raw else None


def find_project() -> dict | None:
    return next((p for p in call("project.all") if p["name"] == PROJECT), None)


def main() -> None:
    providers = call("github.githubProviders")
    if not providers:
        sys.exit("no GitHub provider is installed in Dokploy")
    github_id = providers[0]["githubId"]
    print(f"github provider: {providers[0].get('gitProvider', {}).get('name', github_id)}")

    project = find_project()
    if project is None:
        call("project.create", {"name": PROJECT, "description": "Temnia: staging now, production at M3"})
        project = find_project()
    project_id = project["projectId"]
    envs = call("environment.byProjectId", params={"projectId": project_id})
    env = next((e for e in envs if e["name"] == ENVIRONMENT), None)
    if env is None:
        call("environment.create", {"name": ENVIRONMENT, "projectId": project_id, "description": "tracks main"})
        envs = call("environment.byProjectId", params={"projectId": project_id})
        env = next(e for e in envs if e["name"] == ENVIRONMENT)
    env_id = env["environmentId"]
    print(f"project {PROJECT} ({project_id}) environment {ENVIRONMENT} ({env_id})")
    existing = {
        "postgres": {d["name"] for d in env.get("postgres", [])},
        "compose": {c["name"] for c in env.get("compose", [])},
        "application": {a["name"] for a in env.get("applications", [])},
    }

    # 1. Product database. The two application roles are created at S1 with the first table.
    if "postgres" in existing["postgres"]:
        print("postgres exists, skipping")
        pg = None
    else:
        pg = call("postgres.create", {
        "name": "postgres",
        "appName": "temnia-staging-postgres",
        "databaseName": "temnia",
        "databaseUser": "temnia",
        "databasePassword": secrets.token_hex(24),
        "dockerImage": "pgvector/pgvector:0.8.6-pg18-trixie",
        "environmentId": env_id,
        "description": "Postgres 18 + pgvector; owner role; app roles added at S1",
        })
        # Dokploy mounts the volume at /var/lib/postgresql/data; the Postgres 18 image
        # refuses that layout unless the data directory sits inside the mount.
        call("postgres.update", {"postgresId": pg["postgresId"], "env": "PGDATA=/var/lib/postgresql/data/pgdata"})
        call("postgres.deploy", {"postgresId": pg["postgresId"]})
        print(f"postgres {pg['appName']} ({pg['postgresId']}) deploying")

    # 2. Temporal stack from the repository's compose file.
    if "temporal" in existing["compose"]:
        print("compose temporal exists, skipping")
    else:
        temporal(env_id, github_id)

    # 3. Web app and 4. pipeline worker: Dockerfile builds from the same repository.
    if "web" in existing["application"]:
        print("application web exists, skipping")
    else:
        web_id = application(env_id, github_id,
            "web", "temnia-staging-web", "apps/web/Dockerfile", ".",
            f"TEMPORAL_ADDRESS={TEMPORAL_ADDRESS}\nTEMPORAL_NAMESPACE=default", WEB_WATCH,
        )
        call("domain.create", {
            "host": "staging.temnia.dev",
            "port": 3000,
            "https": False,
            "certificateType": "none",
            "applicationId": web_id,
        })
        print(f"application web ({web_id}) configured with staging.temnia.dev")
    if "pipeline" in existing["application"]:
        print("application pipeline exists, skipping")
    else:
        pipeline_id = application(env_id, github_id,
            "pipeline", "temnia-staging-pipeline", "apps/pipeline/Dockerfile", "apps/pipeline",
            f"TEMPORAL_ADDRESS={TEMPORAL_ADDRESS}\nTEMPORAL_NAMESPACE=default\nTEMPORAL_TASK_QUEUE=temnia-pipeline",
            PIPELINE_WATCH,
        )
        print(f"application pipeline ({pipeline_id}) configured")
    print("done: merge to main deploys temporal, web, and pipeline")


def temporal(env_id: str, github_id: str) -> None:
    compose = call("compose.create", {
        "name": "temporal",
        "appName": "temnia-staging-temporal",
        "environmentId": env_id,
        "composeType": "docker-compose",
        "sourceType": "github",
        "description": "Temporal server, UI, and its own Postgres",
    })
    compose_id = compose["composeId"]
    call("compose.update", {
        "composeId": compose_id,
        "sourceType": "github",
        "githubId": github_id,
        "owner": OWNER,
        "repository": REPO,
        "branch": BRANCH,
        "composePath": "deploy/temporal/compose.yaml",
        "composeType": "docker-compose",
        "autoDeploy": True,
        "watchPaths": TEMPORAL_WATCH,
        "env": f"TEMPORAL_DB_PASSWORD={secrets.token_hex(24)}\nTEMPORAL_HOST={TEMPORAL_HOST}",
    })
    call("domain.create", {
        "host": "temporal.temnia.dev",
        "port": 8080,
        "https": False,
        "certificateType": "none",
        "composeId": compose_id,
        "serviceName": "temporal-ui",
        "domainType": "compose",
    })
    print(f"compose temporal ({compose_id}) configured; deploys on the first push to main")


def application(env_id: str, github_id: str, name: str, app_name: str, dockerfile: str, context: str, env_text: str, watch: list[str]) -> str:
    app = call("application.create", {"name": name, "appName": app_name, "environmentId": env_id, "sourceType": "github"})
    app_id = app["applicationId"]
    call("application.saveGithubProvider", {
        "applicationId": app_id,
        "githubId": github_id,
        "owner": OWNER,
        "repository": REPO,
        "branch": BRANCH,
        "buildPath": "/",
        "triggerType": "push",
        "watchPaths": watch,
    })
    call("application.saveBuildType", {
        "applicationId": app_id,
        "buildType": "dockerfile",
        "dockerfile": dockerfile,
        "dockerContextPath": context,
        "dockerBuildStage": "",
        "herokuVersion": "",
        "railpackVersion": "",
    })
    call("application.saveEnvironment", {
        "applicationId": app_id,
        "env": env_text,
        "buildArgs": "",
        "buildSecrets": "",
        "createEnvFile": False,
    })
    call("application.update", {"applicationId": app_id, "autoDeploy": True})
    return app_id


if __name__ == "__main__":
    main()
