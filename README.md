# Comp AI CRM on Railway

A CRM where the agent is the point and the database is where it keeps its notes.
It runs on its own schedule against its own work queue: it decides which contact
to look at next, spends a research budget, books its own follow-ups, and writes
down only what it can show evidence for. Close the browser and it keeps going.

Upstream: [trycompai/crm](https://github.com/trycompai/crm), MIT, pinned to
`release` at `f2484fb` (v1.13.0 plus 17 commits).

## What gets deployed

| Service | What it is |
| --- | --- |
| **app** | The Next.js front end. The only service with a public address. |
| **api** | NestJS: auth, tRPC, mailbox sync. Private, reached through the app. |
| **agent** | The research agent, its own deployment on its own schedule. Private. |
| **cron** | Fires every 15 minutes to sync mailboxes. Idle in between. |
| **Postgres** | Your data. |

Everything the browser touches goes through one address, because that is the only
shape in which sign-in works here. The app already forwards `/api/*` and
`/eve/v1/*` to the other two services, so giving the API its own public address
would put the session cookie on a hostname the app can never read.

## Before you can sign in

There is no email and password. Upstream ships Google and Microsoft sign-in only,
and that is not a setting. Deploying gets you a running CRM with a sign-in page
that nobody can get through yet. Two things fix that, and one of them can only
happen after the deploy, because it needs the address Railway gives you.

**1. Set who is allowed in.** The template asks for `ALLOWED_SIGN_IN` while
deploying. It is the whole authorisation model: an email domain, a single
address, or a comma-separated mix.

```
acme.com                        everyone at your company
acme.com,contractor@gmail.com   plus one outsider
you@gmail.com                   a one-person install
```

Leave it empty and nobody can sign in, including you. Subdomains count, so
`acme.com` also admits `you@mail.acme.com`.

**2. Point a Google OAuth client at your new address.** Roughly two minutes.

1. Copy your app's address from Railway. It looks like
   `https://app-production-1234.up.railway.app`.
2. Go to the [Google Cloud console
   credentials page](https://console.cloud.google.com/apis/credentials) and make
   an OAuth client ID of type **Web application**.
3. Under **Authorised redirect URIs**, add exactly:
   `https://<your app address>/api/auth/callback/google`
   Note it is the app's address, not the API's. The app forwards the request on.
4. Turn on the [Gmail
   API](https://console.cloud.google.com/apis/library/gmail.googleapis.com) and
   the [Calendar
   API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
   for that project. The same client that signs you in is what reads your mail
   and calendar, which is where the agent gets its best evidence.
5. Put the client ID and secret into the `api` service's variables as
   `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The `app` service picks both up
   by reference, so you only set them once.

If you already had the client when you deployed, you can fill those two in at
deploy time and only step 3 is left afterwards.

Set both or neither. Half a pair is a sign-in button that fails at Google. If
your account is on a Google Workspace domain, set the consent screen to
**Internal** and nobody outside your organisation can even reach the prompt.

Microsoft works the same way and is set up the same way. Add
`MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET` to the `api` service, with
the redirect URI `https://<your app address>/api/auth/callback/microsoft` and the
delegated Graph permissions `User.Read` and `Mail.Read`. You can run both at
once; the sign-in page offers both buttons and each person's mail is read from
whichever they used.

Mail access is read-only in both directions. The CRM can list and read messages
and cannot send, reply, move or delete. Reading is forward-only: the first sync
records the current time and imports nothing, so connecting a ten-year-old
mailbox does not dump a decade into your CRM.

## The Context key on first sign-in

The first thing you see after signing in is a box asking for a Context API key.
Context is a separate company, upstream's brand data partner: it supplies the
logo, the colours, the industry and the real name behind a domain, which is the
difference between an account that arrives as itself and one that arrives as a
grey square with its initials in it.

Upstream blocks every page until something is saved there. **You can type a
placeholder and carry on**, and set a real key later on Settings then General.
Nothing else in the CRM depends on it.

That works because this template leaves `AGENT_BRIDGE_SECRET` off the `api`
service on purpose. That variable is what lets the API ask the agent whether a
pasted key is real; without it, upstream saves the key unchecked, which is its
own documented behaviour rather than a patch. The cost is small and worth
knowing: a company you have just added gets researched on the agent's next
scheduled pass instead of the instant you add it. The Agent tab is unaffected,
because that runs on the front end's own copy of the same secret.

If you would rather have the check, add `AGENT_BRIDGE_SECRET` to the `api`
service with the same value the `agent` service has. Do that only once you have
a real Context key, because after that a placeholder stops working and the CRM
stops opening.

## Giving the agent a brain

The agent reaches its model through the [Vercel AI
Gateway](https://vercel.com/docs/ai-gateway). On Vercel that is handled without a
key; anywhere else, including here, it needs one. The template asks for
`AI_GATEWAY_API_KEY` and it is optional.

Without it the CRM works completely and the agent does nothing. With it, the
agent starts working through its queue on its own. Which model it uses is a
setting on the settings page, not a variable.

The agent works with no other keys at all. It falls back to what your install
already knows, which is your own threads, meetings and the signature blocks in
them, and that is the best evidence available: no data vendor sells you a reply
from the person's own address. Each of these adds one more place it can look, and
it prints at startup which ones it has:

| Variable | Service | What it unlocks |
| --- | --- | --- |
| `PERPLEXITY_API_KEY` | agent | Open-web research with citations |
| `RAPIDAPI_KEY` | agent | LinkedIn profiles, via LinkDAPI |
| `GITHUB_TOKEN` | agent | A higher rate limit when matching contacts to GitHub |
| `BLOB_READ_WRITE_TOKEN` | api, agent | Vercel Blob, so logos and photos survive the source going away |

Company logos and colours come from [Context](https://link.context.dev/crm) and
are asked for inside the app on **Settings → General**, not as a variable, so you
can add it without a redeploy.

## What was left out

- **Redis.** A shared cache only matters once more than one copy of the API is
  running. One copy caching in its own memory is correct, just not shared. If you
  scale the API up, add a Redis service and set `REDIS_URL` on `api` and `app`.
- **Vercel Blob.** Without it, logos and favicons render straight from wherever
  they came from, and contact photographs are not stored at all, because a URL
  that works today and 404s next month is worse than initials.
- **The marketing landing page.** `/` sends a signed-out visitor to sign-in. Set
  `IS_MARKETING=true` on `app` if you want upstream's landing page instead.

## Anonymous usage telemetry

Upstream sends one event a day: how many contacts exist in bands rather than
exactly, which agent tools ran, which optional keys are set as yes-or-no. Server
side only, no session replay, no IP, and no name, company, subject line, amount
or key ever. It is tied to a random ID generated by your first migration. Left at
upstream's default here. To turn it off, set `CRM_TELEMETRY_DISABLED=1` on `api`
and `agent` and nothing is ever sent.

## If something looks wrong

**The sign-in page says no provider is configured.** `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` are not both set on the `api` service. Setting only one of
the pair is treated as a mistake and refused.

**Google returns "redirect_uri_mismatch".** The URI in the Google console has to
be the app's address with `/api/auth/callback/google` on the end, character for
character, https included. Not the API service, and no trailing slash.

**You sign in and land back on the sign-in page.** Your address is not covered by
`ALLOWED_SIGN_IN`. It is on the `api` service and the `app` service reads it by
reference, so change it in one place.

**The Agent tab says the agent is not configured.** `AGENT_BRIDGE_SECRET` has to
be the identical value on `app` and `agent`. The template generates it once on
`agent` and the front end references it; if you overwrite it in one place,
overwrite it in both. It is absent from `api` deliberately, see the Context key
section above.

**Everything is up but no email appears.** The mailbox sync runs on the `cron`
service every 15 minutes and needs `CRON_SECRET` to match the `api` service.
Reading is also forward-only, so mail sent before you connected the mailbox never
arrives. Send yourself something and wait a quarter of an hour.

**The first boot takes a while.** The `api` service applies database migrations
before it starts, and retries for half a minute if Postgres is not up yet. A
couple of restarts on the very first deploy is the expected shape, not a failure.

## Rebuilding the image

The three apps are built into one image, published from
[hmseeb/comp-ai-crm-railway](https://github.com/hmseeb/comp-ai-crm-railway). The
role is the first argument to the entrypoint: `api`, `app`, `agent` or `cron`.

The app's address for the API is compiled in at image build time, because
Next.js inlines `NEXT_PUBLIC_*` during the build and upstream's config derives it
from `API_URL`. It is baked as `http://api.railway.internal:3001`, which is why
the API service has to keep the name `api`.

`test/contract.sh` boots the whole thing under Docker and checks the things that
fail quietly: that the proxy actually reaches the API, that the compiled-in
address is the private one, that all three processes answer over IPv6.
