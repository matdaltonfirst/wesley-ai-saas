# PostgreSQL migration runbook

The code is ready. This is the sequence to run, and it is written so that
nothing before step 6 touches the live app: the current SQLite deployment keeps
serving throughout, and every step before the cutover is reversible by doing
nothing.

**Executed against production on 4 September 2026.** 699 rows across 21 tables,
verified matching on both sides; sequences advanced; three workers running with
the advisory lock confirmed to exclude a second holder. Kept as the record of
how it was done and how to repeat it in another environment.

Two things this document originally got wrong, corrected below in place:

* `railway run` executes **locally** with Railway's variables injected — it does
  not run inside the container. Reaching the volume needs `railway ssh`.
* The Postgres service exposes no `DATABASE_PUBLIC_URL`, so the copy cannot be
  driven from a laptop without opening a public proxy. Running it inside the
  web container is both simpler and keeps members' data on Railway.

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

Requires an SSH key registered (`railway ssh keys add`), and the host key
trusted (`ssh-keyscan ssh.railway.com >> ~/.ssh/known_hosts`) if this is the
first connection from this machine.

```bash
railway ssh --service web "sha256sum /app/data/wesley.db"
railway ssh --service web "base64 -w0 /app/data/wesley.db" | base64 -d > wesley-backup-$(date +%F).db
shasum -a 256 wesley-backup-$(date +%F).db     # must match the first command
sqlite3 wesley-backup-$(date +%F).db "PRAGMA integrity_check; select count(*) from churches;"
```

The checksum comparison is the point: it proves the transfer was clean. The
integrity check and row count prove the file is a usable database rather than a
1.4 MB blob that happens to have arrived.

The second command matters more than the first. A backup you have not opened is
a hope, not a backup.

## 3. Provision Postgres

Add a PostgreSQL service in the Railway project. Do **not** attach it to the web
service yet — attaching sets `DATABASE_URL`, which would cut over immediately.
Copy its connection string for local use in the next two steps.

## 4. Create the schema and dry-run the copy

Run these **inside the web container**, which can reach
`postgres.railway.internal` and already has the dependencies installed. Read the
internal URL into a shell variable without printing it, then pass it through:

```bash
DB=$(railway run --service Postgres bash -c 'printf "X:%s\n" "$DATABASE_URL"' \
       | grep -oE '^X:.*' | sed 's/^X://')

railway ssh --service web "cd /app && DATABASE_URL='$DB' FLASK_APP=app.py \
  /opt/venv/bin/python -m flask db upgrade"

railway ssh --service web "cd /app && DATABASE_URL='$DB' \
  /opt/venv/bin/python migrate_to_postgres.py --check --sqlite /app/data/wesley.db"
```

Note `/opt/venv/bin/python`, not `python3`: the container's default interpreter
is the Nix one and has none of the app's dependencies.

`--check` writes nothing. It verifies the target is reachable, that every table
the models declare exists there, and that the target is empty, then prints the
row counts it would copy. If it reports missing tables, `flask db upgrade` did
not complete.

This step is also the first real exercise of Postgres compatibility. If
something is wrong with the schema, it surfaces here, against an empty database,
with production still untouched.

## 5. Copy the data

```bash
railway ssh --service web "cd /app && DATABASE_URL='$DB' \
  /opt/venv/bin/python migrate_to_postgres.py --run --sqlite /app/data/wesley.db"
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
- **Create something.** This is what proves the sequences are right; reads
  alone will not. To test without leaving rows behind, insert inside a
  transaction and roll back — the id is still drawn from the sequence:

  ```python
  before = db.session.query(func.max(Model.id)).scalar() or 0
  obj = Model(...); db.session.add(obj); db.session.flush()
  assert obj.id > before      # would be 1 if the sequence was not reset
  db.session.rollback()
  ```

  Run against churches, conversations, widget_conversations and text_snippets.
  On this migration `widget_conversations` returned 89 against a maximum of 88 —
  and would have returned 1 had the reset been skipped.

## 7. Raise the worker count

Only now, and only after step 6 is confirmed:

```
WEB_CONCURRENCY=3
```

Redeploy. Each worker runs its own scheduler; the advisory lock means one wins
each job. Do not wait until morning to find out whether the lock works — prove
it directly:

```python
with job_lock("weekly_digest") as first:      # True
    with job_lock("weekly_digest") as second: # must be False
        ...
```

Then confirm the next morning that the nightly logs show each job once, not
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


## What this migration surfaced

`embedding_cache` was **empty** in the SQLite source, meaning the nightly warm
job had never successfully populated it and every church was still answering
from keyword scoring. Nothing looked wrong from outside, because the semantic
path falls back silently by design — which is exactly why it needs an explicit
check rather than an impression that things seem fine.

Running `embedding_warm_job()` by hand produced 562 vectors across four churches
in about fifteen seconds. After that, asking Dalton First's widget "do you have
childcare during the service?" returned the nursery details with a citation,
where before it would have found nothing.

**Check `embedding_cache` has rows after any deploy that restarts the process
near 02:45**, or add a startup log line reporting the row count.
