# Phase 0 Audit: Wesley AI to Dalton First UMC Staff OS

Status: read-only audit. No application code was changed. This file is the only thing written.
Audited commit: `6473079` on `main` (working tree clean at start).
Audience: the next maintainer, then Mat. Written so someone who has never seen this app can follow it.

---

## 0. How to read this, and what I could not verify

**What I did:** read every Python module, route, model, migration, deploy file, scheduler job and the three docs in `docs/`; skimmed all templates and the three JS files; ran the test suite (513 passed in 88 s on Python 3.9.6); inspected the local SQLite file read-only.

**What I did not or could not verify. Treat these as open until you confirm:**

| Gap | Why | Consequence |
|---|---|---|
| Production data | Reading Railway Postgres needs `railway ssh` with a registered key, which is a side effect. The local `data/wesley.db` is a test copy (1 church, "Streaming Test Church", 1 user, 1 sermon, 0 documents). | Row counts in section 2 for production come from the 4 Sep 2026 migration runbook (699 rows, 21 tables, 4 churches), not from a fresh read. Phase 1 step 1 must re-count. |
| Which prod row is Dalton FUMC | Memory notes say church 1 in prod is a demo church ("Wesley AI Admin"), not DFUMC. | **Blocking question (Q1).** The live widget embed carries `data-church-id`, and that id must keep working. |
| Railway env vars | No dashboard access from here. | I list variable names from code. I cannot tell which are set, or whether `FLASK_ENV=production` is set (it controls the `Secure` cookie flag, see S-6). |
| Screenshots | I did not drive the UI (needs a login). | UI debt in section 7 uses file and line references and measurable counts instead. |
| Python version | Local is 3.9.6, README says 3.11+, Railway uses Nixpacks. | Tests passing on 3.9 is encouraging but not proof about the Railway runtime. |

### Where the brief and the code disagree

These change plans, so they come first.

1. **"Planning Center integration pulls the upcoming calendar" is not what the code does.** The calendar comes from a *public ICS feed URL* (`calendar_feed.py`), not the PCO API. The only PCO API code (`pco.py`, OAuth scope `people`) *writes* website guest sign-ups into PCO People. No Services, Groups, Check-ins, Calendar, Registrations, Publishing or Giving data is read anywhere today.
2. **"Communications dashboard routes requests to the right people and timelines them" is smaller than described.** It is an intake form plus a rule-based color triage (`comms_triage.py`) and an admin queue. There is no routing to named owners, no timeline or checklist, no link to calendar events. Phase 3's event-to-checklist engine is therefore new work, not an extension.
3. **The AI triage explanation has never run in production as far as the repo shows.** `routes/comms_routes.py` imports `anthropic`, but `anthropic` is not in `requirements.txt` and is not installed locally. The import sits inside a bare `except Exception`, so the explanation silently comes back blank.
4. **The sermon tool is further along than the brief says.** `packets.py`, `sermon_packet.py`, `sermon_longform.py` already generate quotes (hard-filtered to be verbatim), social posts, YouTube title/description/chapters, a blog post and a small group guide, with a Monday job and an editor. What is missing is the *review queue states* (approve, regenerate), the brand voice config at the level you described, and the manual-trigger parity check. See section 4.
5. **The staff chat has no tools and no live data.** It is retrieval-augmented generation over uploaded documents, curated Q&A, calendar, sermons and the denominational profile. It cannot "see" Planning Center, giving, or check-in data. Phase 5 is a ground-up capability.
6. **The public/staff boundary is enforced in code but only by a filter inside shared loaders, plus shared tables and a shared prompt builder** (section 5). It is better than "only in prompts", and weaker than the separation Phase 1b requires.

---

## 1. Architecture map

### Stack
- **Backend:** Python, Flask 3.1, Flask-Login (session cookies), Flask-SQLAlchemy 3.1 / SQLAlchemy 2.0, Flask-Migrate (Alembic).
- **Process model:** gunicorn, `WEB_CONCURRENCY` workers (default 1; prod runs 3 per the runbook) x 4 threads, `--timeout 120`. Started by `release.sh`.
- **Scheduler:** APScheduler `BackgroundScheduler` runs *inside every web worker*. Each job is wrapped by `scheduling.single_flight`, a Postgres advisory lock so only one worker runs it. On SQLite the lock is a no-op and `release.sh` refuses more than one worker.
- **Background work outside the scheduler:** raw `threading.Thread(daemon=True)` in request handlers for guest-to-PCO sync, crawl, sermon backfill and all outgoing email. Work is lost if the container restarts mid-thread (mitigated for guest sync and sermons by "stuck" recovery logic).
- **Frontend:** server-rendered Jinja templates, plain JavaScript (no build step, no framework). CDN scripts: `marked`, `dompurify`, `chart.js` (jsDelivr, unpinned), Google Fonts.
- **AI:** Google Gemini via `google-genai` (`gemini-2.5-flash-lite`, fallback `gemini-2.5-flash`; embeddings `gemini-embedding-001`, 768 dims). Claude Haiku via `anthropic` for comms triage (broken, see above).
- **Email:** Resend. **Billing:** Stripe. **Scraping:** `requests` + BeautifulSoup, Playwright optional.
- **Hosting:** Railway (Nixpacks build, project `lucid-acceptance`), auto-deploy from GitHub `main` (`matdaltonfirst/wesley-ai-saas`). Health check `/login`. Production DB is Railway Postgres since 4 Sep 2026; the old SQLite volume is retained as the rollback copy.
- **Uploads:** PDF/DOCX files on the container filesystem under `DATA_DIR/uploads/<church_id>/`. Not in Postgres. The runbook flags that if the Railway volume is not mounted at `DATA_DIR`, uploads vanish on redeploy (unverified, see Q9).

