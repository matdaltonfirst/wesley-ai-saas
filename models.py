from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import uuid

db = SQLAlchemy()


class Conversation(db.Model):
    __tablename__ = "conversations"
    id = db.Column(db.Integer, primary_key=True)
    # Whose chat this is. Only that person can read it back.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    title = db.Column(db.String(100), nullable=False, default="New Conversation")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = db.relationship(
        "Message", backref="conversation", lazy=True,
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)   # "user" or "assistant"
    content = db.Column(db.Text, nullable=False)
    sources = db.Column(db.Text, nullable=True)       # JSON-encoded citation list
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SystemPrompt(db.Model):
    """Single-row table (id=1) holding the master Wesley AI system prompt."""
    __tablename__ = "system_prompts"
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Organization(db.Model):
    """The one organization this app serves: Dalton First United Methodist Church.

    A single row (id=1) holding everything that used to be per-tenant: name,
    branding, timezone, local practice, feature flags and integration settings.
    Its theology is fixed (Wesleyan United Methodist, see ``denominations``), so
    there is deliberately no denomination column.
    """
    __tablename__ = "organization"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    website_url = db.Column(db.String(500), nullable=True)
    last_crawled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Branding of the public website chatbot
    bot_name = db.Column(db.String(100), nullable=False, default="Wesley")
    welcome_message = db.Column(db.String(500), nullable=False, default="How can I help you today?")
    primary_color = db.Column(db.String(7), nullable=False, default="#0a3d3d")
    church_city = db.Column(db.String(200), nullable=True)
    starter_questions = db.Column(db.Text, nullable=True)  # JSON-encoded list of strings
    bot_subtitle = db.Column(db.String(200), nullable=True)

    # Validated JSON object of structured local-practice settings; schema and
    # server-side validation live in denominations/local_practice.py.
    local_practices = db.Column(db.Text, nullable=True)
    # The church's own approved statement of faith (local content).
    statement_of_faith = db.Column(db.Text, nullable=True)

    # JSON object of feature flags, e.g. {"comms": true}. See organization.feature_enabled.
    features = db.Column(db.Text, nullable=True)

    digest_last_sent_at = db.Column(db.DateTime, nullable=True)

    # IANA timezone for church-local dates (falls back to DEFAULT_TIMEZONE)
    timezone = db.Column(db.String(50), nullable=True)

    # The id the live website embed passes as data-church-id. The public
    # endpoints still accept it so the embed code does not have to change.
    legacy_widget_id = db.Column(db.Integer, nullable=True)


class User(UserMixin, db.Model):
    """A member of staff. Signs in with Google Workspace (see routes/auth.py).

    What a person may do comes from their roles (``UserRole``) and the permission
    matrix (``permissions.py``), never from a flag on this row.
    """
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    display_name = db.Column(db.String(200), nullable=True)
    # Google's stable account id, recorded on first sign-in. Email can change;
    # this does not.
    google_sub = db.Column(db.String(100), unique=True, nullable=True)
    # Only used while password sign-in is still enabled (Google not configured).
    password_hash = db.Column(db.String(300), nullable=True)
    # Deactivated people keep their history but cannot sign in.
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    # Password reset (password mode only)
    reset_token         = db.Column(db.String(100), nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)

    role_rows = db.relationship("UserRole", backref="user", cascade="all, delete-orphan",
                                lazy="selectin")

    @property
    def is_active(self):  # Flask-Login: inactive users cannot stay signed in
        return bool(self.active)

    @property
    def roles(self) -> set:
        return {r.role for r in self.role_rows}

    def has_role(self, role: str) -> bool:
        return role in self.roles


class UserRole(db.Model):
    """One role held by one person. A person may hold several."""
    __tablename__ = "user_roles"
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    role    = db.Column(db.String(40), nullable=False)

    __table_args__ = (db.UniqueConstraint("user_id", "role", name="uq_user_role"),)


