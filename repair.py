#!/usr/bin/env python3
"""Puts back everything templateGenerate strips.

Generation drops every literal variable value as an anti-secret measure, so what
comes out is the right shape with nothing in it: five services that would ask a
deployer for forty-odd values, none of which they could answer. It also loses
the cron schedule and the cron service's restart policy.

This writes the real config through templateUpsertConfig on the internal
endpoint, which is the composer's own Save button, then reads it back and checks
it field by field. All four input fields are required or it fails with a generic
error.

What templateGenerate hands back cannot be edited: passing its id to
templateUpsertConfig returns "Not Authorized", while the identical call against
a template this script created succeeds. So generation is treated as a read: its
config is copied into a fresh template, with a fresh UUID for the template and
for every service and volume inside it, because the server rejects reused ones
as an ID collision. Pass `new` as the id the first time and the id it prints
thereafter.

Usage: repair.py <templateId|new> <path-to-generated.json>
"""

import json
import pathlib
import re
import sys
import uuid

sys.path.insert(0, str(pathlib.Path("~/.claude/skills/create-template-for-railway/scripts").expanduser()))
import rw  # noqa: E402

WORKSPACE = "fc4796db-2c6c-4354-a564-d4a1d900af53"  # Auromations
NAME = "Comp AI CRM"

# The app's public address, which the API and the agent both have to agree on:
# the API mints the session cookie and builds the OAuth callback from it, and a
# callback that lands anywhere else leaves the cookie on a host the app cannot
# read. Empty in a live project, filled in on a template deploy.
APP_ORIGIN = "https://${{app.RAILWAY_PUBLIC_DOMAIN}}"
API_PRIVATE = "http://${{api.RAILWAY_PRIVATE_DOMAIN}}:3001"
AGENT_PRIVATE = "http://${{agent.RAILWAY_PRIVATE_DOMAIN}}:2000"
DB = "${{postgres.DATABASE_URL}}"

SIGN_IN_HELP = (
    "Who is allowed to sign in, and the only thing deciding it. An email domain "
    "(acme.com), a single address (you@gmail.com), or a comma-separated mix. "
    "Subdomains count. Leave it empty and nobody can sign in, including you."
)
GOOGLE_ID_HELP = (
    "Google OAuth client ID. Optional here because the redirect address only "
    "exists once this is deployed: afterwards, add "
    "https://<your app address>/api/auth/callback/google to the client in the "
    "Google Cloud console and put the pair on the api service. There is no "
    "email and password sign-in, so without Google or Microsoft nobody can get in."
)
GOOGLE_SECRET_HELP = (
    "Google OAuth client secret. Set it together with the client ID; half a pair "
    "is refused on purpose, because it renders a button that fails at Google."
)
GATEWAY_HELP = (
    "Vercel AI Gateway key, which is what the research agent thinks with. "
    "Optional: without it the CRM works fully and the agent does nothing."
)


def var(default=None, optional=False, description=None):
    v = {"isOptional": optional}
    if default is not None:
        v["defaultValue"] = default
    if description:
        v["description"] = description
    return v


SHARED_AUTH = {
    "BETTER_AUTH_SECRET": var("${{api.BETTER_AUTH_SECRET}}"),
    "ALLOWED_SIGN_IN": var("${{api.ALLOWED_SIGN_IN}}"),
    "GOOGLE_CLIENT_ID": var("${{api.GOOGLE_CLIENT_ID}}", optional=True),
    "GOOGLE_CLIENT_SECRET": var("${{api.GOOGLE_CLIENT_SECRET}}", optional=True),
}

