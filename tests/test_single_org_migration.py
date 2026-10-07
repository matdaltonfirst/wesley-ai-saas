"""The multi-tenant to single-organization data migration.

Runs the migration's portable data step on SQLite, against a database built by
the real earlier Alembic revisions, with a realistic mix of tenants. What this
cannot prove (PostgreSQL's DROP COLUMN, constraint and foreign-key behavior) is
covered by the rolled-back rehearsal against a copy of production described in
docs/RUNBOOK.md.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from models import Organization

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
OLD_REVISIONS = ["a01ccbb964a2_initial_schema", "90e0b2579d19_sermon_transcript_segments",
                 "dbfa384bb56f_content_profiles_and_sermon_packets"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def old_db():
    """A SQLite database at the last multi-tenant schema, plus the empty organization table."""
    engine = sa.create_engine("sqlite://", poolclass=sa.pool.StaticPool)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            for rev in OLD_REVISIONS:
                _load(rev).upgrade()
        Organization.__table__.create(conn)
    return engine


def _church(conn, cid, name, comms=True):
    conn.execute(sa.text(
        "INSERT INTO churches (id, name, bot_name, welcome_message, primary_color, "
        "onboarding_complete, denomination, comms_enabled, billing_exempt, plan, "
        "trial_reminder_sent, manual_payment_active, warning_30_sent, warning_7_sent, "
        "expired_sent, website_url, church_city, timezone) VALUES "
        "(:i, :n, 'Wesley', 'Hi', '#112233', 1, 'umc', :c, 0, 'founders', 0, 0, 0, 0, 0, "
        ":w, 'Dalton, GA', 'America/New_York')"),
        {"i": cid, "n": name, "c": comms, "w": f"https://c{cid}.example.org"})


def _user(conn, uid, cid, email, role="admin"):
    conn.execute(sa.text("INSERT INTO users (id, email, password_hash, church_id, role) "
                         "VALUES (:i, :e, 'x', :c, :r)"), {"i": uid, "e": email, "c": cid, "r": role})


def _page(conn, cid, url):
    conn.execute(sa.text("INSERT INTO crawled_pages (church_id, url, content) VALUES (:c, :u, 't')"),
                 {"c": cid, "u": url})


def _widget_convo(conn, cid, session):
    conn.execute(sa.text("INSERT INTO widget_conversations (church_id, session_id) VALUES (:c, :s)"),
                 {"c": cid, "s": session})
    wid = conn.execute(sa.text("SELECT id FROM widget_conversations WHERE session_id=:s"),
                       {"s": session}).scalar()
    conn.execute(sa.text("INSERT INTO widget_messages (widget_conversation_id, role, content) "
                         "VALUES (:w, 'user', 'hi')"), {"w": wid})


@pytest.fixture
def tenants(old_db):
    with old_db.begin() as conn:
        _church(conn, 2, "Dalton First UMC", comms=False)
        _church(conn, 6, "Dalton First Methodist church")
        _church(conn, 8, "Ringgold UMC")
        _user(conn, 1, 2, "mat@daltonfumc.com")
        _user(conn, 2, 6, "merideth@daltonfumc.com")
        _user(conn, 3, 8, "taylor@ringgoldumc.org")
        _page(conn, 2, "https://daltonfumc.com/a")
        _page(conn, 8, "https://ringgoldumc.org/a")
        _widget_convo(conn, 2, "ours")
        _widget_convo(conn, 8, "theirs")
        conn.execute(sa.text(
            "INSERT INTO comms_requests (id, church_id, submitter_id, submitter_name, request_type, "
            "event_name, event_date, target_audience, timeline, deliverables) VALUES "
            "('r6', 6, 2, 'merideth', 'graphic', 'Fall Fest', '2026-10-31', 'community', "
            "'this_week', '[]')"))
    return old_db


def _run(engine, confirmed=True):
    mod = _load("c3a7f1d20b44_single_organization")
    with engine.begin() as conn:
        mod.collapse_data(conn, src=2, merge=[6], confirmed=confirmed)


def _scalar(engine, sql):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).scalar()


class TestCollapseData:
    def test_the_organization_comes_from_the_source_church(self, tenants):
        _run(tenants)
        with tenants.connect() as conn:
            org = conn.execute(sa.text("SELECT * FROM organization")).mappings().one()
        assert org["id"] == 1
        assert org["name"] == "Dalton First UMC"
        assert org["church_city"] == "Dalton, GA"
        assert org["timezone"] == "America/New_York"
        assert org["website_url"] == "https://c2.example.org"
        # The live website embed was installed with the source church's id.
        assert org["legacy_widget_id"] == 2
        assert '"comms": false' in org["features"]

    def test_other_churches_rows_are_deleted_with_their_children(self, tenants):
        _run(tenants)
        assert _scalar(tenants, "SELECT count(*) FROM crawled_pages") == 1
        assert _scalar(tenants, "SELECT url FROM crawled_pages") == "https://daltonfumc.com/a"
        assert _scalar(tenants, "SELECT count(*) FROM widget_conversations") == 1
        assert _scalar(tenants, "SELECT count(*) FROM widget_messages") == 1
        assert _scalar(tenants, "SELECT count(*) FROM users WHERE email='taylor@ringgoldumc.org'") == 0

    def test_a_duplicate_signup_is_folded_in_and_its_user_becomes_staff(self, tenants):
        _run(tenants)
        row = None
        with tenants.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT church_id, role FROM users WHERE email='merideth@daltonfumc.com'")).one()
        assert tuple(row) == (2, "staff")
        assert _scalar(tenants, "SELECT church_id FROM comms_requests WHERE id='r6'") == 2

    def test_the_source_churchs_own_data_is_untouched(self, tenants):
        _run(tenants)
        assert _scalar(tenants, "SELECT role FROM users WHERE email='mat@daltonfumc.com'") == "admin"
        assert _scalar(tenants, "SELECT count(*) FROM widget_conversations WHERE session_id='ours'") == 1

    def test_it_refuses_to_delete_other_churches_without_confirmation(self, tenants):
        with pytest.raises(RuntimeError, match="WESLEY_CONFIRM_DROP_TENANTS"):
            _run(tenants, confirmed=False)
        # Nothing changed: the guard fires before any write.
        assert _scalar(tenants, "SELECT count(*) FROM organization") == 0
        assert _scalar(tenants, "SELECT count(*) FROM crawled_pages") == 2

    def test_no_confirmation_is_needed_when_only_duplicates_exist(self, old_db):
        with old_db.begin() as conn:
            _church(conn, 2, "Dalton First UMC")
            _church(conn, 6, "Dup")
        _run(old_db, confirmed=False)
        assert _scalar(old_db, "SELECT count(*) FROM organization") == 1

    def test_a_missing_source_church_is_an_error(self, old_db):
        with old_db.begin() as conn:
            _church(conn, 8, "Ringgold UMC")
        with pytest.raises(RuntimeError, match="Source church 2 does not exist"):
            _run(old_db)

    def test_a_fresh_database_gets_a_default_organization(self, old_db):
        _run(old_db)
        assert _scalar(old_db, "SELECT name FROM organization") == "Dalton First United Methodist Church"