### Build and deploy
`git push origin main` (matnapp SSH key only) -> Railway builds with Nixpacks -> start command `./release.sh` -> if `DATABASE_URL` set, `flask db upgrade`, then `gunicorn app:app`. No CI. No staging environment. Tests are run by hand.

### Environment variables (names only)
Read from code. **Do not assume all are set in Railway.**

| Name | Used for |
|---|---|
| `SECRET_KEY` | Flask session signing. If unset, a random key is generated at boot and **all sessions die on every restart** (it only prints a warning). |
| `DATABASE_URL` | Postgres (Railway-injected). Absent means SQLite. |
| `DATA_DIR` | SQLite file and uploads root. |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL` | Chat, distillation, packets. First 8 chars of the key are logged at boot. |
| `EMBEDDINGS_ENABLED`, `EMBED_MODEL`, `EMBED_DIM`, `EMBED_SIMILARITY_FLOOR`, `EMBED_RELATIVE_BAND`, `EMBED_*_DENOMINATION` | Semantic retrieval tuning and kill switch. |
| `YOUTUBE_API_KEY` | YouTube Data API v3 (API key, not OAuth). |
| `PCO_CLIENT_ID`, `PCO_CLIENT_SECRET`, `PCO_TOKEN_ENCRYPTION_KEY` | PCO OAuth. If the encryption key is unset, tokens are encrypted with a key derived from the client secret. |
| `RESEND_API_KEY`, `FROM_EMAIL`, `SUPPORT_EMAIL` | Email. |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_MONTHLY_PRICE_ID`, `STRIPE_ANNUAL_PRICE_ID` | Billing (all CUT). |
| `ANTHROPIC_API_KEY` | Comms triage explanation (package not installed). |
| `APP_URL` | Absolute links in email, PCO redirect URI. Defaults to `https://app.wesleyai.co`. |
| `SUPER_ADMIN_EMAIL` | Defaults to `info@wesleyai.co`. Grants `/admin`. |
| `BILLING_EXEMPT_DOMAINS` | Defaults to `daltonfumc.com` plus a hardcoded `wesleyai.co`. |
| `DEFAULT_TIMEZONE`, `SESSION_COOKIE_SECURE`, `FLASK_ENV`, `FLASK_DEBUG`, `WEB_CONCURRENCY`, `PORT`, `WESLEY_SKIP_CREATE_ALL` | Runtime behavior. |

`.env.example` documents only 3 of these. Local `.env` holds real keys and is gitignored; I checked history and no key-shaped string has ever been committed.

### Third-party services
| Service | Used for | Disposition |
|---|---|---|
| Google Gemini | All generation and embeddings | KEEP (see Q7 on data terms) |
| YouTube Data API v3 + `youtube-transcript-api` (unofficial caption scraper) + Gemini "watch the video" fallback | Sermon discovery and transcripts | KEEP, REWORK (OAuth, Analytics) |
| Planning Center (OAuth, People write) and public ICS feed | Guest sync, calendar | REWORK |
| Resend | Email | KEEP |
| Stripe | Billing | CUT |
| Anthropic | Comms triage text | REWORK (decide one model vendor) |
| Railway | Hosting, Postgres | KEEP |

---

## 2. Data model

Declared in `models.py`; 26 tables; 3 Alembic revisions (`a01ccbb964a2` initial, `90e0b2579d19` transcript segments, `dbfa384bb56f` content profiles and packets) which together create all 26. Production had 699 rows across 21 tables on 4 Sep (before `usage_daily`, `knowledge_*`, `content_profiles`, `sermon_packets` and `embedding_cache` may have existed in prod, so recount).

**Tenant scoping is by a `church_id` integer column.** There is no row-level security, no schema-per-tenant, no middleware. Every query is hand-filtered with `church_id=current_user.church_id` (or an id from the request, for public routes). Child tables without their own `church_id` (`messages`, `widget_messages`, `sermon_packets` via sermon) inherit scope by joining to a parent. The only guard is the developer remembering the filter. Vector data is not namespaced because there is no vector store (see section 5).

| Table | Purpose | `church_id` | Disposition |
|---|---|---|---|
| `churches` | Tenant row: name, branding, denomination, local practice, statement of faith, timezone, **plus 24 billing columns** | is the key | REWORK to `organization` config; drop billing columns |
| `users` | Email + password hash + `role` (`admin`/`staff`) + reset token | FK | REWORK (Google sign-in, new roles) |
| `invites` | Staff invitation tokens | FK | CUT (replaced by role assignment) |
| `conversations`, `messages` | Staff chat history (auto-deleted after 14 days) | FK / via conv | KEEP, extend |
| `widget_conversations`, `widget_messages` | Public chat history (auto-deleted after 30 days) | FK / via conv | KEEP |
| `answer_feedback` | Visitor thumbs and auto-flagged low-confidence answers; staff corrections become Q&A | FK | KEEP |
| `guest_connections` | Name, email, phone, message from the widget, plus 11 PCO sync-state columns | FK | KEEP (PII, see S-9) |
| `documents` | Uploaded file metadata. `visibility`: `staff_only` (default) or `staff_and_chatbot` | FK | KEEP, REWORK boundary |
| `crawled_pages` | Scraped public website text. Unique on (`church_id`, `url`) | FK | KEEP (public by nature) |
| `text_snippets`, `qna_pairs` | Staff-written facts and approved answers. **No visibility column: everything here is shown to the public bot** | FK | REWORK (add audience) |
| `knowledge_pack_states`, `knowledge_checklist_states` | Onboarding "what should the bot know" checklist | FK | DEFER (useful as a content-gap checklist) |
| `church_calendars`, `calendar_events` | ICS feeds and expanded events (90 days) | FK | REWORK (PCO connector replaces) |
| `pco_connections` | PCO OAuth tokens (encrypted), auto-sync flag, workflow id. Unique per church | FK | REWORK into generic connector table |
| `sermon_sources`, `sermons` | YouTube channel (one per church) and ingested sermons with transcript, segments, summary | FK | KEEP, REWORK |
| `sermon_packets` | Generated content JSON per sermon. Unique on `sermon_id`. Status `pending/ready/failed` | FK | KEEP, REWORK (add review states) |
| `content_profiles` | Per-church voice notes, title strategy, platforms, hashtags, CTA | FK unique | KEEP (this is the "brand voice config", already editable) |
| `comms_requests` | Comms intake with triage color, tier, estimate | FK | KEEP, REWORK |
| `embedding_cache` | Text hash -> vector cache. No tenant data by design | none | KEEP |
| `usage_daily` | Per-church, per-surface Gemini token counts | FK | KEEP (drop the per-tenant framing) |
| `system_prompts` | One row (id=1): the "platform prompt", super-admin editable | none | REWORK (see 5) |

