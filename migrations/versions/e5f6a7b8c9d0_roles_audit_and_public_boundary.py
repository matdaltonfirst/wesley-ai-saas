"""roles, permissions, audit log, shared rate limits, and the public/staff audience

Revision ID: e5f6a7b8c9d0
Revises: c3a7f1d20b44
Create Date: 2026-10-07

Phase 1b. Adds:

  * user_roles, role_permissions: who holds which role; admin overrides of the matrix
  * audit_log: append-only record of sign-ins, role changes, denied access, AI access
  * rate_limit_hits: request counters shared across workers
  * users: display_name, google_sub, active, last_login_at; password_hash may be empty
  * conversations.user_id: staff chats now belong to the person who had them
  * audience ('public' | 'staff') on text_snippets, qna_pairs, church_calendars;
    everything that exists today stays 'public', which is how it has always behaved

Removes users.role (replaced by user_roles) and the invites table (people are now
added by an admin and sign in with Google).

Role seeding, from the church's own answers on 7 Oct 2026:
  mat@daltonfumc.com      admin + comms
  carey@daltonfumc.com    admin_assistant   (Carrie Ashcraft; confirm the address)
  kids@daltonfumc.com     family            (Family Ministries)
  matthew@daltonfumc.com  music             (Matthew Dean)
Anyone else keeps their account with no role until an admin assigns one.
The senior pastor (pastoral) has no address yet and is added by an admin later.

Not reversible: restore the pre-deploy backup (docs/RUNBOOK.md).
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "c3a7f1d20b44"
branch_labels = None
depends_on = None

SEED_ROLES = {
    "mat@daltonfumc.com": ["admin", "comms"],
    "carey@daltonfumc.com": ["admin_assistant"],
    "kids@daltonfumc.com": ["family"],
    "matthew@daltonfumc.com": ["music"],
}


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.UniqueConstraint("user_id", "role", name="uq_user_role"),
    )
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"])
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("permission", sa.String(60), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(200)),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("role", "permission", name="uq_role_permission"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer()),
        sa.Column("email", sa.String(200)),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("source", sa.String(200)),
        sa.Column("detail", sa.Text()),
        sa.Column("ip", sa.String(60)),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("window", sa.BigInteger(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("key", "window", name="uq_rate_limit_window"),
    )

    op.add_column("users", sa.Column("display_name", sa.String(200)))
    op.add_column("users", sa.Column("google_sub", sa.String(100)))
    op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime()))
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])
    op.alter_column("users", "password_hash", existing_type=sa.String(300), nullable=True)

    op.add_column("conversations", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")))
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    for table in ("text_snippets", "qna_pairs", "church_calendars"):
        op.add_column(table, sa.Column("audience", sa.String(10), nullable=False, server_default="public"))

    # Roles: the one existing admin keeps admin; the named people get their role.
    admins = bind.execute(sa.text("SELECT id FROM users WHERE role = 'admin'")).scalars().all()
    for uid in admins:
        bind.execute(sa.text("INSERT INTO user_roles (user_id, role) VALUES (:u, 'admin')"), {"u": uid})
    for email, roles in SEED_ROLES.items():
        uid = bind.execute(sa.text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar()
        if uid is None:
            continue
        for role in roles:
            exists = bind.execute(sa.text("SELECT 1 FROM user_roles WHERE user_id=:u AND role=:r"),
                                  {"u": uid, "r": role}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO user_roles (user_id, role) VALUES (:u, :r)"),
                             {"u": uid, "r": role})

    op.drop_column("users", "role")
    op.drop_table("invites")


def downgrade():
    raise RuntimeError(
        "roles_audit_and_public_boundary cannot be reversed. Restore the pre-deploy "
        "backup (docs/RUNBOOK.md#backup-and-restore)."
    )
