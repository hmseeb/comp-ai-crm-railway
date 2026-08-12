#!/usr/bin/env bash
# Drives a live deployment the way a deployer would, against its public address.
#
# Everything here can run without a Google account. Signing in for real needs
# one, and that part is done by hand; what this covers is every piece of wiring
# that has to be right before Google is even reached, including the one that
# fails silently: the callback address Better Auth hands to Google.
#
# Usage: live.sh https://your-app.up.railway.app
set -uo pipefail

ORIGIN=${1:?usage: live.sh <origin>}
JAR=$(mktemp)
pass=0
fail=0

ok()   { echo "ok    $1"; pass=$((pass + 1)); }
bad()  { echo "FAIL  $1"; fail=$((fail + 1)); }

echo "== $ORIGIN =="

# The API is alive and can see Postgres, which also means migrations applied.
if curl -fsS --max-time 20 "$ORIGIN/api/health" | grep -q '"database":"up"'; then
	ok "api reachable through the app, database up"
else
	bad "api reachable through the app, database up"
fi

# A signed-out visitor is sent to sign-in rather than shown a stack trace.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$ORIGIN/")
case "$code" in
2* | 3*) ok "root answers ($code)" ;;
*) bad "root answers (got $code)" ;;
esac

if curl -fsS --max-time 20 "$ORIGIN/sign-in" | grep -qi google; then
	ok "sign-in page offers the Google button"
else
	bad "sign-in page offers the Google button (credentials probably not set)"
fi

# The one that matters. Better Auth builds the callback from API_URL, and if
# that is the API's own address instead of this one, Google will happily send
# the browser somewhere the app can never read the resulting cookie from. The
# healthcheck stays green the whole time.
consent=$(curl -fsS --max-time 20 -c "$JAR" -b "$JAR" \
	-H 'Content-Type: application/json' -H "Origin: $ORIGIN" \
	-X POST "$ORIGIN/api/auth/sign-in/social" \
	-d '{"provider":"google","callbackURL":"/"}' 2>/dev/null)

redirect=$(printf '%s' "$consent" |
	python3 -c 'import sys,json,urllib.parse as u
try:
    url = json.load(sys.stdin).get("url", "")
except Exception:
    sys.exit(0)
print(u.parse_qs(u.urlparse(url).query).get("redirect_uri", [""])[0])' 2>/dev/null)

if [ "$redirect" = "$ORIGIN/api/auth/callback/google" ]; then
	ok "google callback points back at this origin"
else
	bad "google callback points back at this origin (got '${redirect:-nothing}')"
fi

if printf '%s' "$consent" | grep -q 'accounts.google.com'; then
	ok "sign-in hands off to Google"
else
	bad "sign-in hands off to Google"
fi

# The session cookie has to be set by this origin, not by the API's hostname,
# or the app cannot read it back.
if grep -q "$(printf '%s' "$ORIGIN" | sed 's#https\?://##')" "$JAR" 2>/dev/null; then
	ok "auth cookies are written for this origin"
else
	bad "auth cookies are written for this origin"
fi

# The app proxies the agent on its own origin and checks the session first, so
# an anonymous call must be refused rather than reaching the agent.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$ORIGIN/eve/v1/sessions")
case "$code" in
401 | 403 | 404) ok "agent bridge refuses an anonymous caller ($code)" ;;
502 | 504) bad "agent bridge cannot reach the agent ($code)" ;;
*) ok "agent bridge answers ($code)" ;;
esac

rm -f "$JAR"
echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