**Dead or dangerous schema:** `Church.plan` (`founders/small/medium/large`), `trial_*`, `stripe_*`, `manual_payment_*`, `warning_*_sent`, `billing_exempt`, `onboarding_complete`, `comms_enabled`, `denomination_profile_version`. `Church.is_active` silently returns True when `trial_ends_at` is NULL "as a safety net against accidental lockout", which is a billing bypass by design.

---

## 3. Tenant touchpoints (complete list)

Search basis: every `church_id`, `current_user.church`, `Church.query`, `User.church`, `has_active_access`, `billing`, `stripe`, `trial`, `plan` reference.

**Auth and identity**
- `routes/auth.py`: open `/signup` creates a new Church + admin with a 14-day trial; no email verification. `/api/invite/accept` creates `role="staff"` users into an existing church.
- `helpers.is_super_admin()`: a string compare of `current_user.email` to `SUPER_ADMIN_EMAIL`. A *platform* super-admin concept that has no meaning for one org.
- `models.User.church_id` (non-null FK); `load_user` has no tenant check (not needed, user row carries it).

**Routes (every one filters by `current_user.church_id`)**
`chat.py`, `widget.py` (staff-side analytics, feedback, guests, snippets, Q&A), `documents_routes.py`, `settings.py`, `sermons_routes.py`, `packets_routes.py`, `comms_routes.py`, `calendars.py`, `pco_routes.py`, `knowledge_packs.py`, `pages.py`. `admin.py` and `stripe_routes.py` are the cross-tenant operator surface.

**Public routes keyed by an attacker-visible integer**
`/api/widget/branding?church_id=`, `/api/widget/chat`, `/api/widget/chat/stream`, `/api/widget/feedback`, `/api/guest-connection` all take `church_id` in the request. `widget-core.js` reads it from the embed script's `data-church-id`. **This is a live contract with the DFUMC website (Q1, section 11).**

**Queries and retrieval**
`documents.load_*` (4 loaders), `calendar_feed.load_calendar_chunks`, `sermons.load_sermon_chunks`, `helpers.build_system_prompt` (QnA, snippets), `digest.py`, `usage.py`, `packets.py` all take or filter `church_id`.

**Prompts**
- `config.DEFAULT_SYSTEM_PROMPT` and `helpers._STAFF_SYSTEM_PROMPT` and `_WESLEY_CORE` are written for "a local church" generically. `_platform_prompt_for()` exists *only* to stop one tenant's denominational wording leaking into another's prompt.
- `denominations/` is a five-profile registry (UMC, GMC, SBC, non-denominational, custom) with a per-church selector, a "foreign denomination text" detector and a matrix. All of this machinery exists for multi-tenancy.

**Embeddings:** `embedding_cache` is deliberately tenant-free. No leak. `warm_chunks` iterates all churches nightly.

**File storage:** `UPLOADS_DIR/<church_id>/<uuid>.<ext>`.

**Billing:** `Church` billing columns, `helpers.get_billing_status/has_active_access/require_active/is_billing_exempt`, `routes/stripe_routes.py`, `routes/admin.py` billing panel, `templates/subscribe.html, stripe_success.html, admin_billing.html`, 6 email templates, jobs `trial_reminder_job`, `manual_billing_check_job`, and gates on staff chat, widget chat, `/` and `/dashboard`.

**Onboarding:** `pages.onboarding_page`, `/api/onboarding/step1`, `templates/onboarding.html` (715 lines), `Church.onboarding_complete`.

**Admin screens:** `/admin` (platform prompt, church list, token counts, temp-password reset), `/admin/billing`.

**Emails:** `emails.py` and 11 templates carry "Wesley AI" branding and `wesleyai.co` addresses (14 hardcoded references). Recipients are "first user" or "all admins of the church".

**Analytics and config:** `usage_daily` per tenant; `config.py` holds `APP_URL`, `FROM_EMAIL`, `SUPPORT_EMAIL` pointing at wesleyai.co; the dashboard hardcodes "Wesley" in 129 template/JS places.

**Tests:** `test_billing_gates.py`, `test_onboarding.py`, much of `test_auth.py` and `test_denominations.py` encode multi-tenant behavior and will change or be deleted.

---

## 4. Feature inventory

Single-organization fit is the test. K = KEEP, R = REWORK, C = CUT, D = DEFER.

### User-facing

