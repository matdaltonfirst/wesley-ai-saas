import os
import re
import uuid
import threading
from datetime import datetime, timedelta
from pathlib import Path

import click
from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from google import genai
from google.genai import types
from sqlalchemy import text, inspect as sa_inspect
import pdfplumber
from docx import Document as DocxDocument
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from models import db, User, Church, Document, SystemPrompt, CrawledPage, Conversation, Message

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.getenv("DATA_DIR", "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_UPLOAD_MB = 32

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATA_DIR / 'wesley.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login_page"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required."}), 401
    return redirect(url_for("login_page"))


# ── Super admin + default prompt ──────────────────────────────────────────────

SUPER_ADMIN_EMAIL = "info@wesleyai.co"

DEFAULT_SYSTEM_PROMPT = (
    "You are Wesley, a helpful AI assistant for United Methodist churches. "
    "You are grounded in Wesleyan theology and United Methodist doctrine. "
    "You speak with warmth, grace, and pastoral care. "
    "You never contradict UMC doctrine. "
    "For deep theological or personal questions you always encourage the user "
    "to speak with their pastor."
)


def is_super_admin() -> bool:
    return current_user.is_authenticated and current_user.email == SUPER_ADMIN_EMAIL


with app.app_context():
    db.create_all()
    print("db.create_all() completed — all tables present.")

    # Inline migration: add Phase 2 columns to existing churches table if absent
    insp = sa_inspect(db.engine)
    existing_cols = {c["name"] for c in insp.get_columns("churches")}
    with db.engine.connect() as conn:
        if "website_url" not in existing_cols:
            conn.execute(text("ALTER TABLE churches ADD COLUMN website_url VARCHAR(500)"))
            conn.commit()
            print("Migration: added churches.website_url")
        if "last_crawled_at" not in existing_cols:
            conn.execute(text("ALTER TABLE churches ADD COLUMN last_crawled_at DATETIME"))
            conn.commit()
            print("Migration: added churches.last_crawled_at")

    # Inline migration: branding columns added to churches table
    insp2 = sa_inspect(db.engine)
    existing_cols2 = {c["name"] for c in insp2.get_columns("churches")}
    with db.engine.connect() as conn2:
        if "bot_name" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN bot_name VARCHAR(100) NOT NULL DEFAULT 'Wesley'"))
            conn2.commit()
            print("Migration: added churches.bot_name")
        if "welcome_message" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN welcome_message VARCHAR(500) NOT NULL DEFAULT 'How can I help you today?'"))
            conn2.commit()
            print("Migration: added churches.welcome_message")
        if "primary_color" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN primary_color VARCHAR(7) NOT NULL DEFAULT '#0a3d3d'"))
            conn2.commit()
            print("Migration: added churches.primary_color")
        if "church_city" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN church_city VARCHAR(200)"))
            conn2.commit()
            print("Migration: added churches.church_city")

    # Seed the master system prompt on first run (id=1 is the single canonical row)
    if not SystemPrompt.query.get(1):
        db.session.add(SystemPrompt(id=1, content=DEFAULT_SYSTEM_PROMPT))
        db.session.commit()
        print("System prompt seeded with default.")

# ── Flask CLI commands ────────────────────────────────────────────────────────


@app.cli.command("init-db")
def init_db_command():
    """Explicitly create all database tables. Safe to run on an existing DB."""
    db.create_all()
    click.echo("init-db: all tables created (or already exist).")
    # Report which tables are present
    from sqlalchemy import inspect as sa_inspect2
    tables = sa_inspect2(db.engine).get_table_names()
    click.echo(f"init-db: tables in DB → {', '.join(sorted(tables))}")


# ── API key validation ────────────────────────────────────────────────────────

_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    print("WARNING: GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
else:
    print(f"Gemini API key loaded ({_api_key[:8]}…)")

# ── Constants ─────────────────────────────────────────────────────────────────

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "on", "at", "by", "for", "with", "about",
    "against", "between", "into", "through", "during", "before", "after",
    "above", "below", "from", "up", "down", "out", "off", "over", "under",
    "again", "further", "then", "once", "and", "but", "or", "nor", "so",
    "yet", "both", "either", "neither", "not", "only", "own", "same",
    "than", "too", "very", "just", "this", "that", "these", "those",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it",
    "his", "her", "its", "they", "them", "their", "what", "which", "who",
    "whom", "how", "when", "where", "why", "all", "each", "every", "any",
    "no", "more", "most", "other", "some", "such", "if", "as",
}

# ── Document processing ───────────────────────────────────────────────────────


def get_church_dir(church_id: int) -> Path:
    d = UPLOADS_DIR / str(church_id)
    d.mkdir(exist_ok=True)
    return d


def read_pdf(filepath: Path, display_name: str) -> list[dict]:
    chunks = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text_content = page.extract_text()
                if text_content and text_content.strip():
                    chunks.append({
                        "content": text_content.strip(),
                        "source": display_name,
                        "location": f"Page {page_num}",
                    })
    except Exception as e:
        print(f"Error reading PDF {filepath.name}: {e}")
    return chunks


def read_docx(filepath: Path, display_name: str) -> list[dict]:
    chunks = []
    try:
        doc = DocxDocument(filepath)
        current_heading = "Document"
        current_text: list[str] = []
        current_length = 0

        def flush_chunk():
            nonlocal current_text, current_length
            text_content = " ".join(current_text).strip()
            if text_content:
                chunks.append({
                    "content": text_content,
                    "source": display_name,
                    "location": current_heading,
                })
            current_text = []
            current_length = 0

        for para in doc.paragraphs:
            style = para.style.name.lower()
            para_text = para.text.strip()
            if not para_text:
                continue
            if "heading" in style:
                flush_chunk()
                current_heading = para_text
            else:
                if current_length + len(para_text) > 500 and current_text:
                    flush_chunk()
                current_text.append(para_text)
                current_length += len(para_text)

        flush_chunk()
    except Exception as e:
        print(f"Error reading DOCX {filepath.name}: {e}")
    return chunks


def load_church_documents(church_id: int) -> list[dict]:
    """Load and parse all documents for a church, scoped by church_id."""
    docs = Document.query.filter_by(church_id=church_id).all()
    church_dir = get_church_dir(church_id)
    all_chunks = []
    for doc in docs:
        filepath = church_dir / doc.filename
        if not filepath.exists():
            continue
        suffix = Path(doc.filename).suffix.lower()
        if suffix == ".pdf":
            all_chunks.extend(read_pdf(filepath, doc.original_name))
        elif suffix == ".docx":
            all_chunks.extend(read_docx(filepath, doc.original_name))
    return all_chunks


def load_church_web_content(church_id: int) -> list[dict]:
    """Return keyword-scoreable chunks from a church's crawled web pages."""
    pages = CrawledPage.query.filter_by(church_id=church_id).all()
    chunks = []
    for page in pages:
        if page.content and page.content.strip():
            chunks.append({
                "content": page.content,
                "source": page.title or page.url,
                "location": page.url,
            })
    return chunks


# ── Relevance scoring ─────────────────────────────────────────────────────────


def extract_keywords(query: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z]{3,}\b", query.lower())
    return [w for w in words if w not in STOP_WORDS]


def score_chunk(chunk: dict, keywords: list[str]) -> int:
    content_lower = chunk["content"].lower()
    return sum(content_lower.count(kw) for kw in keywords)


def find_relevant_chunks(query: str, chunks: list[dict], top_n: int = 8) -> list[tuple[int, dict]]:
    keywords = extract_keywords(query)
    if not keywords:
        return []
    scored = [(score_chunk(c, keywords), c) for c in chunks]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(score, c) for score, c in scored[:top_n] if score > 0]


