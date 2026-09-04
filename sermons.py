"""Sermon ingestion — YouTube channel monitoring, transcripts, and distilled notes.

A church connects its YouTube channel once (Data Sources → Sermons). New
uploads are detected daily and ingested: captions are fetched when available
(free), otherwise Gemini watches the video directly via its YouTube URL
support. Either way one Gemini pass distills the sermon into a summary, main
points, scriptures, and series name, which become citable chat sources.
"""

import json
import logging
import re
from datetime import datetime, timedelta

import requests
from google import genai
from google.genai import types

from models import db, SermonSource, Sermon
from config import YOUTUBE_API_KEY, GEMINI_MODEL

log = logging.getLogger("wesley")

YT_API = "https://www.googleapis.com/youtube/v3"
TIMEOUT_S = 20
BACKFILL_COUNT = 10          # most-recent videos ingested when a channel is connected
MAX_TRANSCRIPT_CHARS = 60000
CONTEXT_SERMON_COUNT = 8     # how many recent sermons the bot can see


class SermonError(ValueError):
    """A problem church staff can act on (shown in the dashboard)."""


def is_configured() -> bool:
    return bool(YOUTUBE_API_KEY)


def _yt_get(path: str, **params) -> dict:
    params["key"] = YOUTUBE_API_KEY
    resp = requests.get(f"{YT_API}/{path}", params=params, timeout=TIMEOUT_S)
    if resp.status_code != 200:
        log.error("YouTube API %s failed (%d): %s", path, resp.status_code, resp.text[:300])
        raise SermonError(f"YouTube did not accept the request ({resp.status_code}).")
    return resp.json()


