# Runbook

## Backup and restore

Taken 7 Oct 2026 before the single-organization conversion. Kept outside the repo
because it contains visitor and staff personal data: `~/wesley-backups/2026-10-07/`
(directory mode 700). SHA-256 of `wesley-backup.tar.gz`:
`6b07e60268d5e961ed65323d76cc6fc3fcbc5b13c988a6563d522bf758b08b54`.

Railway's container has no `pg_dump`, so the backup is a logical dump in Python.

1. Register a temporary SSH key: `railway ssh keys add` (remove it afterwards with
   `railway ssh keys remove <name>`). First time only: `ssh-keyscan ssh.railway.com >> ~/.ssh/known_hosts`.
2. Run the dump inside the container (read-only transaction), then tar the uploads:
   copy `scripts/backup_dump.py` over base64, then
   `/opt/venv/bin/python /tmp/dump.py /tmp/bk/db && tar -C /app/data -czf /tmp/bk/uploads.tar.gz uploads && tar -C /tmp -czf /tmp/wesley-backup.tar.gz bk`.
3. Pull it down and compare checksums. Wrap the payload in BEGIN/END marker lines;
   `railway ssh` appends warning text that otherwise corrupts a payload with no trailing newline.
4. Prove it restores: extract, then `PYTHONPATH=. python3 scripts/restore_check.py <dir>/bk/db`.
   It rebuilds the schema from the models in a scratch SQLite database and checks every
   table's row count and row contents against `MANIFEST.json`. Re-hash the uploaded
   files against `sha256sum` taken in the container.
5. Delete the container's temp files.

Known limit: the restore check loads into SQLite, so it proves completeness and
readability of the data, not Postgres type behavior.

## Archive of the other churches

Before the single-organization migration, the rows of every church other than Dalton First
UMC (and its duplicate sign-up) were exported from the verified backup to
`~/wesley-backups/2026-10-07/outside-tenants-archive.json.gz` (gzipped JSON, mode 600).
SHA-256: `6ef2a6dc96c5e6fb40316b90b4a53ce48969f75fcb48df847c24dab0c102fe17`.
It holds 6 churches, 6 users, 186 crawled pages, 3 widget conversations with 16 messages,
1 feedback row and 3 usage rows. It is not encrypted; keep it somewhere access-controlled.

## Deploying the single-organization conversion

This deploy runs an irreversible migration. Do it deliberately, with time to watch it.

1. **Fresh backup** the moment before, following "Backup and restore" above. Data changes
   daily (widget conversations), so the 7 October backup is not enough.
2. **Set Railway variables** on the web service: `WESLEY_CONFIRM_DROP_TENANTS=yes`.
   (`ORG_SOURCE_CHURCH_ID` defaults to 2 and `ORG_MERGE_CHURCH_IDS` to 6. Set them only to
   change that.) Remove the `STRIPE_*` variables afterwards.
3. **Merge to `main` and push.** `release.sh` runs `flask db upgrade` inside one transaction;
   if anything fails, PostgreSQL rolls it all back and the old tables are untouched. The
   new code will not start against the old schema, so a failed migration means the site is
   down until you roll back (below). Watch `railway logs`.
4. **Verify**: sign in at `/login`; `curl .../api/widget/branding?church_id=2` returns 200 and
   `church_id=5` returns 404; ask the widget on the live site a real question; open
   `/dashboard` and check Sunday Content, guests and Q&A are present.
5. **Remove** `WESLEY_CONFIRM_DROP_TENANTS` from Railway, so a later deploy cannot delete anything.

### What was rehearsed

The migration was run against a scratch schema inside production Postgres, built by the real
Alembic history and loaded with a copy of the production data, then dropped. It refused
without confirmation, then completed: 1 organization, 5 users, 22 comms requests, 323
crawled pages, 12 widget conversations, 66 widget messages, 37 sermons, 11 sermon packets,
no `church_id` column left. The new app was then run on it over Postgres: public chat
through the legacy id, streaming, staff chat, and the dashboard pages all worked. The first
rehearsal found a delete-order bug; it is fixed and now covered by a test.

### Rollback

Before step 3 completes nothing changes. After it:

1. In Railway, redeploy the previous deployment, or `git revert` the merge. The tag
   `pre-single-org` is the last multi-tenant commit.
2. Rebuild the old schema with the old code and restore the data:
   ```bash
   # in the container (railway ssh), against the production DATABASE_URL
   psql ... -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"   # or the equivalent via Python
   git checkout pre-single-org && flask db upgrade dbfa384bb56f
   python scripts/restore_to_postgres.py <backup>/db
   ```
   `restore_to_postgres.py` refuses unless the schema is at `dbfa384bb56f` and empty, loads
   parents first, advances every id sequence, and exits non-zero if any table's count
   differs from the backup.
3. Restore uploads from the backup's `uploads.tar.gz` if the volume was affected.

