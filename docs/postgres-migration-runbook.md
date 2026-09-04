# PostgreSQL migration runbook

The code is ready. This is the sequence to run, and it is written so that
nothing before step 6 touches the live app: the current SQLite deployment keeps
serving throughout, and every step before the cutover is reversible by doing
nothing.

**Not yet verified against a real PostgreSQL server.** The code was developed
and tested on a machine with no Postgres, Docker, or Homebrew available. The
dialect-independent parts — table ordering, type coercion, completeness — are
covered by `tests/test_migration.py`. The Postgres-specific parts (sequence
reset, type acceptance, advisory locks) are verified by step 4, which is why
step 4 exists and must not be skipped.

---

## What changed in the code

| Concern | Before | Now |
|---|---|---|
| Database URL | SQLite path hard-coded | `DATABASE_URL` if set, else SQLite. `postgres://` rewritten to `postgresql://` |
| Schema changes | Hand-written `ALTER TABLE` at startup | Alembic. The old block still runs on SQLite for backward compatibility, never on Postgres |
| Scheduled jobs | One per process | Postgres advisory lock; every worker wakes, one runs |
| Workers | Pinned to 1 | `WEB_CONCURRENCY`, default 1, refuses >1 without Postgres |
| Upserts | SQLite dialect only | Dialect chosen from the live connection |
| Start command | `gunicorn …` | `./release.sh` — migrates when on Postgres, then starts gunicorn |

Today, with no `DATABASE_URL` set, every one of these resolves to exactly the
current behaviour. Deploying this change on its own is a no-op.

---

## 1. Deploy the code (safe, changes nothing)

Merge and deploy as normal. With no `DATABASE_URL`, `release.sh` logs
`no DATABASE_URL — SQLite` and starts one worker, as before. Confirm the app is
healthy before continuing.

## 2. Take a backup you have actually restored

```bash
railway run cat /app/data/wesley.db > wesley-backup-$(date +%F).db
sqlite3 wesley-backup-$(date +%F).db "select count(*) from churches;"
```

The second command matters more than the first. A backup you have not opened is
a hope, not a backup.

## 3. Provision Postgres

Add a PostgreSQL service in the Railway project. Do **not** attach it to the web
service yet — attaching sets `DATABASE_URL`, which would cut over immediately.
Copy its connection string for local use in the next two steps.

## 4. Create the schema and dry-run the copy

From your machine, against the new database:

```bash
pip install -r requirements.txt
DATABASE_URL='<connection string>' FLASK_APP=app.py flask db upgrade
DATABASE_URL='<connection string>' python3 migrate_to_postgres.py --check --sqlite wesley-backup-$(date +%F).db
```

`--check` writes nothing. It verifies the target is reachable, that every table
the models declare exists there, and that the target is empty, then prints the
row counts it would copy. If it reports missing tables, `flask db upgrade` did
not complete.

This step is also the first real exercise of Postgres compatibility. If
something is wrong with the schema, it surfaces here, against an empty database,
with production still untouched.

## 5. Copy the data

```bash
DATABASE_URL='<connection string>' python3 migrate_to_postgres.py --run --sqlite wesley-backup-$(date +%F).db
```

It copies parents-first, advances every identity sequence past the highest id it
inserted, then re-counts both sides and refuses to report success unless they
match.

**The sequence reset is the step people skip.** Rows arrive with their original
primary keys while Postgres sequences stay at 1, so the app looks perfectly
migrated until the first new signup collides with church id 1. The script prints
`Sequences advanced: …`; if that line says `none needed` and you copied real
rows, stop and investigate.

## 6. Cut over

Attach the Postgres service to the web service so `DATABASE_URL` is set, and
redeploy. `release.sh` will run `flask db upgrade` (a no-op — already applied)
and start one worker against Postgres.

Verify before going further:

- Log in to the dashboard.
- Ask the widget a question on a real church site, and check the answer streams.
- Open `/admin` and confirm church list and token counts look right.
- **Create something** — a text snippet is cheap and safe. This is what proves
  the sequences are right; reads alone will not.

## 7. Raise the worker count

Only now, and only after step 6 is confirmed:

```
WEB_CONCURRENCY=3
```

Redeploy. Each worker runs its own scheduler; the advisory lock means one wins
each job. Confirm the next morning that the nightly logs show each job once, not
three times:

```bash
railway logs | grep -E "Nightly crawl|Embedding warm|Weekly digest"
```

If `WEB_CONCURRENCY` is ever set without `DATABASE_URL`, `release.sh` exits
rather than starting — because on SQLite the lock cannot coordinate anything and
every church would get duplicate digest emails.

## Rollback

Before step 7, rollback is: remove `DATABASE_URL` from the web service and
redeploy. The SQLite volume is untouched throughout — nothing in this runbook
writes to it — so the app returns to exactly its current state, minus anything
written to Postgres after cutover.

After real traffic has hit Postgres, rolling back means losing that traffic.
Decide at step 6 whether you are committed.

---

## Afterwards

- **The SQLite volume can be detached** once you are confident, but keep it for
  a few weeks. It costs almost nothing and is the only copy of pre-cutover data
  that is not a file on your laptop.
- **Uploads are still on the container filesystem.** This migration moves the
  database only. `DATA_DIR` still holds `uploads/`, and those files are not in
  Postgres. If the Railway volume is not mounted at `DATA_DIR`, uploaded
  documents are already being lost on every redeploy — worth checking while you
  are in there.
- **Schema changes from now on** are `flask db migrate -m "what changed"`,
  review the generated file, commit it. `release.sh` applies it on deploy.
