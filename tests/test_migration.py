"""Tests for the SQLite → PostgreSQL data migration.

No PostgreSQL is available in this environment, so these exercise the parts of
the migration that are dialect-independent — table ordering, type coercion, and
completeness — against a throwaway SQLite target. What they cannot prove is
covered explicitly in the runbook: the sequence reset and the Postgres-specific
type acceptance both need a real target database, which is what
`migrate_to_postgres.py --check` is for.
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, func

import migrate_to_postgres as migration
from models import db


@pytest.fixture
def populated_source(tmp_path, app):
    """A SQLite database with the full schema and a realistic tangle of rows."""
    from models import (
        Church, User, Conversation, Message, WidgetConversation, WidgetMessage,
        AnswerFeedback, Document, GuestConnection, UsageDaily,
    )
    path = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{path}")
    db.metadata.create_all(engine)

    from sqlalchemy.orm import Session
    with Session(engine) as s:
        church = Church(name="Grace Church", billing_exempt=True,
                        trial_ends_at=datetime(2026, 12, 1),
                        manual_payment_expires=date(2027, 1, 15),
                        manual_payment_active=True)
        s.add(church); s.flush()
        s.add(User(email="a@b.org", password_hash="x", church_id=church.id))
        s.add(Document(church_id=church.id, filename="f.pdf",
                       original_name="Policy.pdf", size_bytes=10))
        s.add(GuestConnection(church_id=church.id, name="Ann", email="ann@x.org",
                              pco_email_synced=True))
        s.add(UsageDaily(church_id=church.id, day=date(2026, 9, 3), surface="widget",
                         model="m", calls=3, total_tokens=120))
        conv = Conversation(church_id=church.id, title="Chat")
        s.add(conv); s.flush()
        s.add(Message(conversation_id=conv.id, role="user", content="hi"))
        wconv = WidgetConversation(church_id=church.id, session_id="abc")
        s.add(wconv); s.flush()
        wmsg = WidgetMessage(widget_conversation_id=wconv.id, role="assistant",
                             content="hello")
        s.add(wmsg); s.flush()
        s.add(AnswerFeedback(church_id=church.id, widget_message_id=wmsg.id,
                             rating="helpful"))
        s.commit()
    return path, engine


@pytest.fixture
def empty_target(tmp_path):
    path = tmp_path / "target.db"
    engine = create_engine(f"sqlite:///{path}")
    db.metadata.create_all(engine)
    return path, engine


class TestCopy:
    def test_every_row_arrives(self, populated_source, empty_target):
        _, source = populated_source
        _, target = empty_target
        tables = migration._ordered_tables()
        source_tables = set(inspect(source).get_table_names())

        migration.copy_all(source, target, tables, source_tables)

        before = migration._counts(source, tables)
        after = migration._counts(target, tables)
        assert before == after
        assert sum(after.values()) == 10

    def test_foreign_keys_hold_after_the_copy(self, populated_source, empty_target):
        """Rows must be inserted parents-first or the child rows are orphans."""
        _, source = populated_source
        target_path, target = empty_target
        migration.copy_all(source, target, migration._ordered_tables(),
                           set(inspect(source).get_table_names()))

        con = sqlite3.connect(target_path)
        con.execute("PRAGMA foreign_keys = ON")
        violations = con.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []

    def test_parents_are_ordered_before_their_children(self):
        order = [t.name for t in migration._ordered_tables()]
        for parent, child in [
            ("churches", "users"),
            ("churches", "conversations"),
            ("conversations", "messages"),
            ("widget_conversations", "widget_messages"),
            ("widget_messages", "answer_feedback"),
            ("sermon_sources", "sermons"),
            ("church_calendars", "calendar_events"),
        ]:
            assert order.index(parent) < order.index(child), \
                f"{parent} must be inserted before {child}"

    def test_a_table_with_no_rows_is_skipped_not_failed(self, populated_source, empty_target):
        _, source = populated_source
        _, target = empty_target
        copied = migration.copy_all(source, target, migration._ordered_tables(),
                                    set(inspect(source).get_table_names()))
        assert "invites" not in copied  # empty at source
        assert copied["churches"] == 1


class TestCoercion:
    """SQLite is permissive about types in a way Postgres is not."""

    def _column(self, table_name, column_name):
        return db.metadata.tables[table_name].columns[column_name]

    def test_integer_booleans_become_real_booleans(self):
        column = self._column("churches", "billing_exempt")
        assert migration._coerce(1, column) is True
        assert migration._coerce(0, column) is False

    def test_string_booleans_become_real_booleans(self):
        column = self._column("churches", "billing_exempt")
        assert migration._coerce("1", column) is True
        assert migration._coerce("false", column) is False

    def test_string_timestamps_become_datetimes(self):
        column = self._column("churches", "trial_ends_at")
        assert migration._coerce("2026-12-01 10:30:00", column) == \
            datetime(2026, 12, 1, 10, 30)

    def test_string_dates_become_dates(self):
        """A DATE column receiving SQLite's datetime string must not keep the
        time part, which Postgres rejects for a date."""
        column = self._column("churches", "manual_payment_expires")
        assert migration._coerce("2027-01-15 00:00:00", column) == date(2027, 1, 15)

    def test_null_stays_null(self):
        column = self._column("churches", "trial_ends_at")
        assert migration._coerce(None, column) is None

    def test_binary_values_pass_through(self):
        """Embedding vectors are raw bytes and must not be coerced."""
        column = self._column("embedding_cache", "vector")
        blob = b"\x00\x01\x02"
        assert migration._coerce(blob, column) == blob


class TestCompleteness:
    def test_every_model_table_is_in_the_copy_set(self):
        """A new model that is not copied would silently lose its data."""
        assert {t.name for t in migration._ordered_tables()} == set(db.metadata.tables)

    def test_the_alembic_revision_covers_every_model_table(self):
        """The target schema is built by `flask db upgrade`, so a table missing
        from the revision would not exist to copy into."""
        versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
        created = set()
        for revision in versions.glob("*.py"):
            text = revision.read_text()
            for line in text.splitlines():
                if "op.create_table(" in line:
                    created.add(line.split("op.create_table(")[1].split("'")[1])
        missing = set(db.metadata.tables) - created
        assert not missing, (
            f"tables in the models but never created by a migration: {sorted(missing)}. "
            "Run `flask db migrate` to generate a revision for them."
        )
