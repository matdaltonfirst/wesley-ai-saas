"""single organization: collapse the multi-tenant schema

Revision ID: c3a7f1d20b44
Revises: dbfa384bb56f
Create Date: 2026-10-07

IRREVERSIBLE. This turns the multi-tenant database into the single-organization
one for Dalton First UMC:

  1. creates the one-row ``organization`` table from the source church row
  2. folds duplicate sign-ups of the same church (``ORG_MERGE_CHURCH_IDS``)
     into it
  3. deletes every other church's rows (they were archived beforehand)
  4. drops every ``church_id`` column and the ``churches`` table

Settings, from the environment:

  ORG_SOURCE_CHURCH_ID            church row that becomes the organization (default 2)
  ORG_MERGE_CHURCH_IDS            comma list folded into it (default 6)
  WESLEY_CONFIRM_DROP_TENANTS=yes required when any other church has rows. Set
                                  it only after the archive and backup exist.

Alembic runs this inside one transaction on PostgreSQL, so a failure leaves the
database untouched. There is no downgrade: restore from the pre-migration backup
(docs/RUNBOOK.md). PostgreSQL only; local development builds its schema from the
models.
"""
import json
import os

from alembic import op
import sqlalchemy as sa

revision = "c3a7f1d20b44"
down_revision = "dbfa384bb56f"
branch_labels = None
depends_on = None

# Tables that carry church_id, in an order that is safe for deletes (children
# of other tables first).
TENANT_TABLES = [
    "answer_feedback", "calendar_events", "church_calendars", "sermon_packets",
    "sermons", "sermon_sources", "comms_requests", "guest_connections",
    "documents", "text_snippets", "qna_pairs", "knowledge_checklist_states",
    "knowledge_pack_states", "content_profiles", "crawled_pages", "usage_daily",
    "pco_connections", "invites", "widget_conversations", "conversations",
    "users",
]
# Rows of a folded duplicate church that move into the organization; any
# other table's rows for it are deleted (they would collide with the real ones).
MOVED_ON_MERGE = [
    "comms_requests", "guest_connections", "documents", "text_snippets",
    "qna_pairs", "answer_feedback", "widget_conversations", "conversations",
]

ORG_COLUMNS = (
    "name, website_url, last_crawled_at, created_at, bot_name, welcome_message, "
    "primary_color, church_city, starter_questions, bot_subtitle, local_practices, "
    "statement_of_faith, digest_last_sent_at, timezone"
)


def _ids(name, default):
    raw = os.getenv(name, default)
    return [int(x) for x in raw.split(",") if x.strip()]