class RolePermission(db.Model):
    """An admin override of the default permission matrix for one role."""
    __tablename__ = "role_permissions"
    id         = db.Column(db.Integer, primary_key=True)
    role       = db.Column(db.String(40), nullable=False)
    permission = db.Column(db.String(60), nullable=False)
    allowed    = db.Column(db.Boolean, nullable=False)
    updated_by = db.Column(db.String(200), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("role", "permission", name="uq_role_permission"),)


class AuditLog(db.Model):
    """Who did or accessed what, and when. Append-only: nothing edits or prunes it."""
    __tablename__ = "audit_log"
    id      = db.Column(db.Integer, primary_key=True)
    at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=True)            # not an FK: the log outlives users
    email   = db.Column(db.String(200), nullable=True)
    action  = db.Column(db.String(60), nullable=False, index=True)   # e.g. ai.chat, auth.login
    source  = db.Column(db.String(200), nullable=True)        # which data source or object
    detail  = db.Column(db.Text, nullable=True)               # JSON, no message text
    ip      = db.Column(db.String(60), nullable=True)


class RateLimitHit(db.Model):
    """Shared request counters, so limits hold across workers and restarts."""
    __tablename__ = "rate_limit_hits"
    id     = db.Column(db.Integer, primary_key=True)
    key    = db.Column(db.String(120), nullable=False)
    window = db.Column(db.BigInteger, nullable=False)          # start of the window, epoch seconds
    count  = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (db.UniqueConstraint("key", "window", name="uq_rate_limit_window"),)


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(300), nullable=False)       # UUID-based stored name
    original_name = db.Column(db.String(300), nullable=False)  # user-visible display name
    size_bytes = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    # "staff_only" = internal use only; "staff_and_chatbot" = also sent to widget chat
    visibility = db.Column(db.String(20), nullable=False, default="staff_only")


class CrawledPage(db.Model):
    """Stores scraped content from a church's public website."""
    __tablename__ = "crawled_pages"
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(1000), nullable=False)
    title = db.Column(db.String(500), nullable=True)
    content = db.Column(db.Text, nullable=True)
    crawled_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("url", name="uq_crawled_page_url"),
    )


class WidgetConversation(db.Model):
    """A visitor conversation started from the embeddable website widget."""
    __tablename__ = "widget_conversations"
    id = db.Column(db.Integer, primary_key=True)
    # Random UUID generated on the visitor's first message; groups messages
    # belonging to one browser session together.
    session_id = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = db.relationship(
        "WidgetMessage", backref="widget_conversation", lazy=True,
        cascade="all, delete-orphan",
        order_by="WidgetMessage.created_at",
    )


class WidgetMessage(db.Model):
    """A single message inside a WidgetConversation."""
    __tablename__ = "widget_messages"
    id = db.Column(db.Integer, primary_key=True)
    widget_conversation_id = db.Column(
        db.Integer, db.ForeignKey("widget_conversations.id"), nullable=False, index=True
    )
    role = db.Column(db.String(20), nullable=False)   # "user" or "assistant"
    content = db.Column(db.Text, nullable=False)
    sources = db.Column(db.Text, nullable=True)       # JSON-encoded citation list
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    feedback = db.relationship(
        "AnswerFeedback", backref="widget_message", uselist=False,
        cascade="all, delete-orphan",
    )


