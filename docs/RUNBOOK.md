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
