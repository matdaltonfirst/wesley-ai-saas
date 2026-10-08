"""connector framework, streaming numbers, and the normalized tables for each connector

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-10-08

Phase 2. Adds the framework tables (integrations, integration_tokens, sync_runs,
raw_payloads), the streaming pane (streaming_numbers and its edit history), and one
small set of normalized tables per connector (YouTube, Facebook/Instagram, Constant
Contact, Text In Church, Planning Center). Purely additive: nothing existing is
changed except one new nullable column, pco_connections.scope.

Reversible: the downgrade drops exactly what this adds. (Dropping streaming_numbers
discards entered numbers, so take a backup first if any have been entered.)
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('email_campaigns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('activity_id', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('sends', sa.Integer(), nullable=True),
    sa.Column('opens', sa.Integer(), nullable=True),
    sa.Column('clicks', sa.Integer(), nullable=True),
    sa.Column('bounces', sa.Integer(), nullable=True),
    sa.Column('optouts', sa.Integer(), nullable=True),
    sa.Column('forwards', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('activity_id')
    )
    op.create_table('email_list_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('list_id', sa.String(length=60), nullable=False),
    sa.Column('list_name', sa.String(length=200), nullable=True),
    sa.Column('members', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('day', 'list_id', name='uq_email_list_snapshot')
    )
    op.create_table('integration_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('access_token', sa.Text(), nullable=False),
    sa.Column('refresh_token', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('scope', sa.String(length=500), nullable=True),
    sa.Column('account_label', sa.String(length=200), nullable=True),
    sa.Column('connected_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('integrations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('config', sa.Text(), nullable=True),
    sa.Column('last_success_at', sa.DateTime(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_error_kind', sa.String(length=20), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('pco_checkin_counts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_time_id', sa.String(length=30), nullable=False),
    sa.Column('event_name', sa.String(length=300), nullable=True),
    sa.Column('starts_at', sa.DateTime(), nullable=True),
    sa.Column('check_ins', sa.Integer(), nullable=True),
    sa.Column('guests', sa.Integer(), nullable=True),
    sa.Column('regulars', sa.Integer(), nullable=True),
    sa.Column('volunteers', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_time_id')
    )
    op.create_index(op.f('ix_pco_checkin_counts_starts_at'), 'pco_checkin_counts', ['starts_at'], unique=False)
    op.create_table('pco_episodes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('episode_id', sa.String(length=30), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('views', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('episode_id')
    )
    op.create_index(op.f('ix_pco_episodes_published_at'), 'pco_episodes', ['published_at'], unique=False)
    op.create_table('pco_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instance_id', sa.String(length=30), nullable=False),
    sa.Column('event_id', sa.String(length=30), nullable=True),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('starts_at', sa.DateTime(), nullable=True),
    sa.Column('ends_at', sa.DateTime(), nullable=True),
    sa.Column('all_day', sa.Boolean(), nullable=False),
    sa.Column('location', sa.String(length=300), nullable=True),
    sa.Column('tags', sa.String(length=300), nullable=True),
    sa.Column('visible_in_church_center', sa.Boolean(), nullable=True),
    sa.Column('updated_remote_at', sa.DateTime(), nullable=True),
    sa.Column('removed_at', sa.DateTime(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instance_id')
    )
    op.create_index(op.f('ix_pco_events_event_id'), 'pco_events', ['event_id'], unique=False)
    op.create_index(op.f('ix_pco_events_starts_at'), 'pco_events', ['starts_at'], unique=False)
    op.create_table('pco_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('group_type', sa.String(length=120), nullable=True),
    sa.Column('members_count', sa.Integer(), nullable=True),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('group_id')
    )
    op.create_table('pco_service_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.String(length=30), nullable=False),
    sa.Column('service_type', sa.String(length=120), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=True),
    sa.Column('series', sa.String(length=300), nullable=True),
    sa.Column('sort_date', sa.DateTime(), nullable=True),
    sa.Column('positions_total', sa.Integer(), nullable=True),
    sa.Column('positions_needed', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plan_id')
    )
    op.create_index(op.f('ix_pco_service_plans_sort_date'), 'pco_service_plans', ['sort_date'], unique=False)
    op.create_table('pco_signups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('signup_id', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('opens_at', sa.DateTime(), nullable=True),
    sa.Column('closes_at', sa.DateTime(), nullable=True),
    sa.Column('capacity', sa.Integer(), nullable=True),
    sa.Column('attendee_count', sa.Integer(), nullable=True),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('signup_id')
    )
    op.create_table('raw_payloads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('integration', sa.String(length=40), nullable=False),
    sa.Column('resource', sa.String(length=60), nullable=False),
    sa.Column('external_id', sa.String(length=120), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=True),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('integration', 'resource', 'external_id', name='uq_raw_payload')
    )
    op.create_table('social_posts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('platform', sa.String(length=12), nullable=False),
    sa.Column('post_id', sa.String(length=60), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('permalink', sa.String(length=500), nullable=True),
    sa.Column('caption', sa.String(length=300), nullable=True),
    sa.Column('reach', sa.Integer(), nullable=True),
    sa.Column('impressions', sa.Integer(), nullable=True),
    sa.Column('engagements', sa.Integer(), nullable=True),
    sa.Column('video_views', sa.Integer(), nullable=True),
    sa.Column('live_views', sa.Integer(), nullable=True),
    sa.Column('watch_seconds', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('platform', 'post_id', name='uq_social_post')
    )
    op.create_table('streaming_number_edits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('number_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('by', sa.String(length=200), nullable=True),
    sa.Column('field', sa.String(length=30), nullable=False),
    sa.Column('old_value', sa.String(length=60), nullable=True),
    sa.Column('new_value', sa.String(length=60), nullable=True),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_streaming_number_edits_number_id'), 'streaming_number_edits', ['number_id'], unique=False)
    op.create_table('streaming_numbers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('service_date', sa.Date(), nullable=False),
    sa.Column('service_label', sa.String(length=60), nullable=False),
    sa.Column('platform', sa.String(length=30), nullable=False),
    sa.Column('peak_concurrent', sa.Integer(), nullable=True),
    sa.Column('total_views', sa.Integer(), nullable=True),
    sa.Column('watch_minutes', sa.Integer(), nullable=True),
    sa.Column('sources', sa.Text(), nullable=True),
    sa.Column('external_id', sa.String(length=120), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('updated_by', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('service_date', 'service_label', 'platform', name='uq_streaming_number')
    )
    op.create_index(op.f('ix_streaming_numbers_service_date'), 'streaming_numbers', ['service_date'], unique=False)
    op.create_table('sync_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('integration', sa.String(length=40), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('trigger', sa.String(length=12), nullable=False),
    sa.Column('rows_fetched', sa.Integer(), nullable=False),
    sa.Column('rows_changed', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('error_kind', sa.String(length=20), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sync_runs_integration'), 'sync_runs', ['integration'], unique=False)
    op.create_index(op.f('ix_sync_runs_started_at'), 'sync_runs', ['started_at'], unique=False)
    op.create_table('tic_connect_cards',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('submission_id', sa.String(length=30), nullable=False),
    sa.Column('contact_id', sa.String(length=30), nullable=True),
    sa.Column('collection', sa.String(length=120), nullable=True),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('submission_id')
    )
    op.create_index(op.f('ix_tic_connect_cards_contact_id'), 'tic_connect_cards', ['contact_id'], unique=False)
    op.create_index(op.f('ix_tic_connect_cards_submitted_at'), 'tic_connect_cards', ['submitted_at'], unique=False)
    op.create_table('tic_contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('contact_id', sa.String(length=30), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=True),
    sa.Column('last_name', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=60), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('optout_sms', sa.Boolean(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('contact_id')
    )
    op.create_table('tic_conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conv_id', sa.String(length=30), nullable=False),
    sa.Column('contact_id', sa.String(length=30), nullable=True),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conv_id')
    )
    op.create_index(op.f('ix_tic_conversations_contact_id'), 'tic_conversations', ['contact_id'], unique=False)
    op.create_table('tic_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('msg_id', sa.String(length=30), nullable=False),
    sa.Column('conv_id', sa.String(length=30), nullable=True),
    sa.Column('incoming', sa.Boolean(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('automated', sa.Boolean(), nullable=False),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('msg_id')
    )
    op.create_index(op.f('ix_tic_messages_conv_id'), 'tic_messages', ['conv_id'], unique=False)
    op.create_index(op.f('ix_tic_messages_sent_at'), 'tic_messages', ['sent_at'], unique=False)
    op.create_table('youtube_videos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('video_id', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('is_live', sa.Boolean(), nullable=False),
    sa.Column('actual_start', sa.DateTime(), nullable=True),
    sa.Column('actual_end', sa.DateTime(), nullable=True),
    sa.Column('views', sa.Integer(), nullable=True),
    sa.Column('likes', sa.Integer(), nullable=True),
    sa.Column('comments', sa.Integer(), nullable=True),
    sa.Column('peak_concurrent', sa.Integer(), nullable=True),
    sa.Column('watch_minutes', sa.Integer(), nullable=True),
    sa.Column('avg_view_seconds', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('video_id')
    )
    op.add_column("pco_connections", sa.Column("scope", sa.String(300), nullable=True))


def downgrade():
    op.drop_column("pco_connections", "scope")
    op.drop_table('youtube_videos')
    op.drop_table('tic_messages')
    op.drop_table('tic_conversations')
    op.drop_table('tic_contacts')
    op.drop_table('tic_connect_cards')
    op.drop_table('sync_runs')
    op.drop_table('streaming_numbers')
    op.drop_table('streaming_number_edits')
    op.drop_table('social_posts')
    op.drop_table('raw_payloads')
    op.drop_table('pco_signups')
    op.drop_table('pco_service_plans')
    op.drop_table('pco_groups')
    op.drop_table('pco_events')
    op.drop_table('pco_episodes')
    op.drop_table('pco_checkin_counts')
    op.drop_table('integrations')
    op.drop_table('integration_tokens')
    op.drop_table('email_list_snapshots')
    op.drop_table('email_campaigns')
