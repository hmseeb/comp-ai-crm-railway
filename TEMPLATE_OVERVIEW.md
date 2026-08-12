# Deploy and Host Comp AI CRM on Railway

Comp AI CRM is an open source CRM built the other way round from the usual: the
agent is not a feature bolted onto a form, the CRM is where the agent keeps its
notes. It runs on its own deployment, on its own schedule, against its own work
queue. It decides which contact to look at next, spends a research budget, books
its own follow-ups, and stops when the budget runs out. Close the browser and it
keeps going.

The rule it never breaks is that nothing about a person is guessed. No tool
accepts a confidence score, because a model asked to grade its own certainty
will, and it will be wrong in the direction that makes it look useful. Tools
report what they observed, a ledger prices the evidence, strong evidence writes
to the record and weak evidence becomes a suggestion a human settles. A
confidently wrong fact about a customer is worse than a blank field, because
nobody can tell it is wrong.

## About Hosting Comp AI CRM

This deploys five pieces: the Next.js front end, a NestJS API that handles auth
and mailbox sync, the research agent as its own long-running deployment, a
scheduled job that syncs mailboxes every fifteen minutes, and Postgres.

Only the front end gets a public address. That is not a hardening choice, it is
the only arrangement in which signing in works. The front end already forwards
every API and agent request through itself, and the sign-in client talks to
whatever origin the browser is on, so one public address keeps the session cookie
first-party. Giving the API its own address would put the cookie on a hostname
the front end can never read, and Railway's generated subdomains cannot share a
cookie between them.

Sign-in is Google or Microsoft, and that is not configurable. There is no email
and password mode. You bring your own OAuth client and add the redirect address
after the deploy, because it contains the address Railway just gave you. Budget
two minutes for it. A single variable, an email domain or address, decides who is
allowed in at all.

The agent needs a Vercel AI Gateway key to think with, and nothing else. Every
outside data source it can use is optional and it is designed to run with none of
them: with no keys it reads your own threads, meetings and email signature
blocks, which is free and is the strongest evidence there is.

## Common Use Cases

- A small sales team that wants contact and company records to fill themselves in
  from the email and calendar history the team already has, rather than paying a
  data vendor for a worse copy of it.
- A founder-led pipeline where nobody has time to write notes after calls, and
  the useful question is "what changed about this account since I last looked".
- Anyone who wants their customer data, their mailbox contents and their model
  usage inside infrastructure they control, on a licence that lets them fork it.

## Dependencies for Comp AI CRM Hosting

- Postgres 17, included in this template.
- A Google Cloud OAuth client, or a Microsoft Entra app registration, or both.
  Free, and the only way to sign in.
- A Vercel AI Gateway key, if you want the research agent to do anything. The CRM
  itself works without one.

### Deployment Dependencies

