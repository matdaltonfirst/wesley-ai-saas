import os
import re
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, render_template, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from google import genai
from google.genai import types
import pdfplumber
from docx import Document as DocxDocument

from models import db, User, Church, Document

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


with app.app_context():
    db.create_all()

# ── API key validation ────────────────────────────────────────────────────────

_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    print("WARNING: GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
else:
    print(f"Gemini API key loaded ({_api_key[:8]}…)")

# ── Constants ─────────────────────────────────────────────────────────────────

SYSTEM_INSTRUCTION = """You are Wesley AI, a knowledgeable and friendly AI assistant for church staff. \
You help with a wide range of tasks — answering questions, drafting communications, \
event planning, pastoral support, and general advice.

When relevant church documents are provided in the conversation context, use them to give \
accurate, grounded answers and cite the source file and page or section.

If documents don't cover the topic, draw on your general knowledge to help — \
you are not limited to document content.

Be warm, professional, and conversational. You are a trusted assistant to the church team."""

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
                text = page.extract_text()
                if text and text.strip():
                    chunks.append({
                        "content": text.strip(),
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
            text = " ".join(current_text).strip()
            if text:
                chunks.append({
                    "content": text,
                    "source": display_name,
                    "location": current_heading,
                })
            current_text = []
            current_length = 0

        for para in doc.paragraphs:
            style = para.style.name.lower()
            text = para.text.strip()
            if not text:
                continue
            if "heading" in style:
                flush_chunk()
                current_heading = text
            else:
                if current_length + len(text) > 500 and current_text:
                    flush_chunk()
                current_text.append(text)
                current_length += len(text)

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


def call_gemini(question: str, context: str, history: list[dict]) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")

    client = genai.Client(api_key=api_key)

    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    current_text = (
        f"[Relevant church documents:]\n{context}\n---\n{question}"
        if context.strip()
        else question
    )
    contents.append(types.Content(role="user", parts=[types.Part(text=current_text)]))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
    )
    return response.text


# ── Auth routes ───────────────────────────────────────────────────────────────


@app.route("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="login")


@app.route("/signup")
def signup_page():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
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


# ── Dashboard ─────────────────────────────────────────────────────────────────


@app.route("/")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        church_name=current_user.church.name,
        user_email=current_user.email,
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


# ── Chat API ──────────────────────────────────────────────────────────────────


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True)
    if not data or not data.get("question", "").strip():
        return jsonify({"error": "No question provided"}), 400

    question = data["question"].strip()
    history = data.get("history", [])

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

    try:
        answer = call_gemini(question, context, history)
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        user_msg, status = _friendly_gemini_error(e)
        return jsonify({"error": user_msg}), status

    answer_lower = answer.lower()
    sources = [s for s in candidate_sources if s["file"].lower() in answer_lower]

    return jsonify({"answer": answer, "sources": sources})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
