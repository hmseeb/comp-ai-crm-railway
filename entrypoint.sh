#!/bin/sh
# One image, three roles. Railway execs the start command raw with no shell, so
# each service's start command is `/usr/local/bin/entrypoint.sh <role>`.
set -eu

role="${1:-app}"

case "$role" in
api)
	# Prisma's config uses paths relative to the package, so migrations only run
	# from here. Retried because on a fresh template deploy this container and
	# Postgres come up together and losing that race would otherwise burn the
	# service's first boot.
	cd /app/packages/db
	attempt=1
	until bunx prisma migrate deploy; do
		if [ "$attempt" -ge 20 ]; then
			echo "[entrypoint] migrations failed after $attempt attempts" >&2
			exit 1
		fi
		echo "[entrypoint] database not ready, retrying migrations ($attempt/20)"
		attempt=$((attempt + 1))
		sleep 5
	done

	cd /app/apps/api
	# NestJS listens with the host omitted, which Node binds dual-stack, so this
	# answers on both Railway's IPv6 private network and its IPv4 healthchecker.
	exec bun dist/main.js
	;;

app)
	cd /app/apps/app
	# `next start` defaults to 0.0.0.0, which Railway's edge cannot reach over
	# IPv6. `-H ::` covers both families.
	exec node ./node_modules/.bin/next start -H :: -p "${PORT:-3000}"
	;;

agent)
	cd /app/apps/agent
	# Upstream's start script spawns `eve start --port N` and nothing else, which
	# leaves the agent bound to 127.0.0.1: reachable from inside its own
	# container and from nowhere the app or the API can dial. Calling eve
	# directly is the whole fix, and it also puts the signal handling back in the
	# process that actually needs it. PATH so eve can find its own tooling.
	PATH="/app/apps/agent/node_modules/.bin:/app/node_modules/.bin:$PATH"
	export PATH
	exec node ./node_modules/.bin/eve start --host :: --port "${AGENT_PORT:-${PORT:-2000}}"
	;;

cron)
	# Runs on a Railway schedule and exits. Upstream expects an outside
	# scheduler to poke these three; without the first one no mailbox is ever
	# read, which is the evidence the agent is built around.
	base="${CRON_TARGET:-http://api.railway.internal:3001}"
	auth="Authorization: Bearer ${CRON_SECRET:?CRON_SECRET is required}"

	echo "[cron] syncing mailboxes"
	curl -fsS -m 300 -X POST -H "$auth" "$base/internal/sync/mailboxes" || echo "[cron] mailbox sync failed" >&2

	# Daily work, on the 03:00 tick of a schedule that also fires more often.
	if [ "$(date -u +%H)" = "03" ] && [ "$(date -u +%M)" -lt 15 ]; then
		echo "[cron] refreshing exchange rates"
		curl -fsS -m 120 -X POST -H "$auth" "$base/internal/sync/rates" || echo "[cron] rates refresh failed" >&2
		echo "[cron] sweeping tracked page views older than 90 days"
		curl -fsS -m 300 -X POST -H "$auth" "$base/internal/tracking/retention" || echo "[cron] retention sweep failed" >&2
	fi
	;;

*)
	echo "[entrypoint] unknown role: $role (expected api, app or agent)" >&2
	exit 1
	;;
esac