This restore was tested on 7 October against a scratch schema built by the old code: all 26
tables matched, sequences advanced, a second run refused. Anything written between the
backup and the rollback is lost, which is why step 1 of the deploy is a fresh backup.


## Turning on Google sign-in

Until `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set, password sign-in stays available
so nobody is locked out. Setting them switches passwords off.

1. In Google Cloud Console (a project owned by the church's Workspace), open APIs and Services,
   OAuth consent screen. Choose **Internal** so only `daltonfumc.com` accounts can use it.
   Scopes: `openid`, `email`, `profile`.
2. Credentials, Create credentials, OAuth client ID, type **Web application**. Authorized
   redirect URI: `https://app.wesleyai.co/auth/google/callback` (it is `APP_URL` plus
   `/auth/google/callback`).
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` on the Railway web service.
4. Before switching, make sure every person who needs access is in the People screen with a
   role, and that **you** have signed in once with Google from a second browser while
   passwords were still on. If something goes wrong, remove the two variables and passwords
   come back.
5. Sign in. A Google account that is on the church domain but not on the People screen is
   refused and the refusal is in the audit log.

## What to do when someone cannot sign in

Check the audit log (Audit log screen, "Denied requests" and "Sign-ins"). Common causes: the
person is not on the People screen, is deactivated, or used a personal Google account. Adding
or reactivating them is enough; no email is sent.

## The public chatbot misbehaves

1. **Too many requests or a bill spike:** the daily cap (`PUBLIC_DAILY_CAP`, default 3000 AI
   calls) makes the chat rest until midnight UTC and logs `PUBLIC CHATBOT DAILY CAP`. Lower it
   in Railway to throttle harder, or raise it for a busy event.
2. **The site's chat stopped working in browsers:** the origin check allows `daltonfumc.com` and
   its subdomains. If the website moves to another domain, set `PUBLIC_ALLOWED_ORIGINS`
   (comma separated) before moving.
3. **It says something wrong:** correct it from Feedback and Corrections (becomes approved Q&A).
4. **It revealed something it should not:** mark the source staff-only (Q&A, snippet, calendar,
   or document visibility) and tell Mat; add a canary for it in `tests/test_public_boundary.py`.


## Rolling back the roles and sign-in release (Phase 1b)

Backup taken just before it: `~/wesley-backups/2026-10-07-predeploy-1b/`, at Alembic revision
`c3a7f1d20b44`. The last commit before the release is the git tag `pre-phase-1b`.

1. In Railway, redeploy the previous deployment, or revert to `pre-phase-1b`.
2. In the container, rebuild the old schema with that code, on an empty schema:
   `flask db upgrade c3a7f1d20b44`.
3. Restore the data:
   `python scripts/restore_to_postgres.py <backup>/db --revision c3a7f1d20b44`.

Anything written between the backup and the rollback is lost. Note: the restore script's
refusal checks and sequence handling were tested against the earlier revision; the logic is
revision-independent, but run it first against a scratch schema (`--schema`) if time allows.

## A connector is failing or stale

Open `/integrations`. The status line says what is wrong in plain language, and the Recent
syncs list shows the error for each run.

- **Needs reconnect.** The saved login was refused. Click Connect again (or paste a new token
  for Facebook and Text In Church). Nothing is lost; syncing resumes.
- **Not set up on this server.** A Railway variable is missing. The line names it. Add it and
  redeploy. See [INTEGRATIONS.md](INTEGRATIONS.md) for where each value comes from.
- **Waiting for access.** The outside service has not turned something on (Text In Church API
  access, or a Planning Center scope that needs a reconnect).
- **Failing or Stale.** Click Sync now. If it fails the same way, copy the error from Recent
  syncs. Rate limits and outages clear on their own; the connector backs off and retries.
- **Numbers on the Streaming page look wrong.** Use History on that row to see where each
  number came from. A person's entry is never overwritten by a sync. Correct it by hand.

Turning a connector off on the Integrations page stops its syncs and keeps its history.

## Deploying Phase 2 (connectors and streaming)

Migration `f7a8b9c0d1e2` only adds tables and one column (`pco_connections.scope`), so it is
reversible: `flask db downgrade e5f6a7b8c9d0` drops them and loses only data synced since.

1. Fresh backup (see "Backup and restore").
2. Optionally set `TOKEN_ENCRYPTION_KEY` on Railway first (see INTEGRATIONS.md). Without it
   tokens are encrypted with `SECRET_KEY`; changing the key later means reconnecting.
3. Merge to `main` and push. `release.sh` runs the migration. Watch `railway logs`.
4. Verify: `/streaming` and `/integrations` load for an admin, an administrative assistant
   can enter a number and see its history, and the public widget still answers.
5. In Planning Center, reconnect once so the new scopes are granted. For YouTube, enable the
   two Google APIs and add the callback URL (see INTEGRATIONS.md), then Connect.

Rollback: redeploy the previous deployment, then `flask db downgrade e5f6a7b8c9d0` if the new
tables must go. The old code ignores the new tables, so the downgrade is optional.
