"""
crawler.py — Website crawler for Wesley AI.

Tries Playwright (JS-rendering) first; if Playwright/Chromium is unavailable
(e.g. Railway without system deps installed) it automatically falls back to
requests + BeautifulSoup.

Most church websites (WordPress, Squarespace, standard Wix) work fine with
the requests fallback — JS rendering is only needed for heavy SPAs.

Usage (inside a Flask app context):
    from crawler import crawl_church_website
    result = crawl_church_website(church_id=1, start_url="https://example.com")
"""

import time
import logging
from collections import deque
from datetime import datetime
from urllib.parse import urlparse, urljoin, urldefrag

import requests as req_lib
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_PAGES   = 200
MAX_DEPTH   = 3
CRAWL_DELAY = 0.3   # seconds between requests (polite)

CRAWLER_UA = (
    "Mozilla/5.0 (compatible; WesleyAI-Crawler/1.0; +https://wesleyai.co/bot)"
)

SKIP_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".zip", ".rar", ".gz", ".tar",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".woff", ".woff2", ".ttf", ".eot",
}

SKIP_PATTERNS = (
    "/wp-admin", "/wp-login", "/wp-json", "/wp-content/uploads",
    "/feed", "/xmlrpc", "?replytocom=", "#",
    "/cdn-cgi", "/wp-includes",
)


# ── Shared URL helpers ────────────────────────────────────────────────────────

def _should_skip(url: str, base_host: str) -> bool:
    """Return True if this URL should not be crawled."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    if parsed.netloc != base_host:
        return True
    path_lower = parsed.path.lower()
    for ext in SKIP_EXTENSIONS:
        if path_lower.endswith(ext):
            return True
    full_lower = url.lower()
    for pat in SKIP_PATTERNS:
        if pat in full_lower:
            return True
    return False


def _extract_links(html: str, base_url: str) -> list[str]:
    """Return absolute links found in the page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href:
            continue
        abs_url, _ = urldefrag(urljoin(base_url, href))
        links.append(abs_url)
    return links


