# Integrations

This is the reference for every outside tool Wesley reads from. Staff see the same facts in
plain language on the **Integrations** page (`/integrations`). If you maintain this app,
read "How a connector works" first, then the section for the tool that is misbehaving.

## What was verified and what was not

Be honest about this before trusting a number. "Tested" means automated tests with
realistic fake responses. "Live" means someone ran it against the real service.

| Connector | Tested with fake responses | Run against the real service |
|---|---|---|
| Planning Center | yes, using real response shapes | The calendar part ran in production before Phase 2. The new parts (services, groups, check-ins, publishing, registrations) have not. |
| YouTube | yes | no |
| Facebook and Instagram | yes | no. Metric and field names may need adjusting on first live run. |
| Constant Contact | yes | no. The list and contact request parameters are unverified. |
| Text In Church | yes, plus a built-in mock | no. The connect-card endpoint name and the webhook payload are unverified. |
| Subsplash | no API exists to test (see below) | not applicable |

Each connector reports its own problems on the Integrations page, so a wrong guess shows up
as a plain-language warning on the first sync instead of silently wrong numbers.

## How a connector works

Every connector lives in `connectors/` and follows one interface (`connectors/base.py`):

- **Auth.** OAuth connectors (`OAuthConnector`) store an encrypted token and refresh it
  themselves, including providers that rotate refresh tokens. Token-paste connectors
  (`StaticTokenConnector`) store one encrypted token.
- **Scheduled sync.** Each connector has an `interval_minutes`. The scheduler runs it with a
  Postgres lock so only one web worker syncs at a time. Jobs get a little random delay so
  they do not all start together.
- **Webhooks** where the service supports them. A webhook only triggers a sync. The payload
  is never trusted or stored as data.
- **Rate limits and retries.** `connectors/http.py` waits and retries on 429 and 5xx,
  honors `Retry-After`, and paces requests.
- **Idempotent upserts.** Syncing twice never duplicates rows. Each row is looked up by the
  service's own id.
- **Raw and normalized are separate.** The untouched response goes to `raw_payloads`
  (pruned by the nightly cleanup). The cleaned version goes to a normalized table such as
  `youtube_videos` or `pco_events`. Text In Church message text is never put in raw payloads.
- **Health.** `connectors/runner.py` turns the last runs into one status: Healthy, Stale,
  Failing, Waiting for access, Needs reconnect, Not set up, Turned off, or By hand or CSV.
- **Every run is recorded** in `sync_runs`: when, how long, rows fetched and changed, errors,
  and warnings. The Integrations page shows the last runs for each connector.
- **Tokens are encrypted** with `TOKEN_ENCRYPTION_KEY` (falls back to `SECRET_KEY`). If you
  change that key, every connector must be reconnected.

### Adding or replacing a connector

1. Copy the closest existing file in `connectors/`, set `key`, `title`, `interval_minutes`.
2. Implement `sync(ctx)`: call `ctx.store_raw(...)`, then `ctx.upsert(Model, lookup, values)`,
   and `ctx.note(resource, fetched, changed)` per resource. Call `ctx.warn(...)` for anything
   a person should know.
3. Register it in `connectors/__init__.py`.
4. Add its tables in a new migration, and its tests beside the existing connector tests.
5. Add the data permission it needs to `permissions.py` and regenerate `docs/ROLES.md`.

To replace a tool (for example Subsplash with Resi), add a new connector or streaming
source and turn the old one off on the Integrations page. History stays in place.

## Planning Center

- **Purpose.** Calendar events, service plans (and how many positions are still unfilled),
  groups, check-in counts, sermon episodes with their statistics, registration counts.
- **Auth.** OAuth, the same connection that already existed. Scopes asked for:
  `people calendar services groups check_ins publishing registrations` (`PCO_SCOPES`).
- **Setup.** `PCO_CLIENT_ID`, `PCO_CLIENT_SECRET`. After deploying Phase 2, an admin must
  **reconnect** Planning Center once so the new scopes are granted. Until then each new
  resource shows a plain warning saying which permission is missing.
- **Refresh.** Automatic. If it fails with an auth error, status becomes Needs reconnect.
- **Webhooks.** Set `PCO_WEBHOOK_SECRET`, then in Planning Center add a webhook pointing at
  `<APP_URL>/webhooks/planning_center`. Signatures (`X-PCO-Webhooks-Authenticity`) are
  verified. Without it, the 15 minute sync still runs.
- **Deliberately not synced.** People records (Phase 5 will look people up live, one at a
  time, with permission). Giving is never requested. Check-ins are counts only, never names.
- **Known limits.** Events that vanish from Planning Center are marked removed, not deleted.
  The sermon statistics attribute name was not verified live.

## YouTube

- **Purpose.** Videos, views, watch time, and peak concurrent viewers for live services.
  Feeds the streaming pane (source: API).
- **Auth.** OAuth with the same Google OAuth client used for sign-in, scopes
  `youtube.readonly` and `yt-analytics.readonly`.
- **Setup (Google Cloud console).** Enable **YouTube Data API v3** and **YouTube Analytics
  API**. Add the redirect URI `<APP_URL>/integrations/youtube/callback` to the OAuth client.
  The person who connects must manage the church channel. Because the client is Internal,
  that person needs a daltonfumc.com account.
- **Channel owned by another account.** If the church channel belongs to an account that is not
  a daltonfumc.com account, add a daltonfumc.com person as a Manager in YouTube Studio
  (Settings, Permissions), then set `YOUTUBE_CHANNEL_ID` on Railway to the church channel's id
  (Studio, Settings, Channel, Advanced settings; it starts with `UC`). Every request then
  names that channel instead of the signed-in person's own. Whether Google's Analytics API
  accepts a manager this way for the church's channel has to be confirmed on the first sync.
