# Wesley: Staff OS for Dalton First United Methodist Church

An internal tool for the staff of one church, plus the public chatbot on the church website.

It began as Wesley AI, a multi-tenant product for many churches. The chatbot product was
retired for other churches and the codebase now serves exactly one organization.
See [docs/AUDIT.md](docs/AUDIT.md) for how it got here and [docs/REMOVED.md](docs/REMOVED.md)
for what was taken out.

## What it does today

- **Staff AI chat** that answers from the church's own documents, approved Q&A, calendar, sermons and the United Methodist profile.
- **Public website chatbot** (the embeddable widget) for members and visitors, with a smaller, public-only knowledge set.
- **Sunday Content**: pulls the latest sermon from YouTube and drafts social posts, quotes, YouTube metadata, a blog post and a small group guide for staff to review.
- **Communications requests** with rule-based triage.
- **Guest connections** from the chatbot, synced to Planning Center People.
- **Calendar** from an ICS feed, **website crawl**, **knowledge base** (documents, snippets, Q&A).

Sign-in is limited to `@daltonfumc.com` email addresses. There is no public signup and no billing.

## Run it locally

Requires Python 3.11+ (3.9 works for the tests).

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set SECRET_KEY; add GEMINI_API_KEY for AI answers
python scripts/seed_dev.py      # fake church data; refuses to touch a real database
python app.py                   # http://localhost:5001
```

The seed script prints which accounts it created. Their password is `SEED_PASSWORD`, or the
throwaway default documented at the top of `scripts/seed_dev.py`.

Everything optional (Planning Center, YouTube, email) switches on when its variables are set
and otherwise shows "not enabled on this server". See `.env.example` for every setting.

## Test

```bash
python -m pytest tests/
```

No test calls the live AI: a fixture fails any test that tries.

## Deploy

Railway builds from GitHub `main` and runs `./release.sh`, which applies database migrations
(PostgreSQL) and starts gunicorn. The one-time conversion from the multi-tenant database has
its own procedure and rollback in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Layout

```
app.py              application factory, scheduler and its jobs
organization.py     the one Organization row; everything per-church comes from here
config.py           environment-driven constants
models.py           database models
helpers.py          prompt assembly, Gemini calls, CSRF, time helpers
routes/             Flask blueprints (chat, widget, settings, sermons, comms, pco, ...)
denominations/      the Wesleyan United Methodist profile and local-practice rules
content/            house-style settings for generated content
migrations/         Alembic revisions
scripts/            backup, restore, restore check, local seed
docs/               audit, architecture, integrations, runbook, decisions, removed features
```

More: [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DECISIONS.md](docs/DECISIONS.md).