def collapse_data(bind, src, merge, confirmed, org_name="Dalton First United Methodist Church"):
    """Steps 1 to 3: build the organization, fold duplicates, delete the rest.

    Portable SQL, kept separate from the PostgreSQL-only column drops so tests
    can exercise it on SQLite. *bind* is a connection; the ``organization``
    table must already exist.
    """
    keep = [src] + [m for m in merge if m != src]
    churches = bind.execute(sa.text("SELECT id FROM churches")).scalars().all()
    if churches and src not in churches:
        raise RuntimeError(f"Source church {src} does not exist (found {churches}).")
    others = [c for c in churches if c not in keep]
    if others and not confirmed:
        raise RuntimeError(
            f"Churches {others} would be deleted. Confirm the archive and backup "
            "exist, then set WESLEY_CONFIRM_DROP_TENANTS=yes."
        )

    # 1. the organization
    if churches:
        row = bind.execute(sa.text("SELECT comms_enabled FROM churches WHERE id = :s"),
                           {"s": src}).first()
        bind.execute(sa.text(
            f"INSERT INTO organization (id, {ORG_COLUMNS}, features, legacy_widget_id) "
            f"SELECT 1, {ORG_COLUMNS}, :f, id FROM churches WHERE id = :s"
        ), {"f": json.dumps({"comms": bool(row[0])}), "s": src})
    else:
        bind.execute(sa.text(
            "INSERT INTO organization (id, name, bot_name, welcome_message, primary_color) "
            "VALUES (1, :n, 'Wesley', 'How can I help you today?', '#0a3d3d')"
        ), {"n": org_name})

    # 2. fold duplicate sign-ups of this same church into it
    for cid in keep[1:]:
        for table in MOVED_ON_MERGE:
            bind.execute(sa.text(f"UPDATE {table} SET church_id = :s WHERE church_id = :m"),
                         {"s": src, "m": cid})
        # People keep access as staff; admin stays with the source church's admins.
        bind.execute(sa.text("UPDATE users SET church_id = :s, role = 'staff' "
                             "WHERE church_id = :m"), {"s": src, "m": cid})

    # 3. delete every other church's rows (already archived)
    bind.execute(sa.text(
        "DELETE FROM widget_messages WHERE widget_conversation_id IN "
        "(SELECT id FROM widget_conversations WHERE church_id <> :s)"), {"s": src})
    bind.execute(sa.text(
        "DELETE FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE church_id <> :s)"), {"s": src})
    for table in TENANT_TABLES:
        bind.execute(sa.text(f"DELETE FROM {table} WHERE church_id <> :s"), {"s": src})


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("This migration is PostgreSQL-only.")

    src = int(os.getenv("ORG_SOURCE_CHURCH_ID", "2"))
    merge = _ids("ORG_MERGE_CHURCH_IDS", "6")
    confirmed = os.getenv("WESLEY_CONFIRM_DROP_TENANTS", "").lower() == "yes"

    # Fail before changing anything.
    existing = bind.execute(sa.text("SELECT id FROM churches")).scalars().all()
    if existing and src not in existing:
        raise RuntimeError(f"Source church {src} does not exist (found {existing}).")
    others = [c for c in existing if c not in [src] + merge]
    if others and not confirmed:
        raise RuntimeError(
            f"Churches {others} would be deleted. Confirm the archive and backup "
            "exist, then set WESLEY_CONFIRM_DROP_TENANTS=yes."
        )

    op.create_table(
        "organization",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("website_url", sa.String(500)),
        sa.Column("last_crawled_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("bot_name", sa.String(100), nullable=False, server_default="Wesley"),
        sa.Column("welcome_message", sa.String(500), nullable=False,
                  server_default="How can I help you today?"),
        sa.Column("primary_color", sa.String(7), nullable=False, server_default="#0a3d3d"),
        sa.Column("church_city", sa.String(200)),
        sa.Column("starter_questions", sa.Text()),
        sa.Column("bot_subtitle", sa.String(200)),
        sa.Column("local_practices", sa.Text()),
        sa.Column("statement_of_faith", sa.Text()),
        sa.Column("features", sa.Text()),
        sa.Column("digest_last_sent_at", sa.DateTime()),
        sa.Column("timezone", sa.String(50)),
        sa.Column("legacy_widget_id", sa.Integer()),
    )
    collapse_data(bind, src, merge, confirmed, os.getenv("ORG_NAME", "Dalton First United Methodist Church"))

    # 4. drop the tenant column everywhere (PostgreSQL drops the indexes, foreign
    # keys, and multi-column constraints that include it)
    for table in TENANT_TABLES:
        op.drop_column(table, "church_id")
    op.drop_table("churches")

    # per-church uniqueness becomes organization-wide uniqueness
    op.create_unique_constraint("uq_crawled_page_url", "crawled_pages", ["url"])
    op.create_unique_constraint("uq_knowledge_pack", "knowledge_pack_states", ["pack_key"])
    op.create_unique_constraint("uq_knowledge_item", "knowledge_checklist_states", ["item_key"])
    op.create_unique_constraint("uq_usage_daily_bucket", "usage_daily",
                                ["day", "surface", "model"])


def downgrade():
    raise RuntimeError(
        "single_organization cannot be reversed. Restore the pre-migration "
        "backup (docs/RUNBOOK.md#backup-and-restore)."
    )
