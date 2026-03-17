from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import uuid

db = SQLAlchemy()


class Conversation(db.Model):
    __tablename__ = "conversations"
    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SystemPrompt(db.Model):
    """Single-row table (id=1) holding the master Wesley AI system prompt."""
    __tablename__ = "system_prompts"
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Church(db.Model):
    __tablename__ = "churches"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    website_url = db.Column(db.String(500), nullable=True)
    last_crawled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Branding / customisation
    bot_name = db.Column(db.String(100), nullable=False, default="Wesley")
    welcome_message = db.Column(db.String(500), nullable=False, default="How can I help you today?")
    primary_color = db.Column(db.String(7), nullable=False, default="#0a3d3d")
    church_city = db.Column(db.String(200), nullable=True)
    starter_questions = db.Column(db.Text, nullable=True)  # JSON-encoded list of strings
    bot_subtitle = db.Column(db.String(200), nullable=True)

    # Onboarding
    onboarding_complete = db.Column(db.Boolean, nullable=False, default=False)

    # Features
    comms_enabled = db.Column(db.Boolean, nullable=False, default=True)

    # Billing
    trial_ends_at          = db.Column(db.DateTime, nullable=True)
    stripe_subscription_id = db.Column(db.String(200), nullable=True)
    stripe_customer_id     = db.Column(db.String(200), nullable=True)
    billing_exempt         = db.Column(db.Boolean, nullable=False, default=False)
    plan                   = db.Column(db.String(20), nullable=False, default="founders")
    trial_reminder_sent    = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def is_active(self) -> bool:
        """True while the trial is running OR an active Stripe subscription exists.
        Treats trial_ends_at=None as active (safety — avoids accidental lockouts
        if the column is somehow absent on a row).
        """
        if self.trial_ends_at is None:
            return True
        if self.trial_ends_at > datetime.utcnow():
            return True
        if self.stripe_subscription_id:
            return True
        return False

    users = db.relationship("User", backref="church", lazy=True)
    documents = db.relationship("Document", backref="church", lazy=True)
    crawled_pages = db.relationship("CrawledPage", backref="church", lazy=True,
                                    cascade="all, delete-orphan")
    conversations = db.relationship("Conversation", backref="church", lazy=True,
                                    cascade="all, delete-orphan")
    widget_conversations = db.relationship("WidgetConversation", backref="church", lazy=True,
                                           cascade="all, delete-orphan")
    comms_requests = db.relationship("CommsRequest", backref="church", lazy=True,
                                     cascade="all, delete-orphan")


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Role: "admin" (church owner) or "staff" (invited member)
    role = db.Column(db.String(20), nullable=False, default="admin")

    # Password reset
    reset_token         = db.Column(db.String(100), nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
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
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    url = db.Column(db.String(1000), nullable=False)
    title = db.Column(db.String(500), nullable=True)
    content = db.Column(db.Text, nullable=True)
    crawled_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("church_id", "url", name="uq_church_url"),
    )


class WidgetConversation(db.Model):
    """A visitor conversation started from the embeddable website widget."""
    __tablename__ = "widget_conversations"
    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CommsRequest(db.Model):
    __tablename__ = "comms_requests"
    id                   = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    church_id            = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
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


class Invite(db.Model):
    """A pending invitation for a staff member to join a church account."""
    __tablename__ = "invites"
    id         = db.Column(db.Integer, primary_key=True)
    church_id  = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False, index=True)
    email      = db.Column(db.String(200), nullable=False)
    token      = db.Column(db.String(100), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accepted   = db.Column(db.Boolean, nullable=False, default=False)