VARIABLES = {
    # Railway's own Postgres config. DATABASE_URL lives here so the other four
    # services can reference it by name instead of each carrying a copy of the
    # password.
    "postgres": {
        "PGDATA": var("/var/lib/postgresql/data/pgdata", description="Where the database is initialized"),
        "PGHOST": var("${{RAILWAY_PRIVATE_DOMAIN}}"),
        "PGPORT": var("5432"),
        "PGUSER": var("${{POSTGRES_USER}}"),
        "PGDATABASE": var("${{POSTGRES_DB}}"),
        "PGPASSWORD": var("${{POSTGRES_PASSWORD}}"),
        "POSTGRES_DB": var("railway"),
        "POSTGRES_USER": var("postgres"),
        "POSTGRES_PASSWORD": var(
            '${{ secret(32, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") }}'
        ),
        "DATABASE_URL": var(
            "postgresql://${{PGUSER}}:${{POSTGRES_PASSWORD}}@${{RAILWAY_PRIVATE_DOMAIN}}:5432/${{PGDATABASE}}"
        ),
        "SSL_CERT_DAYS": var("820", optional=True),
        "RAILWAY_DEPLOYMENT_DRAINING_SECONDS": var("60", description="Let Postgres shut down cleanly"),
    },
    # The three generated secrets are minted here and referenced everywhere else.
    # A generator read twice returns two different values, so there is exactly
    # one place each of them can be defined.
    "api": {
        "PORT": var("3001"),
        "API_URL": var(APP_ORIGIN),
        "APP_URL": var(APP_ORIGIN),
        "AGENT_URL": var(AGENT_PRIVATE),
        "DATABASE_URL": var(DB),
        "BETTER_AUTH_SECRET": var("${{secret(32)}}"),
        "CRON_SECRET": var("${{secret(32)}}"),
        # AGENT_BRIDGE_SECRET is deliberately absent here, and only here. It is
        # what lets the API ask the agent whether a pasted Context key is real,
        # and Context is a separate company whose signup is not reliably
        # instant. With the API unable to ask, upstream saves the key unchecked,
        # so a deployer can put a placeholder in the onboarding box, reach their
        # CRM, and paste a real key later on Settings then General. Without this,
        # a fresh deploy is a running, billing install nobody can open.
        #
        # Upstream's own documented behaviour for an unset value, not a patch.
        # What it costs: a newly added company is researched on the agent's next
        # scheduled tick rather than the instant it is added. The Agent tab is
        # unaffected, because that runs on the front end's own copy of the
        # secret, which is set.
        "ALLOWED_SIGN_IN": var(description=SIGN_IN_HELP),
        "GOOGLE_CLIENT_ID": var("", optional=True, description=GOOGLE_ID_HELP),
        "GOOGLE_CLIENT_SECRET": var("", optional=True, description=GOOGLE_SECRET_HELP),
    },
    # API_URL is the private address here and nowhere else. It is also what is
    # compiled into the browser bundle at image build time, so the two agree
    # whichever one Next ends up reading. It resolves only while the API service
    # is named api.
    "app": {
        "PORT": var("3000"),
        "API_URL": var(API_PRIVATE),
        "APP_URL": var("https://${{RAILWAY_PUBLIC_DOMAIN}}"),
        "AGENT_URL": var(AGENT_PRIVATE),
        "DATABASE_URL": var(DB),
        "AGENT_BRIDGE_SECRET": var("${{agent.AGENT_BRIDGE_SECRET}}"),
        **SHARED_AUTH,
    },
    "agent": {
        "AGENT_PORT": var("2000"),
        "API_URL": var(APP_ORIGIN),
        "APP_URL": var(APP_ORIGIN),
        "AGENT_URL": var("http://${{RAILWAY_PRIVATE_DOMAIN}}:2000"),
        "DATABASE_URL": var(DB),
        "AI_GATEWAY_API_KEY": var("", optional=True, description=GATEWAY_HELP),
        # Minted here rather than on the API, which is the one service that must
        # not have it. The front end reads it from here.
        "AGENT_BRIDGE_SECRET": var("${{secret(32)}}"),
        **SHARED_AUTH,
    },
    "cron": {
        "CRON_TARGET": var(API_PRIVATE),
        "CRON_SECRET": var("${{api.CRON_SECRET}}"),
    },
}