| Feature | Verdict | Reason |
|---|---|---|
| Staff AI chat (`/`, `/api/chat[/stream]`) | **R** | Core value. Needs identity, roles, tools, audit log, data-domain permissions. Currently document RAG only. |
| Public widget chat + `widget-core.js` | **K/R** | Must survive untouched in behavior; move to its own retrieval path (Phase 1b). |
| Feedback and corrections inbox | **K** | Good loop: low-confidence answers become approved Q&A. |
| Guest connections (+ PCO People sync) | **K/R** | Real ministry value. Make PCO sync a connector; guest data is PII. |
| Chat logs, analytics (3 panels) | **K/R** | Useful; the topic categorizer is crude keyword matching. |
| Documents (PDF/DOCX), snippets, Q&A, website crawl | **K/R** | The knowledge base. Add audience (staff/public) per item. |
| Knowledge guide (packs and checklists) | **D** | Written to onboard strangers. Reuse later as a content-gap checklist for DFUMC. |
| Calendars (ICS) | **R** | Replace with PCO Calendar connector; keep ICS as a fallback adapter. |
| Sermons (YouTube) and Sunday Content (packets) | **K/R** | Strongest existing asset, see below. |
| Comms requests (form, triage, my requests, admin queue) | **K/R** | Becomes the home for the Phase 3 checklist engine. Triage logic is 25 lines of rules; the AI explanation is broken. |
| Denominational profile and local practices | **R** | Collapse to one UMC profile plus DFUMC local practice. Keep the *content*, delete the selector and the 4 other profiles' wiring. |
| Settings dashboard (`settings.html`, 2,743 lines) | **R** | Becomes role-based home and module pages. |
| Team management and invites | **C** | Replaced by Google sign-in and role assignment. |
| Playground, embed code panel | **K** | Small, still needed. |
| Subscribe, Stripe checkout, billing portal, trial and manual billing, billing admin | **C** | Single org, no billing. |
| Signup, onboarding wizard, forgot/reset password | **C** | Gone with password auth and multi-tenancy. |
| Super-admin panel (`/admin`: platform prompt, church list, token counts) | **C**, keep the prompt editor as a role-gated admin screen | |
| Super-admin "reset a church admin's password" (returns the temp password in JSON) | **C** | |
| ProPresenter | none found | I grepped templates, JS, Python and docs. **No ProPresenter code exists in this repo.** (There is a `propresenter-sermon-builder` skill in your Claude setup, outside this repo.) Nothing to cut. |

### Background jobs (13, all in `app.py`)

| Job | Schedule (server tz) | Verdict |
|---|---|---|
| `nightly_crawl` | 02:00 | K |
| `transcript_backfill` | 02:30 | K |
| `embedding_warm` | 02:45 | K/R (see 5; check it ran, prior prod found the cache empty) |
| `nightly_cleanup` (staff chats > 14 d) | 03:00 | R (conflicts with audit log retention) |
| `nightly_widget_cleanup` (> 30 d) | 03:30 | K |
| `invite_cleanup` | 04:00 | C |
| `manual_billing_check` | 08:00 | C |
| `trial_reminder` | 09:00 | C |
| `calendar_refresh` | 01:30 | R |
| `sermon_check` | 04:30 | K |
| `monday_packet` | Mon 11:00 | K/R |
| `weekly_digest` (widget stats to church admins) | Mon 13:00 | K/R, folds into Phase 3 attention alerts |
| `pco_reconciliation` | every 5 min | K/R |

APScheduler uses the server's timezone (likely UTC on Railway); comments in `app.py` say "early morning US" for 13:00, so the intent is UTC. Not verified.

### Sermon content engine, against the Phase 3 spec

Already built: newest-video detection (skips unaired livestreams), caption fetch with timings, Gemini-watches-video fallback, distillation, quotes (hard-verified as verbatim and fluent), social posts, YouTube titles/description/chapters/tags, blog, small group guide, editable fields (quotes and chapters deliberately not editable), Monday email, dashboard panel, catch-up logic, per-church voice and title-strategy config (`ContentProfile`, `content/strategies.py`, incl. `question_caps`).
Missing against spec: explicit review-queue states (approve / reject / regenerate per piece), thumbnail direction output (verify), a visible "never auto-publish" status model, and a manual trigger parity test. Nothing publishes automatically today, which is correct.

---

## 5. Knowledge base, dataset, and the public/staff boundary

### Where content lives and how it is indexed
- **Documents:** files on disk plus a `documents` row. Parsed at request time into chunks (PDF: one chunk per page; DOCX: heading-aware ~500 char chunks), cached in an in-process dict (200 entries). Not stored in the database, not shared between workers.
- **Web pages:** `crawled_pages.content`, up to 200 pages per crawl, depth 3. Whole page = one chunk (no splitting).
- **Curated:** `qna_pairs`, `text_snippets` (snippets capped at 1,000 chars).
- **Calendar:** next 45 days, max 25 events. **Sermons:** latest 8, summary and main points only (not the full transcript).
- **Denominational:** reviewed sections in `denominations/umc.py` (version 2024.1), compiled into code.
- **Ranking:** semantic (cosine over cached vectors, brute force in Python over chunks loaded for that request) when the church's *entire* corpus is embedded, else keyword scoring. There is **no vector store and no index**; every chat request loads and scores a church's whole corpus in the web process. Fine at one church's scale, worth knowing.

### How the public and staff paths differ today
| | Staff (`routes/chat.py`) | Public (`routes/widget.py`) |
|---|---|---|
| Documents | all | only `visibility == "staff_and_chatbot"` (`load_chatbot_documents`) |
| Crawled web pages | **not included** | included |
| Q&A and snippets | in prompt and as chunks | in prompt and as chunks (same rows) |
| Calendar, sermons, denomination | yes | yes (same loaders) |
| Prompt | `_STAFF_SYSTEM_PROMPT` | `_PUBLIC_IDENTITY_PREFIX` + platform prompt + public addendum |
| Tools / function calling | none (`automatic_function_calling` disabled) | none |
| Auth | login | none; `church_id` from the request; CORS `*` |