class AnswerFeedback(db.Model):
    """Visitor rating and staff correction state for a widget answer."""
    __tablename__ = "answer_feedback"
    id = db.Column(db.Integer, primary_key=True)
    widget_message_id = db.Column(
        db.Integer, db.ForeignKey("widget_messages.id"), nullable=False, unique=True, index=True
    )
    rating = db.Column(db.String(20), nullable=False)  # helpful | not_helpful
    reason = db.Column(db.String(40), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open | corrected | dismissed
    corrected_answer = db.Column(db.Text, nullable=True)
    qna_pair_id = db.Column(db.Integer, db.ForeignKey("qna_pairs.id"), nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)


class CommsRequest(db.Model):
    __tablename__ = "comms_requests"
    id                   = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    submitter_id         = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    submitter_name       = db.Column(db.String(200), nullable=False)
    ministry_department  = db.Column(db.String(100))
    request_type         = db.Column(db.String(20), nullable=False)   # graphic | video
    event_name           = db.Column(db.String(200), nullable=False)
    event_date           = db.Column(db.Date, nullable=False)
    target_audience      = db.Column(db.String(50), nullable=False)   # community | church_members | small_group
    timeline             = db.Column(db.String(50), nullable=False)   # this_week | 2_4_weeks | 1_plus_month
    deliverables         = db.Column(db.JSON, nullable=False)         # list of strings
    key_info_text        = db.Column(db.Text)
    special_notes        = db.Column(db.Text)
    status               = db.Column(db.String(20), default="in_queue")  # in_queue | in_progress | completed | cancelled
    triage_code          = db.Column(db.String(20))                   # red | yellow | green | blue
    production_tier      = db.Column(db.Integer)                      # 1 | 2 | 3
    estimated_completion = db.Column(db.String(50))
    triage_explanation   = db.Column(db.Text)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at         = db.Column(db.DateTime)


class GuestConnection(db.Model):
    __tablename__ = "guest_connections"
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(200), nullable=False)
    email           = db.Column(db.String(200), nullable=False)
    phone           = db.Column(db.String(50))
    interest_area   = db.Column(db.String(100))
    opening_message = db.Column(db.Text)
    status          = db.Column(db.String(20), nullable=False, default="new")  # new | contacted | connected
    notes           = db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    # Planning Center sync state
    pco_person_id  = db.Column(db.String(50), nullable=True)
    pco_synced_at  = db.Column(db.DateTime, nullable=True)
    pco_sync_error = db.Column(db.String(500), nullable=True)
    pco_sync_status = db.Column(db.String(20), nullable=True)  # pending | syncing | partial | synced | failed
    pco_sync_attempts = db.Column(db.Integer, nullable=False, default=0)
    pco_next_retry_at = db.Column(db.DateTime, nullable=True)
    pco_sync_started_at = db.Column(db.DateTime, nullable=True)
    pco_email_synced = db.Column(db.Boolean, nullable=False, default=False)
    pco_phone_synced = db.Column(db.Boolean, nullable=False, default=False)
    pco_note_synced = db.Column(db.Boolean, nullable=False, default=False)
    pco_workflow_synced = db.Column(db.Boolean, nullable=False, default=False)


class TextSnippet(db.Model):
    """Short text blurbs that supplement uploaded docs in the bot's context."""
    __tablename__ = "text_snippets"
    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(200), nullable=False)
    content    = db.Column(db.Text, nullable=False)
    category   = db.Column(db.String(100), nullable=True)
    # "public" content may be given to the website chatbot; "staff" never is.
    audience   = db.Column(db.String(10), nullable=False, default="public")
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QnAPair(db.Model):
    """Staff-written Q&A pairs injected verbatim into the bot's context."""
    __tablename__ = "qna_pairs"
    id         = db.Column(db.Integer, primary_key=True)
    question   = db.Column(db.String(500), nullable=False)
    answer     = db.Column(db.Text, nullable=False)
    audience   = db.Column(db.String(10), nullable=False, default="public")
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgePackState(db.Model):
    """A church's activation state for one built-in knowledge pack."""
    __tablename__ = "knowledge_pack_states"
    id         = db.Column(db.Integer, primary_key=True)
    pack_key   = db.Column(db.String(80), nullable=False)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("pack_key", name="uq_knowledge_pack"),
    )


class KnowledgeChecklistState(db.Model):
    """Church-specific progress and source link for a built-in checklist item."""
    __tablename__ = "knowledge_checklist_states"
    id          = db.Column(db.Integer, primary_key=True)
    item_key    = db.Column(db.String(100), nullable=False)
    status      = db.Column(db.String(20), nullable=False, default="missing")
    source_type = db.Column(db.String(30), nullable=True)
    source_id   = db.Column(db.Integer, nullable=True)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("item_key", name="uq_knowledge_item"),
    )