- Source: [trycompai/crm](https://github.com/trycompai/crm), MIT licensed.
- Image: [hmseeb/comp-ai-crm-railway](https://github.com/hmseeb/comp-ai-crm-railway),
  pinned to upstream's `release` branch at commit `f2484fb`, which is v1.13.0
  plus 17 commits.
- [Google OAuth client setup](https://console.cloud.google.com/apis/credentials)
- [Vercel AI Gateway](https://vercel.com/docs/ai-gateway)

### Implementation Details

**What the template asks you for.** `ALLOWED_SIGN_IN` is required and is the
entire authorisation model. It takes an email domain, a single address, or a
comma-separated mix of both, and subdomains count.

```
acme.com                        everyone at your company
acme.com,contractor@gmail.com   plus one outsider
you@gmail.com                   a one-person install
```

Leave it empty and nobody can sign in, including you. `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` and `AI_GATEWAY_API_KEY` are optional at deploy time and
can be filled in afterwards.

**After the deploy, to be able to sign in.** Copy your app's address from
Railway, then in the [Google Cloud
console](https://console.cloud.google.com/apis/credentials) create an OAuth
client ID of type Web application and add this as an authorised redirect URI:

```
https://<your app address>/api/auth/callback/google
```

It is the app's address, not the API's, exactly, with https and no trailing
slash. Then enable the Gmail API and the Calendar API for that project, because
the same client that signs you in is what reads your mail and calendar. Put the
client ID and secret on the `api` service; the `app` service reads both by
reference, so you set them once.

Microsoft is the same shape: `MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET`
on the `api` service, redirect URI ending `/api/auth/callback/microsoft`, and the
delegated Graph permissions `User.Read` and `Mail.Read`. You can run both
providers at once.

Mail access is read-only in both directions: the CRM can list and read messages
and cannot send, reply, move or delete anything. Reading is forward-only, so the
first sync records the current time and imports nothing. Connecting a ten-year-old
mailbox does not dump a decade into your CRM.

**Optional variables you can add later.** Each one opens exactly one more place
the agent can look, and it prints at startup which ones it has.

| Variable | Service | What it unlocks |
| --- | --- | --- |
| `PERPLEXITY_API_KEY` | agent | Open-web research with citations |
| `RAPIDAPI_KEY` | agent | LinkedIn profiles, via LinkDAPI |
| `GITHUB_TOKEN` | agent | A higher rate limit when matching contacts to GitHub |
| `BLOB_READ_WRITE_TOKEN` | api, agent | Storage, so logos and photos survive the source going away |
| `REDIS_URL` | api, app | A shared cache, once more than one copy of the API runs |
| `IS_MARKETING` | app | Serves upstream's landing page at the root instead of sign-in |
| `CRM_TELEMETRY_DISABLED` | api, agent | Set to 1 and this install reports nothing |

Company logos and brand colours come from Context and are asked for inside the
app on Settings then General, not as a variable, so adding it needs no redeploy.

**Anonymous usage telemetry is on**, at upstream's default. One event a day:
contact counts in bands rather than exactly, which agent tools ran, which
optional keys are set as yes or no. Server side only, no session replay, no IP
address, and no name, company, subject line, amount or key ever leaves. Set
`CRM_TELEMETRY_DISABLED=1` on `api` and `agent` to switch it off entirely.

**First boot.** The `api` service applies database migrations before it starts
and retries for half a minute if Postgres is not up yet, so a restart or two on
the very first deploy is expected rather than a failure.

**If something looks wrong.**

- Sign-in page says no provider is configured: the Google client ID and secret
  are not both set on `api`. Setting only one of the pair is refused on purpose.
- Google returns `redirect_uri_mismatch`: the URI in the console has to match the
  app's address character for character, https included, no trailing slash.
- You sign in and land back on sign-in: your address is not covered by
  `ALLOWED_SIGN_IN`.
- The Agent tab says the agent is not configured: `AGENT_BRIDGE_SECRET` has to be
  identical on `api`, `app` and `agent`. The template generates it once on `api`
  and the other two reference it.
- No email ever appears: the sync runs on the `cron` service every fifteen
  minutes and its `CRON_SECRET` has to match the one on `api`. Reading is also
  forward-only, so send yourself something new and wait a quarter of an hour.

**Scaling.** This runs one copy of each service, which is the right size for a
team. If you scale the API past one copy, add Redis and set `REDIS_URL` on `api`
and `app`, because the tracking rate limit and the hourly cap on contacts created
from forms are counted in the cache and per-instance counters would let a
multi-instance deployment exceed both.

## Why Deploy Comp AI CRM on Railway?

A CRM whose agent runs continuously is an awkward fit for serverless hosting: the
agent is not request-response, it holds durable sessions, leases work from a
queue and resumes where it stopped after a redeploy. Railway runs it as what it
is, a long-lived process next to its database, with the front end and API beside
it on one private network.

It also solves the part of this app that is genuinely fiddly to self-host by
hand. The five pieces have to agree on a database URL, a signing secret and a
bridge secret, and get the public address right in three places or sign-in fails
in ways that look like something else. Here that wiring is done, the secrets are
generated for you, and the only address you have to type anywhere is the one you
paste into Google.

Your customer data and your mailbox contents stay in a Postgres instance you own,
on an MIT licensed codebase you can fork, with no per-seat pricing between you
and your own pipeline.
