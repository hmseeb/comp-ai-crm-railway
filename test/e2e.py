#!/usr/bin/env python3
"""Drives a scratch deploy past the OAuth wall and through the real CRM path.

Sign-in is Google or Microsoft only, and neither can be driven headless. So this
does what a successful OAuth callback leaves behind and nothing more: a user
row, the workspace membership ensureWorkspaceMembership() writes, and a session
row, then the session cookie Better Auth would set, signed with this deploy's
BETTER_AUTH_SECRET. Everything after that goes through the public app origin
exactly as a browser would: the app's proxy, the API's auth middleware, tRPC,
Prisma, Postgres.

Needs Docker (psql runs from postgres:17-alpine) and a TCP proxy on the
Postgres service (scratch only).

Usage: e2e.py <projectId>
Reads secrets from the Railway API at runtime and never prints them.
"""

import base64
import hashlib
import hmac
import json
import pathlib
import secrets
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path("~/.claude/skills/create-template-for-railway/scripts").expanduser()))
import rw  # noqa: E402

PROJECT = sys.argv[1]
EMAIL = f"e2e-{secrets.token_hex(3)}@example.com"

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("ok    " if ok else "FAIL  ") + name + (f" ({detail})" if detail else ""))
    if not ok:
        failures += 1


proj = rw.gql(
    """query($id:String!){project(id:$id){environments{edges{node{id}}}
         services{edges{node{id name}}}}}""",
    {"id": PROJECT},
)["project"]
env_id = proj["environments"]["edges"][0]["node"]["id"]
svc = {e["node"]["name"]: e["node"]["id"] for e in proj["services"]["edges"]}


def variables(service):
    return rw.gql(
        "query($p:String!,$e:String!,$s:String!){variables(projectId:$p,environmentId:$e,serviceId:$s)}",
        {"p": PROJECT, "e": env_id, "s": svc[service]},
    )["variables"]


api_vars = variables("api")
pg_vars = variables("postgres")
origin = variables("app")["APP_URL"].rstrip("/")
secret = api_vars["BETTER_AUTH_SECRET"]

proxy = rw.gql(
    "query($e:String!,$s:String!){tcpProxies(environmentId:$e,serviceId:$s){domain proxyPort}}",
    {"e": env_id, "s": svc["postgres"]},
)["tcpProxies"]
if not proxy:
    sys.exit("no TCP proxy on postgres")
dsn = (
    f"postgresql://{pg_vars['POSTGRES_USER']}:{urllib.parse.quote(pg_vars['POSTGRES_PASSWORD'])}"
    f"@{proxy[0]['domain'].rstrip('.')}:{proxy[0]['proxyPort']}/{pg_vars['POSTGRES_DB']}?sslmode=require"
)


def sql(statement):
    out = subprocess.run(
        ["docker", "run", "--rm", "-i", "-e", "DSN", "postgres:17-alpine",
         "sh", "-c", 'psql "$DSN" -v ON_ERROR_STOP=1 -At'],
        input=statement, capture_output=True, text=True, timeout=120,
        env={**os.environ, "DSN": dsn},
    )
    if out.returncode:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.strip()


# What a first OAuth callback leaves in the database.
user_id = "e2e-" + secrets.token_hex(8)
token = secrets.token_urlsafe(24)
sql(f"""
INSERT INTO "user"(id,name,email,"emailVerified","createdAt","updatedAt")
  VALUES ('{user_id}','E2E Rep','{EMAIL}',true,now(),now());
INSERT INTO organization(id,name,slug,"createdAt") VALUES ('workspace','CRM','crm',now())
  ON CONFLICT (id) DO NOTHING;
INSERT INTO member(id,"organizationId","userId",role,"createdAt")
  VALUES ('{secrets.token_hex(8)}','workspace','{user_id}','owner',now());
INSERT INTO session(id,"expiresAt",token,"createdAt","updatedAt","userId","activeOrganizationId")
  VALUES ('{secrets.token_hex(8)}',now()+interval '1 day','{token}',now(),now(),'{user_id}','workspace');
""")

signature = base64.b64encode(hmac.new(secret.encode(), token.encode(), hashlib.sha256).digest()).decode()
signed = urllib.parse.quote(f"{token}.{signature}", safe="")
# Better Auth adds the __Secure- prefix only when it decides it is in production;
# send both names so the test does not depend on that.
cookie = f"__Secure-crm.session_token={signed}; crm.session_token={signed}"


def call(method, path, body=None, authed=True):
    req = urllib.request.Request(origin + path, method=method)
    req.add_header("Origin", origin)
    req.add_header("Content-Type", "application/json")
    if authed:
        req.add_header("Cookie", cookie)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            raw = r.read().decode()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def trpc_query(proc, payload):
    q = urllib.parse.quote(json.dumps(payload))
    return call("GET", f"/api/trpc/{proc}?input={q}")


def trpc_mutation(proc, payload):
    return call("POST", f"/api/trpc/{proc}", payload)


code, body = call("GET", "/api/auth/get-session")
check("session cookie accepted through the app proxy", code == 200 and EMAIL in body, f"{code}")

code, body = call("GET", "/api/auth/get-session", authed=False)
check("anonymous get-session is empty", code == 200 and EMAIL not in body, f"{code}")

run = secrets.token_hex(3)
name = f"E2E Robotics {run}"
code, body = trpc_mutation("companies.create", {"name": name, "domain": f"e2e-robotics-{run}.example"})
check("companies.create", code == 200 and name in body, f"{code} {body[:160]}")
company_id = None
try:
    company_id = json.loads(body)["result"]["data"]["id"]
except Exception:
    pass

code, body = trpc_query("companies.byId", {"id": company_id})
check("companies.byId reads it back", code == 200 and name in body, f"{code}")

stored = sql(f"SELECT count(*) FROM company WHERE name = '{name}'")
check("row is in Postgres", stored == "1", stored)

req_code, _ = call("POST", "/api/trpc/companies.create", {"name": "anon"}, authed=False)
check("anonymous tRPC write refused", req_code in (401, 403), f"{req_code}")

# The agent is reached through the app's own origin, behind the same session:
# the app mints a bridge token for the signed-in rep and the named record, and
# the agent verifies it. A 2xx means the whole hop worked. The agent's model
# call afterwards needs AI_GATEWAY_API_KEY, which this test does not have.
def agent_create(authed):
    req = urllib.request.Request(origin + "/eve/v1/session", method="POST")
    for k, v in {"Origin": origin, "Content-Type": "application/json", "x-crm-company": company_id or ""}.items():
        req.add_header(k, v)
    if authed:
        req.add_header("Cookie", cookie)
    body = json.dumps({"message": "In one sentence, what does this company do?"}).encode()
    try:
        with urllib.request.urlopen(req, body, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


code, body = agent_create(authed=False)
# The app's middleware sends a signed-out POST to sign-in before the route runs.
check("agent bridge refuses an anonymous caller", code in (307, 401), f"{code}")
code, body = agent_create(authed=True)
check("agent accepts a session for the record via the bridge", 200 <= code < 300, f"{code} {body[:160]}")

code, body = call("GET", "/")
check("app root renders for a signed-in rep", code == 200, f"{code}")

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