- **Service labels.** A video's service name comes from its title. Titles containing words
  configured in `service_labels` map to labels such as "Sunday service". Anything else is
  stored as the title and can be corrected by hand.
- **Known limits.** Analytics for a live stream can lag by a day. Not run against the real
  channel yet.

## Facebook and Instagram

- **Purpose.** Post performance, live video views, Instagram media.
- **Auth.** A Page access token pasted by an admin (the Integrations page has a form).
  Meta's Graph API is the only supported route.
- **What Meta does not give.** There is no peak concurrent viewers number in the API. The
  streaming pane leaves it blank for Facebook so it can be typed in by hand.
- **Token life.** A long-lived Page token made from a long-lived user token does not expire
  by time, but it dies if the person who created it changes their password, loses Page
  access, or removes the app. Then status becomes Needs reconnect: make a new token and
  paste it in.
- **App review.** Reading your own Page's data while the app is in development mode, with
  an admin of the app, needs no review. Typical permissions: `pages_read_engagement`,
  `pages_show_list`, `read_insights`, and for Instagram `instagram_basic` and
  `instagram_manage_insights`. If Meta asks for review, submit those with a screen recording
  of the Integrations page.
- **Version.** `META_GRAPH_VERSION` (default v23.0). Meta retires old versions after about
  two years. If syncs start failing with version errors, raise it.
- **Not run against the real Page yet.** Optional insight metrics that Meta rejects become
  warnings, not failures.

## Constant Contact (v3)

- **Purpose.** Campaign statistics (sends, opens, clicks, bounces, opt-outs, forwards) and
  list size snapshots over time.
- **Auth.** OAuth at authz.constantcontact.com with scopes `campaign_data contact_data
  offline_access`. Setup: `CONSTANT_CONTACT_CLIENT_ID`, `CONSTANT_CONTACT_CLIENT_SECRET`,
  and the redirect URI `<APP_URL>/integrations/constant_contact/callback`.
- **Refresh.** Automatic. Constant Contact refresh tokens expire after long disuse, so if the
  connector is off for months, reconnect.
- **Not run against the real account yet.** The list and contacts request parameter names
  are the likeliest thing to need a one-line fix.

## Text In Church

- **Purpose.** Contacts, conversations, messages and connect cards. Wesley computes its own
  metrics (new contacts, reply times, volume) from the stored data.
- **Access.** The API must be switched on by Text In Church. Email
  support@textinchurch.com asking for API access to the church account. Until it is on, the
  Integrations page says Waiting for access. Set `TEXT_IN_CHURCH_MOCK=1` to see fake data
  while waiting; never leave it on in production.
- **Auth.** An API key pasted by an admin, or OAuth if Text In Church issues one.
- **Privacy.** Message text is stored only in `tic_messages`, readable only with the
  `data.text_in_church` permission, and every read is audited. It is excluded from raw
  payloads. A retention period for this data is still undecided (see "Open questions").
- **Webhooks.** Optional. Set `TEXT_IN_CHURCH_WEBHOOK_TOKEN` and point the webhook at
  `<APP_URL>/webhooks/text_in_church?token=<the token>`.
- **Not run against the real service yet.** The connect-card endpoint name is unverified.

## Subsplash

- **Finding.** No public analytics API for streaming viewership was found for Subsplash.
  Do not build on a guess. The church is leaving Subsplash in April.
- **What exists instead.** The streaming pane takes numbers three ways, through a common
  `StreamingSource` interface (`streaming_sources.py`): typed by hand, imported from a CSV
  (`Import CSV` on the Streaming page), or a Subsplash placeholder that reports "By hand or
  CSV". Resi or OBS can be added later as another source without touching the pane.
- **CSV columns.** `date, service, platform, peak_concurrent, total_views, watch_minutes`.
  Cells beginning with `=`, `+`, `-` or `@` are neutralized so a spreadsheet cannot run them.

## The streaming pane's rules

These exist so the numbers can be trusted. They are enforced in `streaming.py` and tested.

1. Everything is **views**, never people. The label is always "total views".
2. **Peak concurrent is never added across platforms.** The weekly figure is the highest
   single-platform peak.
3. Each number remembers its **source** (manual, CSV, API) and when it was updated. Per
   field, a person's entry beats a CSV, which beats the API. A later API sync does not
   overwrite a person's correction.
4. Every manual change is kept in an **edit history** (who, when, old and new value).
5. A week runs Monday to Sunday. Week over week is flagged "not like for like" when the set
   of platforms differs between the two weeks.

## Setting everything up on Railway

Variables, all optional until you connect that tool (names only; see `.env.example`):

`TOKEN_ENCRYPTION_KEY`, `PCO_CLIENT_ID`, `PCO_CLIENT_SECRET`, `PCO_WEBHOOK_SECRET`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `CONSTANT_CONTACT_CLIENT_ID`,
`CONSTANT_CONTACT_CLIENT_SECRET`, `TEXT_IN_CHURCH_WEBHOOK_TOKEN`, `META_GRAPH_VERSION`.

Generate `TOKEN_ENCRYPTION_KEY` with
`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
and store it somewhere safe as well as Railway. Losing it means reconnecting everything.

## Open questions

- How long to keep Text In Church message text and guest data.
- Whether to add the Google Workspace Calendar connector (deferred to Phase 3).