def build_context_block(scored_chunks: list[tuple[int, dict]]) -> str:
    lines = []
    for _, chunk in scored_chunks:
        lines.append(f"[From: {chunk['source']}, {chunk['location']}]")
        lines.append(chunk["content"])
        lines.append("")
    return "\n".join(lines)


# ── Gemini call ───────────────────────────────────────────────────────────────


def _friendly_gemini_error(exc: Exception) -> tuple[str, int]:
    msg = str(exc).lower()
    if "429" in msg or "quota" in msg or "rate" in msg or "exhausted" in msg:
        return ("The AI service is temporarily over its request limit. Please wait and try again.", 429)
    if "401" in msg or "403" in msg or "api_key" in msg:
        return ("API key error — please check that GEMINI_API_KEY is configured correctly.", 401)
    if "404" in msg or "not found" in msg:
        return ("The AI model could not be found. Please check the model name.", 404)
    if "503" in msg or "unavailable" in msg:
        return ("The AI service is temporarily unavailable. Please try again.", 503)
    return (f"AI error: {exc}", 502)


def call_gemini(question: str, context: str, history: list[dict], system_instruction: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")

    client = genai.Client(api_key=api_key)

    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    current_text = (
        f"[Relevant church information:]\n{context}\n---\n{question}"
        if context.strip()
        else question
    )
    contents.append(types.Content(role="user", parts=[types.Part(text=current_text)]))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system_instruction),
    )
    return response.text