class ContentProfile(db.Model):
    """One church's house style for generated content (one row per church).

    The separation this enforces matters: how a sermon is turned into a title,
    a chapter list or a post is universal, but *which* title strategy, which
    platforms and which voice are that church's own. Hard-coding one church's
    answers — ALL-CAPS question titles, say — would impose its channel strategy
    on every other congregation as surely as imposing its doctrine would.

    Every column is nullable. An unset profile resolves to a neutral default,
    so a church that has configured nothing still gets usable output.
    """
    __tablename__ = "content_profiles"
    id             = db.Column(db.Integer, primary_key=True)
    # Free text in the church's own words — seeded from their website copy at
    # onboarding, refined by staff.
    voice_notes    = db.Column(db.Text, nullable=True)
    title_strategy = db.Column(db.String(40), nullable=True)   # see content/strategies
    platforms      = db.Column(db.String(200), nullable=True)  # comma-separated
    hashtags       = db.Column(db.String(300), nullable=True)
    call_to_action = db.Column(db.String(300), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SermonPacket(db.Model):
    """The content harvested from one sermon: the Monday packet.

    Stored rather than regenerated so staff edits survive, and so the Monday
    email and the dashboard show the same thing.
    """
    __tablename__ = "sermon_packets"
    id           = db.Column(db.Integer, primary_key=True)
    sermon_id    = db.Column(
        db.Integer, db.ForeignKey("sermons.id"), nullable=False, unique=True, index=True,
    )
    # JSON: {"youtube": {...}, "quotes": [...], "social": [...]}
    content      = db.Column(db.Text, nullable=True)
    status       = db.Column(db.String(20), nullable=False, default="pending")
    # pending | ready | failed
    error        = db.Column(db.String(500), nullable=True)
    emailed_at   = db.Column(db.DateTime, nullable=True)
    generated_at = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class EmbeddingCache(db.Model):
    """A content-addressed cache of text embeddings.

    Deliberately holds no tenant data: it is a pure function cache keyed by the
    hash of the text, the model, and the task type, so identical text is only
    ever embedded once no matter which church supplied it. Nothing here can be
    queried "for church X" — retrieval scores the chunks a caller already
    loaded, and only uses this to avoid paying for the same vector twice.
    """
    __tablename__ = "embedding_cache"
    id         = db.Column(db.Integer, primary_key=True)
    text_hash  = db.Column(db.String(64), nullable=False, index=True)
    model      = db.Column(db.String(60), nullable=False)
    task       = db.Column(db.String(30), nullable=False)
    dim        = db.Column(db.Integer, nullable=False)
    # float32 values, L2-normalised at write time so similarity is a plain
    # dot product rather than a cosine computed on every comparison.
    vector     = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("text_hash", "model", "task", name="uq_embedding_cache_key"),
    )


class UsageDaily(db.Model):
    """AI consumption for a single day, surface, and model.

    Aggregated at write time rather than stored per call: a busy church
    produces a handful of rows a day instead of thousands, which keeps the
    table small enough to query directly on SQLite.
    """
    __tablename__ = "usage_daily"
    id              = db.Column(db.Integer, primary_key=True)
    day             = db.Column(db.Date, nullable=False, index=True)
    surface         = db.Column(db.String(20), nullable=False)   # staff | widget
    model           = db.Column(db.String(60), nullable=False)
    calls           = db.Column(db.Integer, nullable=False, default=0)
    prompt_tokens   = db.Column(db.Integer, nullable=False, default=0)
    response_tokens = db.Column(db.Integer, nullable=False, default=0)
    total_tokens    = db.Column(db.Integer, nullable=False, default=0)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("day", "surface", "model", name="uq_usage_daily_bucket"),
    )


class ChurchCalendar(db.Model):
    """A public ICS calendar feed (Google Calendar, Planning Center, etc.)."""
    __tablename__ = "church_calendars"
    id              = db.Column(db.Integer, primary_key=True)
    url             = db.Column(db.String(1000), nullable=False)
    label           = db.Column(db.String(200), nullable=False, default="Church calendar")
    # A "staff" calendar feeds staff tools only; the website chatbot never sees it.
    audience        = db.Column(db.String(10), nullable=False, default="public")
    last_fetched_at = db.Column(db.DateTime, nullable=True)
    last_error      = db.Column(db.String(500), nullable=True)
    event_count     = db.Column(db.Integer, nullable=False, default=0)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    events = db.relationship(
        "CalendarEvent", backref="calendar", cascade="all, delete-orphan",
    )