def resolve_channel(url: str) -> dict:
    """Turn any YouTube channel URL/handle into {channel_id, title, uploads_playlist}."""
    url = url.strip()
    channel_match = re.search(r"youtube\.com/channel/(UC[\w-]+)", url)
    handle_match = re.search(r"(?:youtube\.com/)?@([\w.-]+)", url)
    user_match = re.search(r"youtube\.com/(?:user|c)/([\w.-]+)", url)

    if channel_match:
        data = _yt_get("channels", part="snippet,contentDetails", id=channel_match.group(1))
    elif handle_match:
        data = _yt_get("channels", part="snippet,contentDetails",
                       forHandle=handle_match.group(1))
    elif user_match:
        data = _yt_get("channels", part="snippet,contentDetails",
                       forUsername=user_match.group(1))
        if not data.get("items"):
            found = _yt_get("search", part="snippet", type="channel",
                            q=user_match.group(1), maxResults=1)
            items = found.get("items", [])
            if not items:
                raise SermonError("Could not find that YouTube channel.")
            data = _yt_get("channels", part="snippet,contentDetails",
                           id=items[0]["snippet"]["channelId"])
    else:
        raise SermonError(
            "Enter your YouTube channel link — like youtube.com/@yourchurch "
            "or youtube.com/channel/UC…"
        )

    items = data.get("items", [])
    if not items:
        raise SermonError("Could not find that YouTube channel. Check the link.")
    ch = items[0]
    return {
        "channel_id": ch["id"],
        "title": ch["snippet"].get("title", ""),
        "uploads_playlist": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def list_recent_videos(channel_id: str, limit: int = BACKFILL_COUNT) -> list[dict]:
    """Most recent uploads for a channel, newest first."""
    uploads = "UU" + channel_id[2:]  # uploads playlist id mirrors the channel id
    data = _yt_get("playlistItems", part="snippet,contentDetails",
                   playlistId=uploads, maxResults=min(limit, 50))
    videos = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        video_id = item.get("contentDetails", {}).get("videoId")
        if not video_id:
            continue
        published = item.get("contentDetails", {}).get("videoPublishedAt") \
            or snippet.get("publishedAt")
        videos.append({
            "video_id": video_id,
            "title": snippet.get("title", "")[:500],
            "published_at": datetime.strptime(published[:19], "%Y-%m-%dT%H:%M:%S"),
        })
    return videos[:limit]


# ── Transcript + distillation ────────────────────────────────────────────────

CAPTION_LANGUAGES = ["en", "en-US", "es"]


def _caption_pieces(video_id: str) -> list[dict]:
    """Caption pieces as {"start", "text"}, across library versions.

    youtube-transcript-api 1.1 replaced the ``get_transcript`` classmethod with
    an instance ``fetch`` returning objects rather than dicts. Both are handled
    because the older releases stopped working against YouTube — which is how
    every sermon came to be transcribed by the video fallback instead, with no
    transcript stored and nothing to quote from.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    if hasattr(YouTubeTranscriptApi, "fetch"):
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=CAPTION_LANGUAGES)
        return [
            {"start": getattr(s, "start", 0.0), "text": getattr(s, "text", "")}
            for s in fetched
        ]
    return YouTubeTranscriptApi.get_transcript(video_id, languages=CAPTION_LANGUAGES)


def fetch_captions(video_id: str):
    """Try YouTube captions. Returns (text, segments) or (None, None).

    Segments keep each caption piece's start time. Joining the pieces into one
    string — which is all the retrieval path needs — discards the timings, and
    without them a chapter list or a clip-worthy quote cannot say *where* in the
    message it came from. Never raises.
    """
    try:
        pieces = _caption_pieces(video_id)
        segments = [
            {"start": float(p.get("start") or 0.0), "text": p["text"].strip()}
            for p in pieces if p.get("text") and p["text"].strip()
        ]
        text = " ".join(s["text"] for s in segments)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None, None
        return text[:MAX_TRANSCRIPT_CHARS], segments
    except Exception as exc:
        log.info("Captions unavailable for %s (%s) — will use video fallback",
                 video_id, type(exc).__name__)
        return None, None


_DISTILL_PROMPT = """\
This is a recording (or transcript) of a church service or sermon video titled
"{title}". Identify the sermon/message portion (ignore announcements, music,
and liturgy) and respond with ONLY a JSON object, no other text:

{{
  "summary": "A 150-250 word recap of the sermon's message, written in third person",
  "main_points": ["3-6 short bullet points"],
  "scriptures": ["Scripture references preached from, e.g. 'John 3:16-21'"],
  "series": "The sermon series name if mentioned, else null"
}}

IMPORTANT: Never identify the preacher or any speaker by name unless the name
is stated unmistakably (introduced on screen or clearly spoken). Audio names
are easily misheard and a wrong name is worse than none — when in any doubt,
write "the pastor" or "the speaker". The same goes for any person named in
passing: omit names you are not certain of.

If the video contains no sermon or message at all, respond with:
{{"summary": null}}"""


def _parse_distilled(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def distill_sermon(sermon: Sermon) -> dict:
    """One Gemini pass: transcript text when we have it, else the video itself."""
    import os
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = _DISTILL_PROMPT.format(title=sermon.title.replace('"', "'"))

    if sermon.transcript:
        contents = [types.Content(role="user", parts=[
            types.Part(text=f"TRANSCRIPT:\n{sermon.transcript}\n\n{prompt}")
        ])]
        config = types.GenerateContentConfig()
    else:
        contents = [types.Content(role="user", parts=[
            types.Part(file_data=types.FileData(file_uri=sermon.video_url)),
            types.Part(text=prompt),
        ])]
        # Low media resolution: we only need the audio track's content
        config = types.GenerateContentConfig(
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
        )

    response = client.models.generate_content(
        model=GEMINI_MODEL, contents=contents, config=config,
    )
    return _parse_distilled(response.text or "")


def ingest_sermon(sermon: Sermon) -> bool:
    """Fetch transcript (captions → video fallback), distill, and save."""
    try:
        if not sermon.transcript:
            text, segments = fetch_captions(sermon.video_id)
            sermon.transcript = text
            sermon.transcript_segments = json.dumps(segments) if segments else None
        distilled = distill_sermon(sermon)

        if not distilled.get("summary"):
            sermon.status = "failed"
            sermon.error = "No sermon content found in this video."
            db.session.commit()
            return False

        sermon.summary = str(distilled["summary"])[:5000]
        points = distilled.get("main_points") or []
        sermon.main_points = "\n".join(str(p) for p in points)[:3000] or None
        scriptures = distilled.get("scriptures") or []
        sermon.scriptures = "; ".join(str(s) for s in scriptures)[:500] or None
        series = distilled.get("series")
        sermon.series = (str(series)[:200] if series else None)
        sermon.status = "ingested"
        sermon.error = None
        sermon.ingested_at = datetime.utcnow()
        db.session.commit()
        log.info("Sermon ingested: %r (%s, church_id=%d)",
                 sermon.title, sermon.video_id, sermon.church_id)
        return True
    except Exception as exc:
        db.session.rollback()
        sermon.status = "failed"
        sermon.error = str(exc)[:500]
        db.session.commit()
        log.error("Sermon ingest failed for %s: %s", sermon.video_id, exc)
        return False


def check_source(source: SermonSource, limit: int = BACKFILL_COUNT) -> int:
    """Find new uploads for one channel and ingest them. Returns ingested count."""
    source.last_checked_at = datetime.utcnow()
    try:
        videos = list_recent_videos(source.channel_id, limit=limit)
        source.last_error = None
    except SermonError as exc:
        source.last_error = str(exc)[:500]
        db.session.commit()
        return 0
    db.session.commit()

    known = {s.video_id for s in source.sermons}
    ingested = 0
    for video in videos:
        if video["video_id"] in known:
            continue
        sermon = Sermon(
            source_id=source.id, church_id=source.church_id,
            video_id=video["video_id"], title=video["title"],
            published_at=video["published_at"],
        )
        db.session.add(sermon)
        db.session.commit()
        ingested += ingest_sermon(sermon)

    # Recover sermons stranded in "pending" — a deploy or restart can kill the
    # background backfill/rebuild thread mid-run.
    stuck = [s for s in source.sermons if s.status == "pending"]
    if stuck:
        log.info("Recovering %d stuck sermon(s) for source %d.", len(stuck), source.id)
    for sermon in stuck:
        ingested += ingest_sermon(sermon)
    return ingested


def check_all_sources() -> int:
    """Daily job body: check every connected channel. Returns sermons ingested."""
    total = 0
    for source in SermonSource.query.all():
        try:
            total += check_source(source)
        except Exception as exc:
            db.session.rollback()
            log.error("Sermon check crashed for source %d: %s", source.id, exc)
    return total


# ── Transcript backfill ──────────────────────────────────────────────────────

# Sermons ingested while the caption library was broken have no transcript at
# all — they were distilled by Gemini watching the video, which answers
# questions but leaves nothing to quote. Captions are free, so refetching is
# cheap; the only real cost is time.
BACKFILL_BATCH = 25
# Past this age a sermon that still has no captions almost certainly never will
# (they were never generated, or the video is gone), and retrying it nightly
# would consume the batch forever while newer sermons waited behind it.
MAX_BACKFILL_AGE_DAYS = 120


def backfill_transcripts(limit: int = BACKFILL_BATCH, church_id: int = None) -> dict:
    """Fetch captions for already-ingested sermons that have none.

    Idempotent and bounded: a sermon that gains a transcript drops out of the
    queue, and one that cannot get captions is retried only while it is recent.
    Distillation is deliberately not re-run — the existing summaries are fine,
    and re-distilling every sermon would spend a model call each to change
    little.
    """
    cutoff = datetime.utcnow() - timedelta(days=MAX_BACKFILL_AGE_DAYS)
    query = (
        Sermon.query
        .filter(
            Sermon.status == "ingested",
            Sermon.transcript.is_(None),
            Sermon.published_at >= cutoff,
        )
        .order_by(Sermon.published_at.desc())
    )
    if church_id:
        query = query.filter(Sermon.church_id == church_id)

    filled = failed = 0
    for sermon in query.limit(limit).all():
        try:
            text, segments = fetch_captions(sermon.video_id)
        except Exception as exc:                      # fetch_captions is safe,
            log.error("Backfill error for %s: %s", sermon.video_id, exc)
            text, segments = None, None
        if not text:
            failed += 1
            continue
        sermon.transcript = text
        sermon.transcript_segments = json.dumps(segments) if segments else None
        filled += 1

    if filled:
        db.session.commit()
        log.info("Transcript backfill: filled %d sermon(s), %d still without captions.",
                 filled, failed)
    return {"filled": filled, "failed": failed}


# ── Chat context ─────────────────────────────────────────────────────────────

_SERMON_INTENT_WORDS = (
    "sermon", "message", "preach", "series", "pastor sa", "pastor talk",
    "sunday's message", "last sunday", "homily", "teaching",
)


def load_sermon_chunks(church_id: int) -> list[dict]:
    """Recent sermons as citable chunks, newest first."""
    from models import Church
    from helpers import utc_to_church

    sermons = (
        Sermon.query
        .filter_by(church_id=church_id, status="ingested")
        .order_by(Sermon.published_at.desc())
        .limit(CONTEXT_SERMON_COUNT)
        .all()
    )
    church = Church.query.get(church_id) if sermons else None
    chunks = []
    for i, sermon in enumerate(sermons):
        # YouTube publish timestamps are UTC; a Sunday-morning upload can read
        # as Monday without converting to the church's timezone.
        local = utc_to_church(sermon.published_at, church)
        date_str = local.strftime("%B %-d, %Y")
        lines = [f"Sermon: {sermon.title} (preached {date_str}"
                 + (", most recent sermon)" if i == 0 else ")")]
        if sermon.series:
            lines.append(f"Series: {sermon.series}")
        if sermon.scriptures:
            lines.append(f"Scripture: {sermon.scriptures}")
        lines.append(sermon.summary or "")
        if sermon.main_points:
            lines.append("Main points:\n" + sermon.main_points)
        chunks.append({
            "content": "\n".join(lines),
            "source": f"Sermon: {sermon.title}",
            "location": sermon.video_url,
            "type": "sermon",
        })
    return chunks


def score_sermon_chunks(question: str, chunks: list[dict]) -> list[tuple[int, dict]]:
    """Sermon-intent questions get recent sermons newest-first; specific
    questions ("what did he say about grace") use keyword relevance."""
    from documents import find_relevant_chunks

    if not chunks:
        return []
    lowered = question.lower()
    if any(word in lowered for word in _SERMON_INTENT_WORDS):
        return [(1, chunk) for chunk in chunks]
    return find_relevant_chunks(question, chunks, top_n=4)
