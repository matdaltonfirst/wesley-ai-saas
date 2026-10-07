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