# Lost in generation along with the values.
DEPLOY_PATCH = {
    "cron": {"cronSchedule": "*/15 * * * *", "restartPolicyType": "NEVER"},
}

# The same mark on all four application services rather than one framework logo
# each. They are four roles of one image, not four products, and Next.js and
# NestJS logos would say more about the stack than about what the service does.
# The mark is a dark glyph on a white rounded square, so it reads as a light tile
# on Railway's dark canvas. Pinned to the commit, so upstream moving the file
# cannot break it later.
APP_ICON = (
    "https://raw.githubusercontent.com/trycompai/crm/"
    "f2484fb08d1dd1357c1e3deddb97610cd8e6f1ed/apps/app/public/web-app-manifest-512x512.png"
)

ICONS = {
    "postgres": "https://devicons.railway.app/i/postgresql.svg",
    "app": APP_ICON,
    "api": APP_ICON,
    "agent": APP_ICON,
    "cron": APP_ICON,
}


def main():
    template_id = sys.argv[1]
    config = json.loads(pathlib.Path(sys.argv[2]).read_text())
    if "templateGenerate" in config:
        config = config["templateGenerate"]["serializedConfig"]

    if template_id == "new":
        template_id = str(uuid.uuid4())
        remap = {sid: str(uuid.uuid4()) for sid in config["services"]}
        rebuilt = {"buckets": config.get("buckets", {}), "services": {}}
        for sid, svc in config["services"].items():
            svc = json.loads(json.dumps(svc))
            if "volumeMounts" in svc:
                # Keyed by the owning service's id, so it has to move with it.
                svc["volumeMounts"] = {remap[k]: v for k, v in svc["volumeMounts"].items()}
            rebuilt["services"][remap[sid]] = svc
        config = rebuilt
        print("minting template", template_id)

    for service in config["services"].values():
        name = service["name"]
        service["variables"] = VARIABLES[name]
        if name in DEPLOY_PATCH:
            service.setdefault("deploy", {}).update(DEPLOY_PATCH[name])
        if name in ICONS:
            service["icon"] = ICONS[name]

    saved = rw.gql(
        """mutation($id: String!, $input: TemplateUpsertConfigInput!) {
             templateUpsertConfig(id: $id, input: $input) { id code }
           }""",
        {
            "id": template_id,
            "input": {
                "name": NAME,
                "workspaceId": WORKSPACE,
                "serializedConfig": config,
                "canvasConfig": {},
            },
        },
        internal=True,
    )["templateUpsertConfig"]
    print(f"saved  id {saved['id']}  code {saved['code']}")

    # Read it back. A write that returned success is not a write that landed,
    # and a reference keyed by UUID instead of name resolves to an empty string
    # on every deploy while still reporting SUCCESS.
    back = rw.gql(
        "query($c: String!) { template(code: $c) { serializedConfig } }",
        {"c": saved["code"]},
    )["template"]["serializedConfig"]

    problems = []
    for service in back["services"].values():
        name = service["name"]
        want = VARIABLES[name]
        got = service.get("variables", {})
        for key, spec in want.items():
            if key not in got:
                problems.append(f"{name}.{key} missing")
                continue
            if got[key].get("defaultValue") != spec.get("defaultValue"):
                problems.append(
                    f"{name}.{key} default is {got[key].get('defaultValue')!r}, wanted {spec.get('defaultValue')!r}"
                )
        for key, spec in DEPLOY_PATCH.get(name, {}).items():
            if service.get("deploy", {}).get(key) != spec:
                problems.append(f"{name}.deploy.{key} is {service.get('deploy', {}).get(key)!r}")

    # UUIDs inside a reference are the failure that looks exactly like a race.
    blob = json.dumps(back)
    for ref in re.findall(r"\$\{\{[^}]*\}\}", blob):
        if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", ref):
            problems.append(f"reference keyed by UUID: {ref}")

    for line in problems:
        print("MISMATCH", line)
    print("clean" if not problems else f"{len(problems)} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
