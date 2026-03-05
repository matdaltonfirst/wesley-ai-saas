"""
crawler.py — Playwright BFS website crawler for Wesley AI.

Crawls a church's public website up to depth 3, scrapes visible text,
and upserts rows into the `crawled_pages` table.

Usage (inside a Flask app context):
    from crawler import crawl_church_website
    result = crawl_church_website(church_id=1, start_url="https://example.com")
"""

import time
import logging
from collections import deque
from datetime import datetime
from urllib.parse import urlparse, urljoin, urldefrag

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
MAX_PAGES   = 200
MAX_DEPTH   = 3
CRAWL_DELAY = 0.3   # seconds between page requests (polite)

# Extensions that are never HTML pages
SKIP_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".zip", ".rar", ".gz", ".tar",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".woff", ".woff2", ".ttf", ".eot",
}

# URL path patterns that are unlikely to contain useful church content
SKIP_PATTERNS = (
    "/wp-admin", "/wp-login", "/wp-json", "/wp-content/uploads",
    "/feed", "/xmlrpc", "?replytocom=", "#",
    "/cdn-cgi", "/wp-includes",
)


def _should_skip(url: str, base_host: str) -> bool:
    """Return True if this URL should not be crawled."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    if parsed.netloc != base_host:
        return True
    path_lower = parsed.path.lower()
    # Check extension
    for ext in SKIP_EXTENSIONS:
        if path_lower.endswith(ext):
            return True
    # Check skip patterns
    full_lower = url.lower()
    for pat in SKIP_PATTERNS:
        if pat in full_lower:
            return True
    return False


def _extract_links(html: str, base_url: str) -> list[str]:
    """Return a deduplicated list of absolute same-host links from the page."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href:
            continue
        abs_url, _ = urldefrag(urljoin(base_url, href))
        links.append(abs_url)
    return links


def _extract_text(html: str, url: str) -> tuple[str, str]:
    """Return (title, clean_text) from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()[:500]

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "noscript", "iframe", "aside", "form", "button",
                     "svg", "img", "meta", "link"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse blank lines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    clean = "\n".join(lines)
    return title, clean


def crawl_church_website(church_id: int, start_url: str) -> dict:
    """
    BFS crawl of `start_url` scoped to the same hostname.

    Must be called inside a Flask application context (so that db is bound).

    Returns:
        {
            "pages_crawled": int,
            "pages_failed":  int,
            "error":         str | None,
        }
    """
    # Import inside function so crawler can be imported without an app context
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    from models import db, Church, CrawledPage
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    start_url = start_url.rstrip("/")
    parsed_start = urlparse(start_url)
    base_host = parsed_start.netloc

    if not base_host:
        return {"pages_crawled": 0, "pages_failed": 0, "error": "Invalid start URL"}

    visited: set[str]  = set()
    # queue items: (url, depth)
    queue: deque = deque([(start_url, 0)])
    pages_crawled = 0
    pages_failed  = 0
    new_urls: list[str] = []  # track discovered urls for metrics

    log.info("Starting crawl church_id=%s url=%s", church_id, start_url)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (compatible; WesleyAI-Crawler/1.0; "
                    "+https://wesleyai.co/bot)"
                ),
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.set_default_timeout(15_000)  # 15s per page

            while queue and pages_crawled < MAX_PAGES:
                url, depth = queue.popleft()

                # Normalise: strip trailing slash for dedup
                url = url.rstrip("/") or url

                if url in visited:
                    continue
                if _should_skip(url, base_host):
                    continue

                visited.add(url)

                try:
                    response = page.goto(url, wait_until="domcontentloaded")
                    if response is None or response.status >= 400:
                        pages_failed += 1
                        continue

                    html = page.content()
                    title, text = _extract_text(html, url)

                    if not text.strip():
                        # No useful content — skip but don't count as failed
                        continue

                    # Upsert into DB
                    stmt = (
                        sqlite_insert(CrawledPage)
                        .values(
                            church_id=church_id,
                            url=url,
                            title=title,
                            content=text,
                            crawled_at=datetime.utcnow(),
                        )
                        .on_conflict_do_update(
                            index_elements=["church_id", "url"],
                            set_=dict(
                                title=title,
                                content=text,
                                crawled_at=datetime.utcnow(),
                            ),
                        )
                    )
                    db.session.execute(stmt)
                    db.session.commit()
                    pages_crawled += 1

                    # Enqueue linked pages if within depth limit
                    if depth < MAX_DEPTH:
                        for link in _extract_links(html, url):
                            norm = link.rstrip("/") or link
                            if norm not in visited and not _should_skip(norm, base_host):
                                queue.append((norm, depth + 1))

                    time.sleep(CRAWL_DELAY)

                except PWTimeout:
                    log.warning("Timeout crawling %s", url)
                    pages_failed += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("Error crawling %s: %s", url, exc)
                    pages_failed += 1

            page.close()
            context.close()
            browser.close()

    except Exception as exc:  # noqa: BLE001
        log.exception("Playwright fatal error for church_id=%s: %s", church_id, exc)
        return {"pages_crawled": pages_crawled, "pages_failed": pages_failed,
                "error": str(exc)}

    # Update Church.last_crawled_at
    try:
        church = Church.query.get(church_id)
        if church:
            church.last_crawled_at = datetime.utcnow()
            db.session.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to update last_crawled_at for church %s: %s", church_id, exc)

    log.info(
        "Crawl complete church_id=%s crawled=%d failed=%d",
        church_id, pages_crawled, pages_failed,
    )
    return {"pages_crawled": pages_crawled, "pages_failed": pages_failed, "error": None}
