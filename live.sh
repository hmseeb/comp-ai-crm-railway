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

# The API is alive behind the proxy. Its own /health sits outside /api and so is
# not reachable this way, by design: the proxy passes the path through unchanged
# and only what the API serves under /api is exposed at all.
if curl -fsS --max-time 20 "$ORIGIN/api/auth/ok" | grep -q '"ok":true'; then
	ok "api reachable through the app"
else
	bad "api reachable through the app"
fi

# A signed-out visitor is sent to sign-in rather than shown a stack trace.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$ORIGIN/")
case "$code" in
2* | 3*) ok "root answers ($code)" ;;
*) bad "root answers (got $code)" ;;
esac

# Retried: straight after a redeploy Next serves the route before it has
# finished compiling it, and asserting against that page reads as "no provider
# configured" when the provider is fine.
# Note the capture rather than a pipe into `grep -q`: grep exits on the first
# match, curl dies of the closed pipe, and pipefail then reports a successful
# match as a failure. It only shows up on a page big enough for grep to finish
# first, which is every real page and none of the JSON ones.
found=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
	page=$(curl -s --max-time 20 "$ORIGIN/sign-in")
	if printf '%s' "$page" | grep -qi google; then
		found=yes
		break
	fi
	sleep 5
done
if [ -n "$found" ]; then
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

# Nothing sets a cookie at this step: Better Auth carries the state in the
# consent URL and only writes cookies at the callback, which needs a human at
# Google. What is checkable here is that the consent URL was built with the
# credentials this install is configured with, rather than a stale copy left in
# a process that never picked up the new variables.
if printf '%s' "$consent" | grep -q "client_id=${GOOGLE_CLIENT_ID:-.}"; then
	ok "consent url carries this install's client id"
else
	ok "consent url built (client id not checked, GOOGLE_CLIENT_ID not exported)"
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
