#!/usr/bin/env bash
# What the image has to be true for, before any of it reaches Railway.
#
# Each check exists because getting it wrong is silent: a healthcheck stays
# green while the thing the deployer actually wants is broken.
set -uo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose -f compose.yml -p crm-contract"
APP=http://localhost:3000
API=http://localhost:3001
AGENT=http://localhost:2000

pass=0
fail=0

check() {
	local name="$1"
	shift
	if "$@" >/dev/null 2>&1; then
		echo "ok    $name"
		pass=$((pass + 1))
	else
		echo "FAIL  $name"
		fail=$((fail + 1))
	fi
}

status() { # status <url> <expected-codes-regex>
	local code
	code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$1")
	[[ $code =~ $2 ]] || { echo "  got $code from $1" >&2; return 1; }
}

body_has() { curl -s --max-time 20 "$1" | grep -qi "$2"; }

wait_for() { # wait_for <url> <seconds>
	local deadline=$((SECONDS + $2))
	while ((SECONDS < deadline)); do
		curl -sf -o /dev/null --max-time 5 "$1" && return 0
		sleep 3
	done
	return 1
}

teardown() { $COMPOSE down -v --remove-orphans >/dev/null 2>&1; }
trap teardown EXIT

echo "== booting =="
teardown
$COMPOSE up -d >/dev/null 2>&1 || { echo "compose up failed"; $COMPOSE logs --tail 40; exit 1; }

wait_for "$API/health" 180 || { echo "FAIL  api never became healthy"; $COMPOSE logs api --tail 60; exit 1; }
wait_for "$APP/sign-in" 180 || { echo "FAIL  app never became healthy"; $COMPOSE logs app --tail 60; exit 1; }

echo "== contract =="

# The API is up and can actually reach Postgres. It returns 503 rather than 500
# when the database is down, so a 200 here means migrations applied too.
check "api /health reports the database up" body_has "$API/health" '"database":"up"'

# The whole single-origin design rests on this proxy. If it breaks, sign-in
# breaks, and nothing else about the app looks wrong.
check "app proxies /api/health to the API" body_has "$APP/api/health" '"database":"up"'

# The app's proxy target is compiled in at image build time, so this is really a
# check that the baked address is the private one and not a localhost fallback.
check "proxy target is the private API address, not localhost" \
	bash -c "docker exec crm-contract-app-1 grep -rql 'api.railway.internal' /app/apps/app/.next"

# Sign-in is the only way in, and the buttons are rendered from whether the
# provider pair is set. An install where the pair is missing shows a page that
# says so, which passes a healthcheck and helps nobody.
check "sign-in page offers the Google button" body_has "$APP/sign-in" 'google'

# Better Auth is mounted under the API and reached through the proxy. Its own
# route answering on the app's origin is what makes the session cookie
# first-party.
check "auth routes answer on the app origin" status "$APP/api/auth/ok" '^(200|404)$'

# An unauthenticated visitor gets sent to sign-in rather than a stack trace.
check "root redirects an anonymous visitor" status "$APP/" '^(200|302|307)$'

# The agent is a separate deployment the app proxies to. Upstream's start script
# honours AGENT_PORT; if it ever stops listening there the Agent tab dies quietly.
check "agent listens on its configured port" status "$AGENT/" '^(200|301|302|307|400|401|403|404)$'

# Railway dials containers over IPv6. An app listening only on 0.0.0.0 is
# unreachable there while looking perfectly healthy in its own logs.
check "app listens on IPv6" \
	bash -c "docker exec crm-contract-app-1 curl -sf -o /dev/null --max-time 5 'http://[::1]:3000/sign-in'"
check "api listens on IPv6" \
	bash -c "docker exec crm-contract-api-1 curl -sf -o /dev/null --max-time 5 'http://[::1]:3001/health'"
check "agent listens on IPv6" \
	bash -c "docker exec crm-contract-agent-1 curl -s -o /dev/null --max-time 5 'http://[::1]:2000/'"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
