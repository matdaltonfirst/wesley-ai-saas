"""Restore a backup_dump.py backup into a PostgreSQL database at the OLD schema.

    DATABASE_URL=postgresql://... python scripts/restore_to_postgres.py <backup-dir>/db [--schema NAME]

This is the rollback for the single-organization migration (docs/RUNBOOK.md).
The target must already be at the pre-migration Alembic revision (dbfa384bb56f),
so rebuild the schema first with the OLD code:

    DROP SCHEMA public CASCADE; CREATE SCHEMA public;
    git checkout <pre-migration tag> && flask db upgrade dbfa384bb56f

It refuses to run against a non-empty database, loads parents before children,
advances every id sequence past the highest restored id, then verifies each
table's row count against MANIFEST.json and exits non-zero on any mismatch.
--schema restores into a named schema instead of the default (for rehearsals).
"""
import argparse
import base64
import datetime
import decimal
import json
import os
import sys

import sqlalchemy as sa

EXPECTED_REVISION = "dbfa384bb56f"


def decode(v):
    if isinstance(v, dict) and "__t" in v:
        t, x = v["__t"], v["v"]
        return {"dt": datetime.datetime.fromisoformat, "d": datetime.date.fromisoformat,
                "dec": decimal.Decimal, "b": base64.b64decode}[t](x)
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backup_dir")
    ap.add_argument("--schema", default=None)
    args = ap.parse_args()

    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://", 1)
    if args.schema:
        url += ("&" if "?" in url else "?") + f"options=-csearch_path%3D{args.schema}"
    engine = sa.create_engine(url)
    manifest = json.load(open(os.path.join(args.backup_dir, "MANIFEST.json")))["tables"]

    meta = sa.MetaData()
    meta.reflect(bind=engine, schema=args.schema)
    tables = {t.name: t for t in meta.sorted_tables}

    with engine.begin() as conn:
        revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        if revision != EXPECTED_REVISION:
            print(f"Refusing: database is at {revision!r}, expected {EXPECTED_REVISION!r}. "
                  "Rebuild the old schema first.")
            return 2
        missing = [t for t in manifest if t != "alembic_version" and t not in tables]
        if missing:
            print(f"Refusing: tables in the backup but not the database: {missing}")
            return 2
        for name in manifest:
            if name == "alembic_version":
                continue
            if conn.execute(sa.select(sa.func.count()).select_from(tables[name])).scalar():
                print(f"Refusing: {name} is not empty. Restore into a fresh schema.")
                return 2

        for table in tables.values():                       # parents first
            name = table.name
            if name == "alembic_version" or name not in manifest:
                continue
            rows = [{k: decode(v) for k, v in json.loads(line).items()}
                    for line in open(os.path.join(args.backup_dir, name + ".jsonl"))]
            if rows:
                conn.execute(table.insert(), rows)
        advanced = []
        for table in tables.values():
            if "id" in table.c and table.name in manifest and isinstance(table.c.id.type, sa.Integer):
                seq = conn.execute(sa.text("SELECT pg_get_serial_sequence(:t, 'id')"),
                                   {"t": table.fullname}).scalar()
                if seq:
                    conn.execute(sa.text("SELECT setval(:s, GREATEST((SELECT COALESCE(MAX(id),0) FROM "
                                         + table.fullname + "), 1))"), {"s": seq})
                    advanced.append(table.name)

        bad = 0
        for name, info in manifest.items():
            if name == "alembic_version":
                continue
            got = conn.execute(sa.select(sa.func.count()).select_from(tables[name])).scalar()
            ok = got == info["rows"]
            bad += not ok
            print(f"{'OK ' if ok else 'BAD'} {name:28} backup={info['rows']:5} restored={got:5}")
        print("Sequences advanced for", len(advanced), "tables")
        if bad:
            print(f"RESTORE FAILED ({bad} tables). Rolling back.")
            raise SystemExit(1)
    print("RESTORE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
