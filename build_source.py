#!/usr/bin/env python3
"""Builds the live source project in Auromations, which is what gets generated
into the template.

Literal values everywhere rather than cross-service references: a
${{other.RAILWAY_PUBLIC_DOMAIN}} resolves to an empty string in a live project
and only fills in during a template deploy, so a source project wired with
references looks fine and boots broken. The references go back in during the
template repair, where they belong.
"""

import json
import pathlib
import secrets
import string
import sys
import time

sys.path.insert(0, str(pathlib.Path("~/.claude/skills/create-template-for-railway/scripts").expanduser()))
import rw  # noqa: E402

WORKSPACE = "fc4796db-2c6c-4354-a564-d4a1d900af53"  # Auromations
IMAGE = "ghcr.io/hmseeb/comp-ai-crm-railway:1.13.0-r1"
PG_IMAGE = "ghcr.io/railwayapp-templates/postgres-ssl:17"


def rand(n=32):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def q(doc, variables=None, internal=False):
    return rw.gql(doc, variables, internal=internal)


def main():
    allowed_sign_in = sys.argv[1] if len(sys.argv) > 1 else ""
    if not allowed_sign_in:
        sys.exit("usage: build_source.py <allowed-sign-in> [google-client-id] [google-client-secret]")
    google_id = sys.argv[2] if len(sys.argv) > 2 else ""
    google_secret = sys.argv[3] if len(sys.argv) > 3 else ""

    project = q(
        """mutation($input: ProjectCreateInput!) {
             projectCreate(input: $input) {
               id
               environments { edges { node { id name } } }
             }
           }""",
        {"input": {"name": "comp-ai-crm-source", "workspaceId": WORKSPACE}},
    )["projectCreate"]
    project_id = project["id"]
    env_id = next(
        e["node"]["id"] for e in project["environments"]["edges"] if e["node"]["name"] == "production"
    )
    print(f"project {project_id}  env {env_id}")

    pg_password = rand(32)
    better_auth_secret = rand(32)
    bridge_secret = rand(32)
    cron_secret = rand(32)

    database_url = (
        f"postgresql://postgres:{pg_password}@postgres.railway.internal:5432/railway"
    )

    def service(name, image, variables, start=None, healthcheck=None, cron=None):
        svc = q(
            """mutation($input: ServiceCreateInput!) {
                 serviceCreate(input: $input) { id name }
               }""",
            {
                "input": {
                    "projectId": project_id,
                    "environmentId": env_id,
                    "name": name,
                    "source": {"image": image},
                    "variables": variables,
                }
            },
        )["serviceCreate"]
        print(f"  service {name} {svc['id']}")

        update = {}
        if start:
            update["startCommand"] = start
        if healthcheck:
            update["healthcheckPath"] = healthcheck
        if cron:
            update["cronSchedule"] = cron
            update["restartPolicyType"] = "NEVER"
        else:
            update["restartPolicyType"] = "ON_FAILURE"
        if update:
            q(
                """mutation($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
                     serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input)
                   }""",
                {"serviceId": svc["id"], "environmentId": env_id, "input": update},
            )
        return svc["id"]

    postgres = service(
        "postgres",
        PG_IMAGE,
        {
            "PGDATA": "/var/lib/postgresql/data/pgdata",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": pg_password,
            "POSTGRES_DB": "railway",
            "SSL_CERT_DAYS": "820",
        },
    )
    q(
        """mutation($input: VolumeCreateInput!) { volumeCreate(input: $input) { id } }""",
        {
            "input": {
                "projectId": project_id,
                "environmentId": env_id,
                "serviceId": postgres,
                "mountPath": "/var/lib/postgresql/data",
            }
        },
    )

    common = {
        "DATABASE_URL": database_url,
        "BETTER_AUTH_SECRET": better_auth_secret,
        "ALLOWED_SIGN_IN": allowed_sign_in,
        "GOOGLE_CLIENT_ID": google_id,
        "GOOGLE_CLIENT_SECRET": google_secret,
        "AGENT_URL": "http://agent.railway.internal:2000",
    }

    # The app is created first so its public domain exists before the API and
    # the agent are told what it is.
    app = service(
        "app",
        IMAGE,
        {
            **common,
            "PORT": "3000",
            "API_URL": "http://api.railway.internal:3001",
            "AGENT_BRIDGE_SECRET": bridge_secret,
        },
        start="/usr/local/bin/entrypoint.sh app",
        # Not /sign-in: Railway rejects a healthcheckPath containing a hyphen,
        # with "Error in healthcheckPath - Invalid input" and nothing about why.
        # This one is better anyway, because it is only green once the app, the
        # proxy and the API are all working.
        healthcheck="/api/auth/ok",
    )
    domain = q(
        """mutation($input: ServiceDomainCreateInput!) {
             serviceDomainCreate(input: $input) { domain }
           }""",
        {
            "input": {
                "environmentId": env_id,
                "serviceId": app,
                "targetPort": 3000,
            }
        },
    )["serviceDomainCreate"]["domain"]
    origin = f"https://{domain}"
    print(f"  app origin {origin}")

    q(
        """mutation($input: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $input) }""",
        {
            "input": {
                "projectId": project_id,
                "environmentId": env_id,
                "serviceId": app,
                "replace": False,
                "variables": {"APP_URL": origin},
            }
        },
    )

    service(
        "api",
        IMAGE,
        {
            **common,
            "PORT": "3001",
            "API_URL": origin,
            "APP_URL": origin,
            "CRON_SECRET": cron_secret,
        },
        start="/usr/local/bin/entrypoint.sh api",
        healthcheck="/health",
        # No AGENT_BRIDGE_SECRET here, and only here. It is what lets the API ask
        # the agent whether a pasted Context key is real, and upstream blocks
        # every page until a key is saved. Context is a separate company whose
        # signup is not reliably instant, so with the check on, a fresh deploy is
        # a running, billing install nobody can open. Unset, upstream saves the
        # key unchecked and a placeholder gets the deployer in. Costs the instant
        # research poke on a newly added company; the Agent tab is unaffected
        # because that runs on the front end's own copy.
    )

    service(
        "agent",
        IMAGE,
        {
            **common,
            "AGENT_PORT": "2000",
            "AGENT_BRIDGE_SECRET": bridge_secret,
            "API_URL": origin,
            "APP_URL": origin,
            "AI_GATEWAY_API_KEY": "",
        },
        start="/usr/local/bin/entrypoint.sh agent",
    )

    service(
        "cron",
        IMAGE,
        {
            "CRON_TARGET": "http://api.railway.internal:3001",
            "CRON_SECRET": cron_secret,
        },
        start="/usr/local/bin/entrypoint.sh cron",
        cron="*/15 * * * *",
    )

    print(json.dumps({"projectId": project_id, "environmentId": env_id, "origin": origin}, indent=2))


if __name__ == "__main__":
    main()