# ── Nightly crawl scheduler ───────────────────────────────────────────────────


def nightly_crawl_job():
    """Re-crawl all churches that have a website URL configured. Runs at 2am daily."""
    with app.app_context():
        from crawler import crawl_church_website
        churches = Church.query.filter(Church.website_url.isnot(None)).all()
        print(f"Nightly crawl: found {len(churches)} church(es) to crawl.")
        for church in churches:
            if not church.website_url:
                continue
            try:
                result = crawl_church_website(church.id, church.website_url)
                print(f"Nightly crawl church_id={church.id} ({church.name}): {result}")
            except Exception as exc:
                print(f"Nightly crawl error church_id={church.id}: {exc}")


def nightly_cleanup_job():
    """Delete conversations (and their messages) last updated more than 14 days ago."""
    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(days=14)
        old_convs = Conversation.query.filter(Conversation.updated_at < cutoff).all()
        count = len(old_convs)
        for conv in old_convs:
            db.session.delete(conv)
        db.session.commit()
        print(f"Nightly cleanup: deleted {count} conversation(s) older than 14 days.")


scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(nightly_crawl_job, CronTrigger(hour=2, minute=0))
scheduler.add_job(nightly_cleanup_job, CronTrigger(hour=3, minute=0))
if not scheduler.running:
    scheduler.start()


# ── Auth routes ───────────────────────────────────────────────────────────────


