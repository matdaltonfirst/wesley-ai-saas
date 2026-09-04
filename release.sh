#!/usr/bin/env bash
# Start command for Railway. Applies migrations when running on Postgres, then
# launches gunicorn.
set -euo pipefail

export FLASK_APP="${FLASK_APP:-app.py}"
WORKERS="${WEB_CONCURRENCY:-1}"

if [ -n "${DATABASE_URL:-}" ]; then
  echo "release: Postgres detected — applying migrations"
  flask db upgrade
else
  echo "release: no DATABASE_URL — SQLite, schema handled by create_all"
  # The scheduler's cross-process lock is a Postgres advisory lock; on SQLite it
  # cannot coordinate anything. More than one worker would therefore give every
  # church duplicate weekly digests, duplicate crawls, and duplicate billing
  # warnings — silently, and only visible in someone's inbox.
  if [ "$WORKERS" -gt 1 ]; then
    echo "release: refusing to start $WORKERS workers without Postgres." >&2
    echo "release: set DATABASE_URL first, or unset WEB_CONCURRENCY." >&2
    exit 1
  fi
fi

echo "release: starting gunicorn with $WORKERS worker(s)"
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT:-5000}" \
  --workers "$WORKERS" \
  --threads 4 \
  --timeout 120
