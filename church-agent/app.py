import os
import re
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from google import genai
from google.genai import types
import pdfplumber
from docx import Document

load_dotenv()

# Validate API key at startup so misconfiguration is obvious in the logs
_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    print("WARNING: GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
else:
    print(f"Gemini API key loaded ({_api_key[:8]}…)")

app = Flask(__name__, static_folder="static")

DOCUMENTS_DIR = Path("/Users/matnapp/Library/CloudStorage/GoogleDrive-mat@daltonfumc.com/Shared drives/wesley's workspace")

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

def read_pdf(filepath: Path) -> list[dict]:
    chunks = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text()
                if text and text.strip():
                    chunks.append({
                        "content": text.strip(),
                        "source": filepath.name,
                        "location": f"Page {page_num}",
                    })
    except Exception as e:
        print(f"Error reading PDF {filepath.name}: {e}")
    return chunks


def read_docx(filepath: Path) -> list[dict]:
    chunks = []
    try:
        doc = Document(filepath)
        current_heading = "Document"
        current_text: list[str] = []
        current_length = 0

        def flush_chunk():
            nonlocal current_text, current_length
            text = " ".join(current_text).strip()
            if text:
                chunks.append({
                    "content": text,
                    "source": filepath.name,
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


def load_all_documents() -> list[dict]:
    all_chunks = []
    for filepath in sorted(DOCUMENTS_DIR.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() == ".pdf":
            all_chunks.extend(read_pdf(filepath))
        elif filepath.suffix.lower() == ".docx":
            all_chunks.extend(read_docx(filepath))
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


# ── Gemini call (multi-turn) ──────────────────────────────────────────────────

def _friendly_gemini_error(exc: Exception) -> tuple[str, int]:
    """Return a (user-facing message, HTTP status) for common Gemini errors."""
    msg = str(exc).lower()
    if "429" in msg or "quota" in msg or "rate" in msg or "exhausted" in msg:
        return (
            "The AI service is temporarily over its request limit. "
            "Please wait a moment and try again.",
            429,
        )
    if "401" in msg or "403" in msg or "api_key" in msg or "invalid" in msg and "key" in msg:
        return (
            "API key error — please check that GEMINI_API_KEY is set correctly in your .env file.",
            401,
        )
    if "404" in msg or "not found" in msg:
        return (
            "The AI model could not be found. Please check the model name in app.py.",
            404,
        )
    if "503" in msg or "unavailable" in msg:
        return (
            "The AI service is temporarily unavailable. Please try again in a few seconds.",
            503,
        )
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

    if context.strip():
        current_text = f"[Relevant church documents:]\n{context}\n---\n{question}"
    else:
        current_text = question

    contents.append(types.Content(role="user", parts=[types.Part(text=current_text)]))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION),
    )
    return response.text


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/documents")
def list_documents():
    files = []
    for filepath in sorted(DOCUMENTS_DIR.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() in {".pdf", ".docx"}:
            # Show subfolder/filename so staff can tell files apart
            relative = filepath.relative_to(DOCUMENTS_DIR)
            files.append({
                "name": str(relative),
                "size_kb": round(filepath.stat().st_size / 1024, 1),
                "type": filepath.suffix.lower().lstrip("."),
            })
    return jsonify({"documents": files})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True)
    if not data or not data.get("question", "").strip():
        return jsonify({"error": "No question provided"}), 400

    question = data["question"].strip()
    history = data.get("history", [])

    chunks = load_all_documents()
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

    # Only surface sources whose filename is actually mentioned in the answer.
    # Gemini is instructed to cite by filename, so this filters out documents
    # that were passed as context but not genuinely used.
    answer_lower = answer.lower()
    sources = [
        s for s in candidate_sources
        if s["file"].lower() in answer_lower
    ]

    return jsonify({"answer": answer, "sources": sources})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
