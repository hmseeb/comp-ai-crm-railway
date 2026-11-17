#!/usr/bin/env bash
# The cron role must call every internal job upstream schedules in
# apps/api/vercel.json. A job nobody calls fails silently: nothing errors, the
# work just never happens.
#
# Runs entrypoint.sh with fake curl and date on PATH; no network, no Docker.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
bin=$(mktemp -d)
trap 'rm -rf "$bin"' EXIT

cat >"$bin/curl" <<'EOF'
#!/bin/sh
for arg in "$@"; do last="$arg"; done
echo "$last" >>"$CALLS"
EOF
cat >"$bin/date" <<'EOF'
#!/bin/sh
case "$*" in
*%H*) echo "$FAKE_HOUR" ;;
*%M*) echo "$FAKE_MIN" ;;
*) /bin/date "$@" ;;
esac
EOF
chmod +x "$bin/curl" "$bin/date"

fail=0
run() { # run <hour> <minute> -> prints called URLs
	CALLS="$bin/calls" FAKE_HOUR=$1 FAKE_MIN=$2 PATH="$bin:$PATH" \
		CRON_SECRET=test CRON_TARGET=http://api \
		sh "$here/../entrypoint.sh" cron >/dev/null 2>&1
	cat "$bin/calls" 2>/dev/null
	rm -f "$bin/calls"
}
expect() { # expect <name> <haystack> <needle> <present|absent>
	if printf '%s\n' "$2" | grep -qx "$3"; then has=present; else has=absent; fi
	if [ "$has" = "$4" ]; then echo "ok    $1"; else echo "FAIL  $1"; fail=1; fi
}

daily=$(run 03 05)
expect "daily tick syncs mailboxes" "$daily" "http://api/internal/sync/mailboxes" present
expect "daily tick refreshes rates" "$daily" "http://api/internal/sync/rates" present
expect "daily tick sweeps page views" "$daily" "http://api/internal/tracking/retention" present
expect "daily tick prunes the archive" "$daily" "http://api/internal/archive/prune" present

other=$(run 10 30)
expect "other ticks sync mailboxes" "$other" "http://api/internal/sync/mailboxes" present
expect "other ticks skip the archive prune" "$other" "http://api/internal/archive/prune" absent

[ "$fail" -eq 0 ]
