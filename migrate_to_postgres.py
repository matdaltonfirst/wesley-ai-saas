"""Copy a Wesley SQLite database into PostgreSQL.

Usage (from the project root, with the target schema already created by
`flask db upgrade`):

    DATABASE_URL='postgresql://...' python3 migrate_to_postgres.py --check
    DATABASE_URL='postgresql://...' python3 migrate_to_postgres.py --run

--check does everything except write: it verifies both ends are reachable, that
the target schema matches the models, and that the target is empty. Run it
first. --run performs the copy and then verifies row counts on both sides.

Three things this handles that a naive dump/restore does not:

1. **Sequence reset.** Rows are inserted with their original primary keys, which
   leaves every Postgres identity sequence still at 1. The first insert the app
   attempts afterwards collides with an existing row. Sequences are advanced to
   match the data before this script reports success — skipping it produces an
   app that looks migrated and then fails on the first signup.

2. **Table order.** Rows are inserted parents-first so foreign keys are
   satisfiable, using the dependency order SQLAlchemy derives from the models
   rather than a hand-maintained list that a new table could fall out of.

3. **Booleans and dates.** SQLite stores booleans as 0/1 and dates as strings.
   Values are coerced to the type each column actually declares, because
   Postgres will not accept 0 for a boolean or a string for a date.
"""

import argparse
import os
import sys
from datetime import date, datetime

from sqlalchemy import create_engine, inspect, select, func
from sqlalchemy.orm import Session

# models pulls in no application setup, so importing it here builds the table
# metadata without creating or migrating anything.
from models import db
import models  # noqa: F401  (importing registers every model on db.metadata)


BOOL_TRUE = {1, "1", "true", "True", "t", "y", "yes", True}


def _coerce(value, column):
    """Turn a SQLite value into something Postgres will accept for *column*."""
    if value is None:
        return None
    python_type = None
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return value

    if python_type is bool:
        return value in BOOL_TRUE
    if python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if python_type is date and isinstance(value, str):
        return date.fromisoformat(value.split(" ")[0])
    return value


def _ordered_tables():
    """Tables parents-first, so foreign keys resolve as rows are inserted."""
    return db.metadata.sorted_tables


def _reset_sequences(target_engine, tables):
    """Advance each identity sequence past the highest id already inserted."""
    from sqlalchemy import text

    adjusted = []
    with target_engine.begin() as conn:
        for table in tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1:
                continue
            column = pk[0]
            try:
                python_type = column.type.python_type
            except NotImplementedError:
                continue
            if python_type is not int:
                continue
            seq = conn.execute(text(
                "SELECT pg_get_serial_sequence(:t, :c)"
            ), {"t": table.name, "c": column.name}).scalar()
            if not seq:
                continue
            highest = conn.execute(
                select(func.max(column))
            ).scalar()
            if highest is None:
                continue
            conn.execute(text("SELECT setval(:seq, :value)"),
                         {"seq": seq, "value": int(highest)})
            adjusted.append(f"{table.name}={highest}")
    return adjusted


def copy_all(source, target, tables, source_tables):
    """Copy every row, parents first, in one transaction. Returns per-table counts.

    Separated from the command line so it can be exercised end to end against a
    throwaway database in the tests, which is the only place the ordering and
    coercion logic can be proven without a live Postgres.
    """
    copied = {}
    with Session(source) as src_session, target.begin() as dest:
        for table in tables:
            if table.name not in source_tables:
                continue
            rows = src_session.execute(select(table)).mappings().all()
            if not rows:
                continue
            payload = [
                {c.name: _coerce(row.get(c.name), c) for c in table.columns}
                for row in rows
            ]
            dest.execute(table.insert(), payload)
            copied[table.name] = len(payload)
    return copied


def _counts(engine, tables):
    out = {}
    with engine.connect() as conn:
        for table in tables:
            out[table.name] = conn.execute(
                select(func.count()).select_from(table)).scalar()
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default=os.getenv("SQLITE_PATH", "data/wesley.db"),
                        help="Path to the source SQLite file (default: data/wesley.db)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Verify only; write nothing.")
    mode.add_argument("--run", action="store_true", help="Perform the copy.")
    args = parser.parse_args()

    target_url = os.getenv("DATABASE_URL", "").strip()
    if not target_url:
        sys.exit("DATABASE_URL is not set. Point it at the target Postgres database.")
    if target_url.startswith("postgres://"):
        target_url = "postgresql://" + target_url[len("postgres://"):]
    if not target_url.startswith("postgresql"):
        sys.exit(f"DATABASE_URL is not a PostgreSQL URL: {target_url.split('@')[0]}...")

    if not os.path.exists(args.sqlite):
        sys.exit(f"Source database not found: {args.sqlite}")

    source = create_engine(f"sqlite:///{args.sqlite}")
    target = create_engine(target_url)
    tables = _ordered_tables()

    # 1. Both ends reachable, and the target schema matches the models.
    try:
        target_tables = set(inspect(target).get_table_names())
    except Exception as e:
        sys.exit(f"Cannot connect to the target database: {e}")

    expected = {t.name for t in tables}
    missing = expected - target_tables
    if missing:
        sys.exit("Target is missing tables: " + ", ".join(sorted(missing))
                 + "\nRun `flask db upgrade` against it first.")

    source_tables = set(inspect(source).get_table_names())
    absent_at_source = expected - source_tables
    if absent_at_source:
        print("NOTE: not present in the SQLite source, will be left empty: "
              + ", ".join(sorted(absent_at_source)))

    source_counts = _counts(source, [t for t in tables if t.name in source_tables])
    total_rows = sum(source_counts.values())
    print(f"\nSource: {args.sqlite}")
    for name, count in source_counts.items():
        if count:
            print(f"  {name:<28} {count:>8}")
    print(f"  {'TOTAL':<28} {total_rows:>8}")

    # 2. The target must be empty; this script appends and would duplicate.
    target_counts = _counts(target, tables)
    non_empty = {n: c for n, c in target_counts.items() if c}
    if non_empty:
        print("\nTarget is NOT empty:")
        for name, count in non_empty.items():
            print(f"  {name:<28} {count:>8}")
        sys.exit("Refusing to copy into a non-empty database. Drop and re-upgrade it.")
    print("\nTarget schema present and empty.")

    if args.check:
        print("\n--check passed. Re-run with --run to perform the copy.")
        return

    # 3. Copy, parents first, in one transaction.
    print("\nCopying...")
    for name, count in copy_all(source, target, tables, source_tables).items():
        print(f"  {name:<28} {count:>8}")

    # 4. Sequences, or the first insert the app makes collides.
    adjusted = _reset_sequences(target, tables)
    print("\nSequences advanced: " + (", ".join(adjusted) or "none needed"))

    # 5. Verify both sides agree.
    final_counts = _counts(target, tables)
    mismatches = [
        (name, count, final_counts.get(name, 0))
        for name, count in source_counts.items()
        if count != final_counts.get(name, 0)
    ]
    if mismatches:
        print("\nROW COUNT MISMATCH:")
        for name, expected_count, actual in mismatches:
            print(f"  {name:<28} source={expected_count} target={actual}")
        sys.exit("Migration did not verify. Do not point the app at this database.")

    print(f"\nVerified: {sum(final_counts.values())} rows match on both sides.")
    print("Next: set DATABASE_URL on the web service and redeploy.")


if __name__ == "__main__":
    main()
