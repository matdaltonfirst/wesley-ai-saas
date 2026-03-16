"""Document parsing, chunk caching, and relevance scoring."""

import re
import logging
import threading
from collections import defaultdict
from pathlib import Path

import pdfplumber
from docx import Document as DocxDocument

from models import Document, CrawledPage

log = logging.getLogger("wesley")

# ── Stop words for keyword extraction ────────────────────────────────────────

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

# ── Church data directory ────────────────────────────────────────────────────


def get_church_dir(church_id: int, uploads_dir: Path) -> Path:
    """Return (and lazily create) the per-church upload directory."""
    d = uploads_dir / str(church_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── PDF / DOCX parsing ──────────────────────────────────────────────────────


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
        log.error("Error reading PDF %s: %s", filepath.name, e)
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
            if current_text:
                chunks.append({
                    "content": "\n".join(current_text),
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
        log.error("Error reading DOCX %s: %s", filepath.name, e)
    return chunks


# ── In-memory document chunk cache ───────────────────────────────────────────
# Keyed by (doc_id, uploaded_at_iso). Since uploaded_at never changes for a
# given document row, entries are permanently valid until the document is
# deleted (at which point we proactively evict). Capped at 200 entries to
# bound memory usage (~50–100 KB per typical document = ~10–20 MB max).

_doc_cache: dict[tuple, list[dict]] = {}
_doc_cache_lock = threading.Lock()
_DOC_CACHE_MAX = 200


def _parse_doc_chunks(doc, filepath: Path) -> list[dict]:
    """Return parsed chunks for a document, reading from cache when possible."""
    cache_key = (doc.id, doc.uploaded_at.isoformat())
    with _doc_cache_lock:
        if cache_key in _doc_cache:
            return _doc_cache[cache_key]

    suffix = filepath.suffix.lower()
    if suffix == ".pdf":
        chunks = read_pdf(filepath, doc.original_name)
    elif suffix == ".docx":
        chunks = read_docx(filepath, doc.original_name)
    else:
        chunks = []

    with _doc_cache_lock:
        if len(_doc_cache) >= _DOC_CACHE_MAX:
            evict_keys = list(_doc_cache.keys())[: _DOC_CACHE_MAX // 2]
            for k in evict_keys:
                del _doc_cache[k]
        _doc_cache[cache_key] = chunks

    return chunks


def evict_doc_cache(doc_id: int, uploaded_at) -> None:
    """Remove a document's parsed chunks from the cache after deletion."""
    cache_key = (doc_id, uploaded_at.isoformat())
    with _doc_cache_lock:
        _doc_cache.pop(cache_key, None)


# ── Document loaders ─────────────────────────────────────────────────────────


def load_church_documents(church_id: int, uploads_dir: Path) -> list[dict]:
    """Load and parse all documents for a church (staff chat — no visibility filter)."""
    docs = Document.query.filter_by(church_id=church_id).all()
    church_dir = get_church_dir(church_id, uploads_dir)
    all_chunks = []
    for doc in docs:
        filepath = church_dir / doc.filename
        if not filepath.exists():
            continue
        all_chunks.extend(_parse_doc_chunks(doc, filepath))
    return all_chunks


def load_chatbot_documents(church_id: int, uploads_dir: Path) -> list[dict]:
    """Load and parse only documents marked staff_and_chatbot (widget chat)."""
    docs = Document.query.filter_by(church_id=church_id, visibility="staff_and_chatbot").all()
    church_dir = get_church_dir(church_id, uploads_dir)
    all_chunks = []
    for doc in docs:
        filepath = church_dir / doc.filename
        if not filepath.exists():
            continue
        all_chunks.extend(_parse_doc_chunks(doc, filepath))
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


# ── Relevance scoring ────────────────────────────────────────────────────────


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
