# Comp AI CRM, built once and run three ways.
#
# Upstream ships no image at all: docker-compose.yml carries a bare Postgres and
# nothing else, and the three apps are expected to run from a `bun run dev` in a
# clone. So this image bakes the whole Turborepo build, and the three Railway
# services run the same image with different start commands.
#
# One image rather than three because the apps share a single node_modules tree
# and a single Prisma client; three images would each carry the same 2 GB of
# dependencies to save nothing.

FROM oven/bun:1.3.12-debian

# Pinned to the head of upstream's `release` branch, which is the branch upstream
# tells self-hosters to run. 17 commits past tag v1.13.0.
ARG CRM_REF=f2484fb08d1dd1357c1e3deddb97610cd8e6f1ed

# Baked into the browser bundle. next.config.ts re-exports API_URL as
# NEXT_PUBLIC_API_URL, and Next inlines NEXT_PUBLIC_* at build time, so the app's
# server-side proxy target is fixed here rather than at boot. It points at the
# API over Railway's private network, which is why the API never needs a public
# domain and the session cookie stays first-party on the app's origin.
ARG API_URL=http://api.railway.internal:3001
ARG APP_URL=http://localhost:3000

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates openssl curl \
 && rm -rf /var/lib/apt/lists/*

# Bun runs everything here except Next.js. `bun run build` segfaults partway
# through `next build` (panic at 2.7 GB RSS, well under the ceiling, so it is a
# Bun bug rather than memory), and `next start` is the same runtime on the same
# bundle. Upstream never hits this because it builds the front end on Vercel,
# which uses Node. One binary, no package manager, is all that takes.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

RUN git init -q . \
 && git remote add origin https://github.com/trycompai/crm.git \
 && git fetch -q --depth 1 origin "${CRM_REF}" \
 && git checkout -q FETCH_HEAD \
 && rm -rf .git

# `bun install` runs @crm/db's postinstall (`prisma generate`), which reads
# DATABASE_URL. A placeholder is enough: generation only needs it to parse.
ENV DATABASE_URL="postgresql://build:build@127.0.0.1:5432/build?schema=public"

RUN bun install --frozen-lockfile

# turbo.json declares API_URL and APP_URL as cache keys precisely because they
# are compiled in; everything else the build touches is passthrough. The
# DATABASE_URL placeholder is needed again here because `eve build` evaluates the
# agent's authored modules, and those import @crm/db, which throws on an unset
# connection string.
ENV API_URL=${API_URL} \
    APP_URL=${APP_URL} \
    TURBO_TELEMETRY_DISABLED=1

RUN bunx turbo run build --filter='!app' \
 && cd apps/app && node ./node_modules/.bin/next build

# Only after the build, so the toolchain above still sees development mode.
ENV NODE_ENV=production

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Railway discards ENTRYPOINT and execs the start command raw, so the template
# calls `/usr/local/bin/entrypoint.sh <role>` explicitly. This default is only
# for local runs.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["app"]