### Blunt assessment
**What is genuinely good:** the public bot has no tools and no database access of its own. A prompt injection cannot make it call anything, because there is nothing to call. Documents default to `staff_only` and the filter is in the SQL query, not in the prompt. The core rules forbid revealing instructions.

**What is weak, in order of importance:**
1. **One table, one process, one shared loader set.** "Public" is a `WHERE visibility=` clause on a table that also holds staff-only files, queried by the same code that serves staff. A future edit (a new loader, a joined query, a cache keyed without visibility) can leak. There is no structural separation, and no test that proves it. This is exactly what Phase 1b must fix.
2. **Q&A and snippets have no audience flag.** Anything staff type into them is injected into the *public* system prompt and retrievable by anyone who can phrase the question. The UI labels them "Additional Church Information", but nothing stops "staff only: the alarm code is..." from being saved. For Staff OS, where staff will want to store internal facts, this is the most likely accidental leak.
3. **The document chunk cache is keyed by `(doc_id, uploaded_at)` and holds parsed text for both audiences in one dict.** It is safe today because loaders choose which docs to read, but it is the kind of shared structure that breaks silently.
4. **The calendar feed is public by assumption.** Whatever the ICS contains goes to the public bot, including descriptions. Events marked `CLASS:PRIVATE/CONFIDENTIAL` are skipped, but PCO's ICS export does not reliably set `CLASS`, so internal events (staff meetings, counseling room bookings) in the feed would be public. Needs a separate public calendar or an explicit allow-list.
5. **No audit trail** for either surface beyond usage counters.
6. **Prompt injection through retrieved content** (crawled pages, calendar descriptions, sermon text, uploaded docs) is possible but low impact today because there are no tools. It becomes high impact the moment the *staff* AI gets tools (Phase 5): untrusted retrieved text will sit next to authority to call them.
7. Conversation history for public sessions is replayed from the database keyed by a client-supplied `session_id`. 128-bit random ids make this safe; the weakness is only that the client may pick the id, so keep it opaque and server-issued only.

### Dataset preservation
What would be lost if mishandled: Q&A and snippets (curated, staff-written), uploaded documents (on filesystem, not in DB, see Q9), crawled pages (regenerable), sermon records and transcripts (YouTube captions are flaky, so transcripts are expensive to regenerate), `answer_feedback` corrections, `guest_connections`, packets with staff edits. Vectors are regenerable (about 562 vectors in 15 s per the runbook). Conversations are intentionally ephemeral.

---

## 6. Existing integrations

### Planning Center
- **Auth:** OAuth 2, scope `people` only, per-church connection, tokens Fernet-encrypted at rest, refresh under a per-process lock (not cross-worker; with 3 workers two can refresh at once, and PCO refresh tokens rotate, so a lost race can invalidate the connection).
- **Does:** on a widget guest sign-up, find person by email, else create; add email, phone, a note in category "Wesley AI", and optionally a workflow card. Resumable, per-step flags, retries at 5 min / 30 min / 2 h, max 4 attempts, reconciliation job every 5 min.
- **Does not do:** read anything. No webhooks. No Calendar, Services, Groups, Check-Ins, Publishing, Registrations or Giving scopes.
- **Note:** this is a *write* to the system of record triggered by an unauthenticated visitor. It is rate limited (5 per IP per hour) and visitor-initiated, but under your Phase 5 rule ("any write is draft and approve") it needs a conscious exception or a human step. See Q8.
- **Existing PCO assets I noticed in your session:** the Planning Center MCP connector in this Claude environment exposes read tools for all the domains you want. That is useful for *prototyping queries and learning the data shape*, but the app itself needs its own OAuth/API client (a Claude connector is not a runtime dependency of a Railway service).

### Calendar
Public ICS URL, fetched nightly (and when added), expanded 90 days, stored as wall-clock times, one calendar per row. SSRF check resolves DNS but `requests.get` follows redirects, so a feed that redirects to an internal address would bypass the check (low risk, admin-only input).

### YouTube
- **Access today:** one server-side **API key** for the Data API v3: `channels`, `playlistItems`, `videos` (`liveStreamingDetails`, `status`). Public data only.
- **Transcripts:** `youtube-transcript-api` (unofficial scraper, pinned `>=1.2,<1.3`), which is why a Gemini video-URL fallback exists. This already broke once in production (sermons went untranscribed).
- **Missing for Phase 2:** OAuth for the channel owner (needed for the Analytics API: views, watch time, concurrent peaks by live stream). An API key cannot read analytics.

### Not present at all
Facebook/Instagram (Meta Graph), Constant Contact, Text In Church, Subsplash, Google Workspace/Calendar, Giving, Check-ins.

---

## 7. UI debt

Measured, with file references. No screenshots (see section 0).

