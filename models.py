from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class Conversation(db.Model):
    __tablename__ = "conversations"
    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
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
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False)
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

    # Onboarding
    onboarding_complete = db.Column(db.Boolean, nullable=False, default=False)

    users = db.relationship("User", backref="church", lazy=True)
    documents = db.relationship("Document", backref="church", lazy=True)
    crawled_pages = db.relationship("CrawledPage", backref="church", lazy=True,
                                    cascade="all, delete-orphan")
    conversations = db.relationship("Conversation", backref="church", lazy=True,
                                    cascade="all, delete-orphan")


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
    filename = db.Column(db.String(300), nullable=False)       # UUID-based stored name
    original_name = db.Column(db.String(300), nullable=False)  # user-visible display name
    size_bytes = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class CrawledPage(db.Model):
    """Stores scraped content from a church's public website."""
    __tablename__ = "crawled_pages"
    id = db.Column(db.Integer, primary_key=True)
    church_id = db.Column(db.Integer, db.ForeignKey("churches.id"), nullable=False)
    url = db.Column(db.String(1000), nullable=False)
    title = db.Column(db.String(500), nullable=True)
    content = db.Column(db.Text, nullable=True)
    crawled_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("church_id", "url", name="uq_church_url"),
    )