def _extract_text(html: str) -> tuple[str, str]:
    """Return (title, clean_text) from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()[:500]

    for tag in soup(["script", "style", "nav", "footer", "header",
                     "noscript", "iframe", "aside", "form", "button",
                     "svg", "img", "meta", "link"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return title, "\n".join(lines)


def _upsert_page(church_id: int, url: str, title: str, text: str) -> None:
    """Insert or update a CrawledPage row."""
    from models import db, CrawledPage
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
            set_=dict(title=title, content=text, crawled_at=datetime.utcnow()),
        )
    )
    db.session.execute(stmt)
    db.session.commit()


# ── Generic BFS engine ────────────────────────────────────────────────────────

def _bfs_crawl(
    church_id: int,
    start_url: str,
    base_host: str,
    fetch_fn,           # (url: str) -> str | None   (None = skip)
    method_name: str,
) -> tuple[int, int]:
    """
    BFS over `start_url` up to MAX_DEPTH / MAX_PAGES.
    `fetch_fn` must return raw HTML or None (on 4xx/5xx or network error).
    Returns (pages_crawled, pages_failed).
    """
    visited: set[str] = set()
    queue: deque = deque([(start_url, 0)])
    pages_crawled = 0
    pages_failed  = 0

    while queue and pages_crawled < MAX_PAGES:
        url, depth = queue.popleft()
        url = url.rstrip("/") or url

        if url in visited:
            continue
        if _should_skip(url, base_host):
            log.debug("[%s] Skipping (filtered): %s", method_name, url)
            continue

        visited.add(url)
        _log(f"[{method_name}] Fetching ({depth}/{MAX_DEPTH}): {url}")

        try:
            html = fetch_fn(url)
        except Exception as exc:
            _log(f"[{method_name}] Fetch error on {url}: {exc}", level="warning")
            pages_failed += 1
            time.sleep(CRAWL_DELAY)
            continue

        if html is None:
            _log(f"[{method_name}] Non-200 response, skipping: {url}", level="warning")
            pages_failed += 1
            continue

        title, text = _extract_text(html)

        if not text.strip():
            _log(f"[{method_name}] No text content extracted from: {url}", level="warning")
            continue

        try:
            _upsert_page(church_id, url, title, text)
        except Exception as exc:
            _log(f"[{method_name}] DB upsert failed for {url}: {exc}", level="error")
            pages_failed += 1
            continue

        pages_crawled += 1
        _log(f"[{method_name}] Saved page #{pages_crawled}: {title!r} — {url}")

        if depth < MAX_DEPTH:
            new_links = 0
            for link in _extract_links(html, url):
                norm = link.rstrip("/") or link
                if norm not in visited and not _should_skip(norm, base_host):
                    queue.append((norm, depth + 1))
                    new_links += 1
            if new_links:
                log.debug("[%s] Enqueued %d links from %s", method_name, new_links, url)

        time.sleep(CRAWL_DELAY)

    return pages_crawled, pages_failed


def _log(msg: str, level: str = "info") -> None:
    """Log via Python logger (visible in Railway log viewer via basicConfig)."""
    getattr(log, level)("[Wesley Crawler] %s", msg)


# ── Playwright crawler ────────────────────────────────────────────────────────

def _crawl_with_playwright(church_id: int, start_url: str, base_host: str) -> tuple[int, int]:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    _log("Launching Playwright Chromium browser…")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            user_agent=CRAWLER_UA,
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(15_000)

        _log("Playwright browser ready.")

        def fetch(url: str):
            try:
                response = page.goto(url, wait_until="domcontentloaded")
                if response is None:
                    _log(f"[playwright] No response object for {url}", level="warning")
                    return None
                if response.status >= 400:
                    _log(f"[playwright] HTTP {response.status} for {url}", level="warning")
                    return None
                return page.content()
            except PWTimeout:
                _log(f"[playwright] Timeout on {url}", level="warning")
                return None

        pages_crawled, pages_failed = _bfs_crawl(
            church_id, start_url, base_host, fetch, "playwright"
        )

        page.close()
        context.close()
        browser.close()

    return pages_crawled, pages_failed


# ── requests fallback crawler ─────────────────────────────────────────────────

def _crawl_with_requests(church_id: int, start_url: str, base_host: str) -> tuple[int, int]:
    session = req_lib.Session()
    session.headers.update({
        "User-Agent": CRAWLER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })

    _log("requests session ready.")

    def fetch(url: str):
        try:
            resp = session.get(url, timeout=15, allow_redirects=True)
            if resp.status_code >= 400:
                _log(f"[requests] HTTP {resp.status_code} for {url}", level="warning")
                return None
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type:
                log.debug("[requests] Non-HTML content-type (%s) for %s", content_type, url)
                return None
            return resp.text
        except req_lib.exceptions.Timeout:
            _log(f"[requests] Timeout on {url}", level="warning")
            return None
        except req_lib.exceptions.RequestException as exc:
            _log(f"[requests] Request error on {url}: {exc}", level="warning")
            return None

    return _bfs_crawl(church_id, start_url, base_host, fetch, "requests")


# ── Public entry point ────────────────────────────────────────────────────────

def crawl_church_website(church_id: int, start_url: str) -> dict:
    """
    Crawl `start_url` for church `church_id`.

    Tries Playwright (JS-rendering) first. If Playwright is unavailable or
    fails to launch, automatically falls back to requests + BeautifulSoup.

    Must be called inside a Flask application context.

    Returns:
        {"pages_crawled": int, "pages_failed": int, "error": str | None,
         "method": "playwright" | "requests"}
    """
    from models import db, Church

    start_url = start_url.rstrip("/")
    parsed = urlparse(start_url)
    base_host = parsed.netloc

    if not base_host:
        _log(f"Invalid start URL for church_id={church_id}: {start_url!r}", level="error")
        return {"pages_crawled": 0, "pages_failed": 0,
                "error": "Invalid start URL", "method": None}

    _log(f"=== Crawl start — church_id={church_id} url={start_url} ===")

    # ── Try Playwright ────────────────────────────────────────────────────────
    method = "playwright"
    try:
        # Attempt a quick browser launch to verify Chromium is available
        # before committing to the full BFS.
        from playwright.sync_api import sync_playwright
        _log("Checking Playwright/Chromium availability…")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            browser.close()
        _log("Playwright available — using JS-rendering crawler.")

        pages_crawled, pages_failed = _crawl_with_playwright(
            church_id, start_url, base_host
        )

    except Exception as exc:
        _log(
            f"Playwright unavailable ({type(exc).__name__}: {exc}) "
            "— falling back to requests crawler.",
            level="warning",
        )
        method = "requests"
        try:
            pages_crawled, pages_failed = _crawl_with_requests(
                church_id, start_url, base_host
            )
        except Exception as exc2:
            _log(f"requests crawler also failed: {exc2}", level="error")
            return {"pages_crawled": 0, "pages_failed": 0,
                    "error": str(exc2), "method": method}

    # ── Update Church.last_crawled_at ─────────────────────────────────────────
    try:
        church = Church.query.get(church_id)
        if church:
            church.last_crawled_at = datetime.utcnow()
            db.session.commit()
    except Exception as exc:
        _log(f"Failed to update last_crawled_at for church_id={church_id}: {exc}",
             level="error")

    _log(
        f"=== Crawl complete — church_id={church_id} method={method} "
        f"crawled={pages_crawled} failed={pages_failed} ==="
    )
    return {
        "pages_crawled": pages_crawled,
        "pages_failed":  pages_failed,
        "error":         None,
        "method":        method,
    }