1. **One 2,743-line template runs the whole management UI.** `templates/settings.html` holds 18 panels switched by `data-panel` buttons (`:1294` to `:1473`), 5 inline `<script>` blocks, a 1,000-line `<style>` block and 67 inline `style=` attributes. The matching `static/dashboard.js` is 2,052 lines with no module structure.
2. **Two overlapping design systems.** `static/style.css` (976 lines) and `static/brand.css` (532 lines) both define components; recent commits (`fef137f`, `eea324a`) were migrating toward brand.css but each page still ships its own `<style>` block (all 7 main templates have one).
3. **Accessibility is thin.** In `settings.html`: 3 `aria-` attributes and 1 `role` across 2,743 lines; 16 `<label>` for 30 form controls; the panel nav is `<button data-panel>` with no `role="tab"`/`aria-selected`/`aria-controls` and no focus management on panel switch. `templates/partials/head.html` has no `lang`, `auth.html` has it once. Focus rings exist (`:focus-visible` in both CSS files) which is good. No `prefers-color-scheme`. Reduced-motion is handled in brand.css only. Contrast has not been measured; the dark teal sidebar with `rgba(255,255,255,0.28)` external-link icons (`settings.html:219`) will fail.
4. **Mobile:** 3 `@media` rules total across both stylesheets. The settings sidebar layout is desktop-first. Needs a real check at 375 px.
5. **Unsafe-by-construction DOM building:** 105 `innerHTML` uses across JS and templates. `dashboard.js` has an `esc()` helper and uses it in the places I sampled, and the staff chat renders through DOMPurify. This is a pattern to audit rather than a confirmed bug, but every new screen will copy it.
6. **External scripts are unpinned and unhashed** (`marked`, `dompurify`, `chart.js` from jsDelivr with no version or SRI). A compromised CDN file runs with a staff session.
7. **Navigation exposes everything to everyone with the admin role.** There is no concept of "what does this person need first". The chat is the home screen and the product.
8. **Branding is baked in:** "Wesley" appears 129 times in templates/JS, plus logo `WesleyAI.png`.
9. **Inline `onclick` handlers** in `onboarding.html` and `comms/admin.html` (blocks a strict CSP).

---

## 8. Security review

Ranked within each group. **S-1 and S-2 are urgent whatever else we decide, because they are live now.**

### Auth and access
- **S-1 (High): Open public signup with no email verification.** `POST /api/auth/signup` lets anyone on the internet create a church and admin account, which immediately gets Gemini-backed chat (14-day trial) at your cost. Anyone who signs up as `anything@daltonfumc.com` is also treated as *billing exempt* (`is_billing_exempt` trusts the unverified email string). Rate limited to 10 per 15 minutes per IP, in memory, per worker. This is a live cost and abuse exposure today. Cheapest immediate mitigation (not applied, this is read-only): disable the signup route behind an env flag. Q2.
- **S-2 (High): No cross-site request forgery protection on most state-changing endpoints.** `validate_csrf_json` is called on 5 auth endpoints and 3 packet endpoints only. Documents, staff removal, settings, snippets, Q&A, PCO disconnect, admin password reset, billing admin, widget feedback correction, comms status changes, and the rest have none. Practical risk is reduced by `SESSION_COOKIE_SAMESITE=Lax` and by `request.get_json()` rejecting non-JSON bodies, which together block the usual forged-form attack. It is still one browser quirk or one `text/plain` JSON-parsing change away from exploitable, and Staff OS endpoints will be more dangerous (send, publish, giving).
- **S-3 (Medium): Passwords only, no MFA, no email verification, pbkdf2:sha256 with default iterations, minimum 8 chars, no breach or complexity check.** Reset tokens are stored in plaintext in `users.reset_token` (1-hour expiry, single use). Replaced wholesale by Google Workspace sign-in in Phase 1b.
- **S-4 (Medium): Super admin = string equality on email.** Combined with S-1, anyone who controls email at the `SUPER_ADMIN_EMAIL` address (or a mail provider that lets someone claim it) owns the platform. The admin "reset password" endpoint returns the new password in a JSON response body.
- **S-5 (Medium): Role model is two flat roles** (`admin`, `staff`). Many endpoints check only `login_required`, so any staff user can read all widget conversations (visitor questions and, via guests, names, emails, phones), edit Q&A that goes public, upload public-visible documents and trigger crawls. Nothing is gated by data domain.
- **S-6 (Medium): `Secure` cookie flag depends on `FLASK_ENV == "production"`.** If Railway does not set that variable the session cookie is sent without `Secure`. Unverified (Q3). Sessions are browser-session cookies with no server-side expiry and no idle timeout.
- **S-7 (Low): `SECRET_KEY` fallback generates a random key** and only prints a warning, so a missing variable looks like "everyone got logged out at every deploy" rather than a hard failure.

### Secrets and data handling
- Local `.env` has real keys but is gitignored, never in history (checked). Good.
- The first 8 characters of `GEMINI_API_KEY` are logged at every boot (`app.py:450`). Remove.
- PCO tokens are encrypted at rest with Fernet; the key may be derived from the client secret if `PCO_TOKEN_ENCRYPTION_KEY` is unset, so rotating the OAuth secret can orphan stored tokens.
- **S-8 (Medium): Retention is inconsistent and undocumented.** Staff chats deleted after 14 days, public chats after 30, but `guest_connections` (names, emails, phones, free text) are kept forever and also copied to PCO, and `answer_feedback` keeps the visitor's question text indefinitely via its message. No privacy statement was found in the widget.
- **S-9 (Medium): The AI now has a longer data horizon than the retention rules.** When giving or check-in data reaches the staff AI, the audit log you asked for must outlive the 14-day chat cleanup, so the cleanup job needs to be redesigned, not just extended.

### Input validation and injection
- File upload checks extension and magic bytes (good), stores under a UUID name, display name through `secure_filename`. No virus scanning, no page or size limit beyond 32 MB, `pdfplumber` parses untrusted PDFs in the web process.
- Widget input: 2,000 char cap, 64 char session id, type checks. SQL is through the ORM except fixed raw SQL in `admin.py`. Jinja autoescape is on.
- **S-10 (Medium): Public endpoint cost and abuse controls are weak.** CORS `*` on every public endpoint, so any website can embed your bot and spend your Gemini budget; the limiter is per-IP, in memory, per worker (3 workers = 3x the limit, and it resets on deploy); `church_id` is a guessable integer. No bot challenge, no per-day token cap per IP, no kill switch except `EMBEDDINGS_ENABLED` (which does not affect chat). `GUEST_LIMITER` is better (5 per hour per IP).
- Prompt injection on the public bot: see section 5. No tools means impact is limited to what is in the prompt (Q&A, snippets, local practice, statement of faith, retrieved chunks). The system prompt is not secret, but the *Q&A and snippets* inside it are effectively public.
- SSRF: guarded for website URL and ICS URL (resolve and reject private ranges), not for redirects.