@app.route("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("chat_page"))
    return render_template("auth.html", mode="login")


@app.route("/signup")
def signup_page():
    if current_user.is_authenticated:
        return redirect(url_for("chat_page"))
    return render_template("auth.html", mode="signup")


@app.route("/api/auth/signup", methods=["POST"])
def api_signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    church_name = (data.get("church_name") or "").strip()

    if not email or not password or not church_name:
        return jsonify({"error": "Email, password, and church name are required."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "An account with that email already exists."}), 400

    church = Church(name=church_name)
    db.session.add(church)
    db.session.flush()  # get church.id before commit

    user = User(
        email=email,
        password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        church_id=church.id,
    )
    db.session.add(user)
    db.session.commit()

    login_user(user)
    return jsonify({"ok": True}), 201


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid email or password."}), 401

    login_user(user)
    return jsonify({"ok": True})


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login_page"))


# ── Chat page (main interface) ────────────────────────────────────────────────


@app.route("/")
@login_required
def chat_page():
    church = current_user.church
    return render_template(
        "dashboard.html",
        church_name=church.name,
        user_email=current_user.email,
        bot_name=church.bot_name or "Wesley",
        welcome_message=church.welcome_message or "How can I help you today?",
        primary_color=church.primary_color or "#0a3d3d",
    )


# ── Dashboard (management) ────────────────────────────────────────────────────


@app.route("/dashboard")
@login_required
def management_dashboard():
    church = current_user.church
    return render_template(
        "settings.html",
        church_name=church.name,
        church_id=current_user.church_id,
        user_email=current_user.email,
        bot_name=church.bot_name or "Wesley",
        welcome_message=church.welcome_message or "How can I help you today?",
        primary_color=church.primary_color or "#0a3d3d",
        church_city=church.church_city or "",
    )


# ── Documents API ─────────────────────────────────────────────────────────────


@app.route("/api/documents")
@login_required
def list_documents():
    docs = (
        Document.query
        .filter_by(church_id=current_user.church_id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return jsonify({
        "documents": [
            {
                "id": d.id,
                "name": d.original_name,
                "size_kb": round(d.size_bytes / 1024, 1),
                "type": Path(d.original_name).suffix.lower().lstrip("."),
            }
            for d in docs
        ]
    })


@app.route("/api/documents/upload", methods=["POST"])
@login_required
def upload_document():
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    original_name = file.filename
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only PDF and DOCX files are supported."}), 400

    content = file.read()
    size_bytes = len(content)

    # UUID filename prevents path traversal and collisions
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    church_dir = get_church_dir(current_user.church_id)
    (church_dir / stored_name).write_bytes(content)

    # sanitize display name; fall back to stored name if result is empty
    display_name = secure_filename(original_name) or stored_name

    doc = Document(
        church_id=current_user.church_id,
        filename=stored_name,
        original_name=display_name,
        size_bytes=size_bytes,
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({
        "ok": True,
        "id": doc.id,
        "name": doc.original_name,
        "size_kb": round(size_bytes / 1024, 1),
        "type": suffix.lstrip("."),
    }), 201


@app.route("/api/documents/<int:doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id):
    doc = Document.query.filter_by(id=doc_id, church_id=current_user.church_id).first()
    if not doc:
        return jsonify({"error": "Document not found."}), 404

    filepath = get_church_dir(current_user.church_id) / doc.filename
    if filepath.exists():
        filepath.unlink()

    db.session.delete(doc)
    db.session.commit()
    return jsonify({"ok": True})


# ── Chat API (staff dashboard) ────────────────────────────────────────────────


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True)
    if not data or not data.get("question", "").strip():
        return jsonify({"error": "No question provided"}), 400

    question = data["question"].strip()
    conversation_id = data.get("conversation_id")

    # Resolve or create the conversation
    if conversation_id:
        conv = Conversation.query.filter_by(
            id=conversation_id, church_id=current_user.church_id
        ).first()
        if not conv:
            return jsonify({"error": "Conversation not found."}), 404
    else:
        title = question[:40]
        conv = Conversation(church_id=current_user.church_id, title=title)
        db.session.add(conv)
        db.session.flush()  # get conv.id

    # Save the user message
    db.session.add(Message(conversation_id=conv.id, role="user", content=question))

    # Build history from DB (all previous messages for this conversation)
    history = [
        {"role": m.role, "content": m.content}
        for m in conv.messages
    ]

    # RAG context
    chunks = load_church_documents(current_user.church_id)
    context = ""
    candidate_sources = []

    if chunks:
        scored = find_relevant_chunks(question, chunks)
        if scored:
            context = build_context_block(scored)
            seen: set[tuple] = set()
            for _, chunk in scored:
                key = (chunk["source"], chunk["location"])
                if key not in seen:
                    seen.add(key)
                    candidate_sources.append({"file": chunk["source"], "location": chunk["location"]})

    prompt_row = SystemPrompt.query.get(1)
    base_prompt = prompt_row.content if prompt_row else DEFAULT_SYSTEM_PROMPT

    # Append church-specific identity so the bot knows exactly where it is installed
    church = current_user.church
    church_ctx = f"\n\nYou are installed at {church.name}"
    if church.church_city:
        church_ctx += f", located in {church.church_city}"
    church_ctx += f". Your name is {church.bot_name or 'Wesley'}."
    system_instruction = base_prompt + church_ctx

    try:
        answer = call_gemini(question, context, history, system_instruction)
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        db.session.rollback()
        user_msg, status = _friendly_gemini_error(e)
        return jsonify({"error": user_msg}), status

    # Save the assistant message and touch updated_at
    db.session.add(Message(conversation_id=conv.id, role="assistant", content=answer))
    conv.updated_at = datetime.utcnow()
    db.session.commit()

    answer_lower = answer.lower()
    sources = [s for s in candidate_sources if s["file"].lower() in answer_lower]

    return jsonify({"answer": answer, "sources": sources, "conversation_id": conv.id})


# ── Conversations API ──────────────────────────────────────────────────────────


@app.route("/api/conversations")
@login_required
def list_conversations():
    convs = (
        Conversation.query
        .filter_by(church_id=current_user.church_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return jsonify({
        "conversations": [
            {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
            for c in convs
        ]
    })


@app.route("/api/conversations/<int:conv_id>/messages")
@login_required
def get_conversation_messages(conv_id):
    conv = Conversation.query.filter_by(
        id=conv_id, church_id=current_user.church_id
    ).first()
    if not conv:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify({
        "conversation_id": conv.id,
        "title": conv.title,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in conv.messages
        ],
    })


# ── Church branding API ───────────────────────────────────────────────────────

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@app.route("/api/church/branding", methods=["GET"])
@login_required
def get_church_branding():
    church = current_user.church
    return jsonify({
        "bot_name":       church.bot_name or "Wesley",
        "welcome_message": church.welcome_message or "How can I help you today?",
        "primary_color":  church.primary_color or "#0a3d3d",
        "church_city":    church.church_city or "",
    })


@app.route("/api/church/branding", methods=["POST"])
@login_required
def save_church_branding():
    data = request.get_json(silent=True) or {}
    church = current_user.church

    bot_name = (data.get("bot_name") or "").strip()
    welcome_message = (data.get("welcome_message") or "").strip()
    primary_color = (data.get("primary_color") or "").strip()
    church_city = (data.get("church_city") or "").strip()

    if not bot_name:
        return jsonify({"error": "Bot name cannot be empty."}), 400
    if not welcome_message:
        return jsonify({"error": "Welcome message cannot be empty."}), 400
    if primary_color and not _HEX_COLOR_RE.match(primary_color):
        return jsonify({"error": "Primary color must be a valid hex color (e.g. #1a2b3c)."}), 400

    church.bot_name = bot_name[:100]
    church.welcome_message = welcome_message[:500]
    church.primary_color = primary_color if primary_color else "#0a3d3d"
    church.church_city = church_city[:200] if church_city else None
    db.session.commit()
    return jsonify({"ok": True})


# ── Admin panel ───────────────────────────────────────────────────────────────


@app.route("/admin")
@login_required
def admin_panel():
    if not is_super_admin():
        return render_template("admin.html", forbidden=True), 403
    prompt_row = SystemPrompt.query.get(1)
    current_prompt = prompt_row.content if prompt_row else DEFAULT_SYSTEM_PROMPT
    return render_template(
        "admin.html",
        forbidden=False,
        current_prompt=current_prompt,
        default_prompt=DEFAULT_SYSTEM_PROMPT,
    )


@app.route("/api/admin/system-prompt", methods=["POST"])
@login_required
def update_system_prompt():
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Prompt content cannot be empty."}), 400
    prompt_row = SystemPrompt.query.get(1)
    if prompt_row:
        prompt_row.content = content
    else:
        db.session.add(SystemPrompt(id=1, content=content))
    db.session.commit()
    return jsonify({"ok": True})


# ── Widget JS (public, CORS) ──────────────────────────────────────────────────


@app.route("/widget.js")
def serve_widget():
    resp = make_response(send_from_directory("static", "widget.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


# ── Widget Chat API (public, CORS, crawled content only) ─────────────────────


@app.route("/api/widget/chat", methods=["POST", "OPTIONS"])
def widget_chat():
    # Handle CORS preflight
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    data = request.get_json(silent=True) or {}
    church_id_raw = data.get("church_id")
    question = (data.get("question") or "").strip()
    history = data.get("history", [])

    def cors_err(msg, status=400):
        resp = jsonify({"error": msg})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, status

    if not church_id_raw or not question:
        return cors_err("church_id and question are required.")

    try:
        church_id = int(church_id_raw)
    except (ValueError, TypeError):
        return cors_err("Invalid church_id.")

    church = Church.query.get(church_id)
    if not church:
        return cors_err("Church not found.", 404)

    # Use crawled web content ONLY — never staff documents
    web_chunks = load_church_web_content(church_id)
    context = ""
    if web_chunks:
        scored = find_relevant_chunks(question, web_chunks)
        if scored:
            context = build_context_block(scored)

    prompt_row = SystemPrompt.query.get(1)
    system_instruction = prompt_row.content if prompt_row else DEFAULT_SYSTEM_PROMPT

    try:
        answer = call_gemini(question, context, history, system_instruction)
    except ValueError as e:
        return cors_err(str(e), 500)
    except Exception as e:
        user_msg, status = _friendly_gemini_error(e)
        return cors_err(user_msg, status)

    resp = jsonify({"answer": answer})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ── Church settings API (website URL + crawl stats) ───────────────────────────


@app.route("/api/church/settings", methods=["GET"])
@login_required
def get_church_settings():
    church = current_user.church
    page_count = CrawledPage.query.filter_by(church_id=church.id).count()
    return jsonify({
        "website_url": church.website_url or "",
        "last_crawled_at": church.last_crawled_at.isoformat() if church.last_crawled_at else None,
        "page_count": page_count,
        "church_id": church.id,
    })


@app.route("/api/church/settings", methods=["POST"])
@login_required
def save_church_settings():
    data = request.get_json(silent=True) or {}
    url = (data.get("website_url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400
    current_user.church.website_url = url or None
    db.session.commit()
    return jsonify({"ok": True})


# ── Manual re-crawl (fires background thread) ─────────────────────────────────


@app.route("/api/church/crawl", methods=["POST"])
@login_required
def trigger_crawl():
    church = current_user.church
    if not church.website_url:
        return jsonify({"error": "No website URL configured. Save a URL first."}), 400

    crawl_url  = church.website_url
    church_id  = church.id

    def run_crawl():
        with app.app_context():
            from crawler import crawl_church_website
            result = crawl_church_website(church_id, crawl_url)
            print(f"Manual crawl church_id={church_id}: {result}")

    t = threading.Thread(target=run_crawl, daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Crawl started in the background."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
