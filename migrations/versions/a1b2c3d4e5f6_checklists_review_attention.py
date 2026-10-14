"""checklists, content review, attention snoozes

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-10-14

Phase 3. Adds the event checklist engine (templates, template tasks, checklists, tasks),
the per-piece review state and version history for weekly sermon content, and the
per-person alert snoozes. Purely additive; nothing existing is changed.

Reversible: the downgrade drops exactly what this adds. (Dropping checklists discards
task progress, so take a backup first if any have been used.)
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('checklist_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=True),
    sa.Column('keywords', sa.String(length=500), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('attention_snoozes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('alert_key', sa.String(length=120), nullable=False),
    sa.Column('until', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'alert_key', name='uq_attention_snooze')
    )
    op.create_index(op.f('ix_attention_snoozes_user_id'), 'attention_snoozes', ['user_id'], unique=False)
    op.create_table('checklist_template_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('template_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('owner_role', sa.String(length=30), nullable=True),
    sa.Column('days_before', sa.Integer(), nullable=False),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['template_id'], ['checklist_templates.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_checklist_template_tasks_template_id'), 'checklist_template_tasks', ['template_id'], unique=False)
    op.create_table('checklists',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('template_id', sa.Integer(), nullable=True),
    sa.Column('instance_id', sa.String(length=30), nullable=True),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('event_date', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['template_id'], ['checklist_templates.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instance_id', 'template_id', name='uq_checklist_instance_template')
    )
    op.create_index(op.f('ix_checklists_event_date'), 'checklists', ['event_date'], unique=False)
    op.create_index(op.f('ix_checklists_instance_id'), 'checklists', ['instance_id'], unique=False)
    op.create_index(op.f('ix_checklists_template_id'), 'checklists', ['template_id'], unique=False)
    op.create_table('content_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('packet_id', sa.Integer(), nullable=False),
    sa.Column('piece', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('reason', sa.String(length=20), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_content_versions_packet_id'), 'content_versions', ['packet_id'], unique=False)
    op.create_table('checklist_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('checklist_id', sa.Integer(), nullable=False),
    sa.Column('template_task_id', sa.Integer(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.Column('owner_role', sa.String(length=30), nullable=True),
    sa.Column('assigned_user_id', sa.Integer(), nullable=True),
    sa.Column('days_before', sa.Integer(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('due_overridden', sa.Boolean(), nullable=False),
    sa.Column('catch_up', sa.Boolean(), nullable=False),
    sa.Column('done_at', sa.DateTime(), nullable=True),
    sa.Column('done_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['checklist_id'], ['checklists.id'], ),
    sa.ForeignKeyConstraint(['done_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_checklist_tasks_assigned_user_id'), 'checklist_tasks', ['assigned_user_id'], unique=False)
    op.create_index(op.f('ix_checklist_tasks_checklist_id'), 'checklist_tasks', ['checklist_id'], unique=False)
    op.create_index(op.f('ix_checklist_tasks_due_date'), 'checklist_tasks', ['due_date'], unique=False)
    op.create_table('content_reviews',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('packet_id', sa.Integer(), nullable=False),
    sa.Column('piece', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=14), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('decided_by_id', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['packet_id'], ['sermon_packets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('packet_id', 'piece', name='uq_content_review_piece')
    )
    op.create_index(op.f('ix_content_reviews_packet_id'), 'content_reviews', ['packet_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_content_reviews_packet_id'), table_name='content_reviews')
    op.drop_table('content_reviews')
    op.drop_index(op.f('ix_checklist_tasks_due_date'), table_name='checklist_tasks')
    op.drop_index(op.f('ix_checklist_tasks_checklist_id'), table_name='checklist_tasks')
    op.drop_index(op.f('ix_checklist_tasks_assigned_user_id'), table_name='checklist_tasks')
    op.drop_table('checklist_tasks')
    op.drop_index(op.f('ix_content_versions_packet_id'), table_name='content_versions')
    op.drop_table('content_versions')
    op.drop_index(op.f('ix_checklists_template_id'), table_name='checklists')
    op.drop_index(op.f('ix_checklists_instance_id'), table_name='checklists')
    op.drop_index(op.f('ix_checklists_event_date'), table_name='checklists')
    op.drop_table('checklists')
    op.drop_index(op.f('ix_checklist_template_tasks_template_id'), table_name='checklist_template_tasks')
    op.drop_table('checklist_template_tasks')
    op.drop_index(op.f('ix_attention_snoozes_user_id'), table_name='attention_snoozes')
    op.drop_table('attention_snoozes')
    op.drop_table('checklist_templates')