### Logging
Logs include church ids, guest ids, sermon titles and exception text. They do not include message bodies (good). Gemini error strings are returned to the client verbatim in the fallback branch of `friendly_gemini_error` (`f"AI error: {exc}"`), which can leak internal detail.

---

## 9. Tests and observability

**Tests:** 513 tests, all passing, ~88 s. In-memory SQLite with `StaticPool`; CSRF, SSRF and rate limits are bypassed under `TESTING`. Gemini is faked at the function level. Coverage is strong for retrieval/citations, sermons, packets, denominations, PCO sync, widget, and the SQLite-to-Postgres copier. Gaps: **no test hits Postgres** (so advisory locks, `flask db upgrade`, and sequence behavior are untested in CI; none exists); **no prompt-injection or exfiltration test**; **no cross-tenant access test**; **no test for the staff/public document boundary beyond happy-path visibility**; no front-end tests; no CSRF test because it is disabled under test; `tests/test_auth.py` tests password flows that will be deleted.

**CI:** none. No lint, no formatter. 265 SQLAlchemy `Query.get()` deprecation warnings.

**Observability:** Python `logging` to stdout, read through `railway logs`. No error tracker, no metrics, no request ids, no structured logs, no alerting, no uptime check beyond Railway's `/login` health probe. Scheduler jobs log a line but record nothing queryable: **if the 02:45 embedding job silently does nothing, the only evidence is a log line** (this actually happened: the cache was empty at migration time and nobody noticed). `usage_daily` is the only metrics table. A `sync_runs` table (Phase 2) fixes this class of problem and should be built early.

**Docs:** `README.md` describes the multi-tenant SaaS and is stale for the new purpose; `docs/` has denominational architecture, scaling notes and the (executed) Postgres runbook. No architecture diagram, no runbook for failures.

---

## 10. Risks and open questions for you, ranked

### Blocking (I need an answer before Phase 1)

**Q1. Which church row is Dalton First UMC in production, and what `data-church-id` does the live website embed use?**
My notes say prod church 1 is a demo. The runbook says four churches existed. The public widget on the real site must keep working through the conversion. *Proposal:* keep accepting `church_id` on public endpoints for compatibility, validate it equals the single organization's id (or a configured legacy alias), return the same errors, and leave the site's embed code alone. If the DFUMC row is not id 1, tell me its id and I will map it.

**Q2. May I disable public signup now, ahead of everything else?**
S-1 is a live cost and abuse hole and is a one-line, reversible change behind an env flag. It is a code change so I did not make it. Your call whether it waits for Phase 1.

**Q3. Is `FLASK_ENV=production` set on Railway, and is a volume mounted at `DATA_DIR`?**
These decide whether session cookies are `Secure` and whether uploaded documents (part of the dataset you told me not to lose) survive a deploy. Please check the Railway dashboard, or approve me to check read-only with the Railway CLI.

**Q4. What happens to the other tenant churches' data?**
Three or more non-DFUMC churches exist in prod (the 4 Sep migration counted four). Options: (a) export and archive them to a dated file, then delete from the live database; (b) archive in place in a `_archive` schema, unreachable by the app; (c) hand data back to those churches. This is a privacy and obligations question, not just a technical one. Have those churches been told the chatbot is ending? Their conversations and guest contact lists are in the database.

### High

**Q5. Roles in practice.** Who maps to each role today? You named five people; confirm the Google Workspace accounts, and whether the Senior Pastor is Admin, Pastoral, or both. Also confirm the default for *Family Ministries* (check-in aggregates only?) and *Music* (Services only).

**Q6. Giving data: do you want it in Phase 5 at all?** I recommend building the Giving connector last, behind its own permission, and excluding it from the AI until the permission and audit log have been tested for a few weeks. Pastoral care notes, background checks and child names stay out, as you specified.

**Q7. Gemini data terms.** Staff chat sends church documents (and, soon, operational data) to Gemini. Is the API key on a paid, data-protected tier (Google does not train on paid-tier API data; the free tier is different)? Needed before any sensitive data domain is exposed to the staff AI.

**Q8. Are guest-to-PCO writes an acceptable standing exception to "draft and approve only"?** Today a visitor's form submission creates a person and a note in PCO People automatically. Options: keep (visitor-initiated, narrow, rate limited), or change to "queue for staff approval" (adds friction to follow-up). I recommend keeping, documenting it in `DECISIONS.md`, and logging it.

**Q9. Confirm the uploads path in prod** (same as Q3, restated because it is data loss risk, not just configuration).

### Medium