class CalendarEvent(db.Model):
    """One occurrence of a calendar event, expanded from the ICS feed.

    Times are stored as the event's local wall-clock time (what a visitor
    would read on a poster), not UTC.
    """
    __tablename__ = "calendar_events"
    id          = db.Column(db.Integer, primary_key=True)
    calendar_id = db.Column(
        db.Integer, db.ForeignKey("church_calendars.id"), nullable=False, index=True,
    )
    title       = db.Column(db.String(500), nullable=False)
    location    = db.Column(db.String(500), nullable=True)
    description = db.Column(db.Text, nullable=True)
    starts_at   = db.Column(db.DateTime, nullable=False, index=True)
    ends_at     = db.Column(db.DateTime, nullable=True)
    all_day     = db.Column(db.Boolean, nullable=False, default=False)


class PcoConnection(db.Model):
    """A church's Planning Center OAuth connection (one per church)."""
    __tablename__ = "pco_connections"
    id                = db.Column(db.Integer, primary_key=True)
    access_token      = db.Column(db.String(1000), nullable=False)
    refresh_token     = db.Column(db.String(1000), nullable=False)
    token_expires_at  = db.Column(db.DateTime, nullable=False)
    organization_name = db.Column(db.String(200), nullable=True)
    auto_sync         = db.Column(db.Boolean, nullable=False, default=True)
    workflow_id       = db.Column(db.String(50), nullable=True)
    workflow_name     = db.Column(db.String(200), nullable=True)
    connected_by_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)


class SermonSource(db.Model):
    """A church's YouTube channel used for sermon ingestion (one per church)."""
    __tablename__ = "sermon_sources"
    id              = db.Column(db.Integer, primary_key=True)
    channel_url     = db.Column(db.String(500), nullable=False)
    channel_id      = db.Column(db.String(100), nullable=False)
    channel_title   = db.Column(db.String(200), nullable=True)
    last_checked_at = db.Column(db.DateTime, nullable=True)
    last_error      = db.Column(db.String(500), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    sermons = db.relationship(
        "Sermon", backref="source", cascade="all, delete-orphan",
    )


class Sermon(db.Model):
    """One ingested sermon video: transcript (when available) plus distilled notes."""
    __tablename__ = "sermons"
    id           = db.Column(db.Integer, primary_key=True)
    source_id    = db.Column(
        db.Integer, db.ForeignKey("sermon_sources.id"), nullable=False, index=True,
    )
    video_id     = db.Column(db.String(20), nullable=False, index=True)
    title        = db.Column(db.String(500), nullable=False)
    published_at = db.Column(db.DateTime, nullable=False)
    transcript   = db.Column(db.Text, nullable=True)      # from captions, when available
    # Caption pieces with their start times, as JSON [{"start": 12.4, "text": …}].
    # The flat transcript above stays the retrieval surface; this exists because
    # chapters and clip-worthy pull quotes are worthless without timing, and the
    # caption API's timings are discarded when the text is joined.
    # Null for sermons transcribed by the video fallback, which has no timings.
    transcript_segments = db.Column(db.Text, nullable=True)
    summary      = db.Column(db.Text, nullable=True)      # distilled recap for retrieval
    main_points  = db.Column(db.Text, nullable=True)      # newline-separated
    scriptures   = db.Column(db.String(500), nullable=True)
    series       = db.Column(db.String(200), nullable=True)
    status       = db.Column(db.String(20), nullable=False, default="pending")
    # pending | ingested | failed
    error        = db.Column(db.String(500), nullable=True)
    ingested_at  = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def video_url(self):
        return f"https://www.youtube.com/watch?v={self.video_id}"