- **R1.** Single maintainer, no CI, no staging, deploys go straight to production from `main`. Phase 1 should add a staging environment (a second Railway service on the same repo) before touching the live chatbot. *Mitigation: Phase 1 begins with CI and a staging service.*
- **R2.** Scheduler runs in web workers. With the Phase 3 and Phase 2 jobs (syncs every few minutes, webhooks, weekly generation) a long job can starve request threads. Recommend a separate worker process (same code, `python -m jobs`) before connectors land.
- **R3.** YouTube transcripts rely on an unofficial scraper that has already broken once. With OAuth to the channel owner account, the official captions API can be used (needs the channel's own authorization) as a more reliable primary.
- **R4.** Subsplash has no public analytics API that I know of and you are leaving in April. The adapter plan in your brief is right. I have not yet investigated Subsplash exports; that is a Phase 2 task.
- **R5.** Meta (Facebook/Instagram) Page insights and live video views require an approved Meta app and long-lived token handling; app review can take weeks. Start the application process early. I will document it in `INTEGRATIONS.md`.
- **R6.** Text In Church API access needs an email from you to support first (as you noted). Build behind a flag with a mock adapter as you specified.
- **R7.** I will not rely on the Claude Planning Center connector for production data access; it exists in this environment but is not something a Railway service can call.
- **R8.** Calendar privacy (section 5, weakness 4): decide which calendars are public, as it directly affects the public bot.
- **R9.** The denominational code is 1,443 lines of multi-tenant machinery whose *content* (UMC facts, current as of General Conference 2024) is valuable. Cutting must keep the content and the review process for it.

### Low
Unpinned CDN scripts; `Query.get()` deprecations; stale README; `Church.is_active` returns True for null trial (moot after billing removal); misleading "AI error" passthrough message; unused `Procfile` and `railway.toml` duplication.

---

## 11. Proposed migration plan: removing multi-tenancy, preserving data, reversible

Guiding rule from the brief: no destructive step without a verified backup and a written rollback. Everything below is additive until the final, separately approved cleanup.

### Phase 1 exit criteria (stated now so you can veto them)
1. A restore of the pre-migration backup into a scratch Postgres has been performed and row counts match the live database (proved with a script and its output saved in `docs/`).
2. Before and after transcripts of at least 25 fixed test questions against the public widget (service times, nursery, events this week, sermon, giving, plus 5 hostile prompts) are saved and compared. Answers are materially equivalent; hostile prompts leak nothing.
3. DFUMC data is complete in the single-org schema: counts per table equal the source for the DFUMC `church_id`. Document files re-hash to the same SHA-256.
4. No code path references `church_id`, `Church`, billing, Stripe, signup, invites or tenant admin. `git grep` proves it.
5. App boots with only `.env.example` values plus a seed script; `pytest` is green; CI runs it on every push.
6. Rollback tested once on staging.

### Steps

**Step 0, before any change (no risk).**
Create staging (second Railway service plus its own Postgres), a CI workflow (pytest on 3.11 against Postgres), and a branch. Decide Q1 to Q4.

**Step 1, backup that has been restored.**
`pg_dump` of production (custom format) plus a tarball of `DATA_DIR/uploads` with a SHA-256 manifest, copied off Railway. Restore both into a scratch database and compare counts and file hashes. Write `docs/RUNBOOK.md#backup-and-restore`. This reuses the checksum-then-open discipline already recorded in `docs/postgres-migration-runbook.md`.

**Step 2, introduce `organization` alongside `churches` (additive).**
New `organization` table (one row): name, city, timezone, website URL, branding (bot name, welcome, color, subtitle, starters), denomination key, local practices, statement of faith, feature flags (JSON), integration settings. Populate from the DFUMC row. Nothing reads it yet. Alembic revision, reversible `downgrade()`.

**Step 3, make the code read `organization`, behind a flag.**
A tiny `current_org()` helper returns the single row. Public endpoints keep accepting `church_id` (validated against the org's id or alias) so the live embed is untouched. Behavior is identical; the flag lets us flip back instantly. Run the before/after transcripts here.

**Step 4, stop writing and reading tenant columns.**
Remove `church_id` filters route by route, with tests per route. In the same step, delete billing, signup, onboarding, invites, tenant admin and their jobs and templates. Write `docs/REMOVED.md` listing each removal and why (your request: "keep a short note of what was removed and why").

**Step 5, archive the other tenants.**
Per Q4's answer. Default recommendation: export their rows and files to an encrypted archive stored outside the app, record its checksum, then delete from the live database in a separate, explicitly approved migration.

**Step 6, drop the dead schema (separate approval, point of no return).**
Drop `church_id` columns and FKs, drop `churches` billing columns, drop `invites`, `knowledge_*` if cut, rename `churches` out. Done only after at least a week of clean operation on the flag, and after a fresh verified backup. Each migration has a tested `downgrade()` that restores columns from the archive.

### Rollback, by step
- Steps 2 to 3: turn the flag off. Data untouched. Zero downtime.
- Step 4: revert the deploy to the previous git tag (Railway redeploy). Data untouched because columns still exist.
- Step 5: restore from the archive file (tested in Step 1's procedure).
- Step 6: `flask db downgrade` to the prior revision, then restore columns from the Step 1 backup. This is the only step that needs the backup; therefore it is last and gated.

### What stays the same through Phase 1
The widget script, its API paths, its CORS behavior, its response shapes, and `data-church-id`. The staff chat URL (`/`). The scheduler and its advisory lock.

---

## 12. Suggested order of work after approval (for your judgment, not a commitment)

0. Staging, CI, signup flag (small, protective).
1. Phase 1 as above.
1b. Google sign-in and the role matrix **before** adding any connector, so every later feature is born permission-aware. Public/staff storage split in the same phase with the injection test suite.
2. `sync_runs` table, connector base class, Integrations status page. Then, in your order: PCO read, YouTube OAuth, Meta, Constant Contact, Text In Church, streaming pane with CSV/manual entry first (it is the administrative assistant's hero feature, and needs no external API to ship a correct v1; I suggest *this* ships first within Phase 2 so she has a reason to log in early).
3 to 5 as written.

Demo thought for the Senior Pastor: the fastest convincing demo is probably the weekly stream pane (real numbers, boring and correct), the Sunday Content review queue (already close), and one cross-tool question answered with citations. That argues for pulling a thin slice of the streaming pane and review queue forward, as a Phase 1.5, once the single-org conversion is verified.

---

## Questions needing an answer to start Phase 1
Q1 (which church row and embed id), Q2 (disable signup now?), Q3/Q9 (production cookie and uploads volume), Q4 (other tenants' data). Q5 to Q8 can wait for Phase 1b.
