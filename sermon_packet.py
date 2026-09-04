"""Turn one preached sermon into a week of content — the Monday packet.

A church's sermon is the richest thing it produces all week, and today it is
used once, as a video upload, and then never again. This harvests it: video
metadata, clip-worthy quotes, and social posts, waiting on Monday morning for a
message preached on Sunday, with nothing asked of the staff who preached it.

Input is a Sermon record, never YouTube. Everything here works from the stored
transcript, so a future Vimeo, podcast, or direct-upload ingester needs no
change on this side.

**Quotes are extracted, never written.** Every quote the model returns is
checked against the transcript and dropped if it is not actually there. A
sentence the pastor did not say, posted publicly in their name, is worse than
no feature at all — so the check is a hard filter rather than a prompt request,
because a prompt is a hope and a filter is a guarantee.
"""

import json
import logging
import re

from content import profile_for, style_prompt_block
from helpers import call_gemini

log = logging.getLogger("wesley")

# Long transcripts are truncated for the model; the verbatim check still runs
# against the full text so a quote from a late passage is never wrongly dropped.
MAX_PROMPT_TRANSCRIPT = 45000
MAX_QUOTES = 8
MIN_QUOTE_WORDS = 6


def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, and drop punctuation that captions vary on.

    Auto-captions differ from human phrasing in apostrophes, commas and casing,
    so an exact match would reject quotes that genuinely were spoken.
    """
    text = (text or "").lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def verify_quote(quote: str, transcript: str) -> bool:
    """True when *quote* actually appears in *transcript*."""
    if not quote or not transcript:
        return False
    if len(quote.split()) < MIN_QUOTE_WORDS:
        return False
    return _normalise(quote) in _normalise(transcript)


def locate_quote(quote: str, segments) -> float:
    """Start time in seconds of the point where *quote* begins, or -1.

    Works at word level rather than per caption piece, because a spoken
    sentence is split across several pieces and the answer wanted is the time
    of the quote's first *word*, not of the piece that happens to contain it.
    """
    if not segments:
        return -1.0
    needle = _normalise(quote).split()[:MIN_QUOTE_WORDS]
    if not needle:
        return -1.0

    # Each caption word paired with the start time of the piece it came from.
    words = []
    for segment in segments:
        start = float(segment.get("start") or 0.0)
        for word in _normalise(segment.get("text", "")).split():
            words.append((word, start))

    span = len(needle)
    for index in range(len(words) - span + 1):
        if [w for w, _ in words[index:index + span]] == needle:
            return words[index][1]
    return -1.0


def format_timestamp(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_PACKET_PROMPT = """\
Below is the transcript of a sermon titled "{title}", preached on {preached_on}\
{series_line}.

Produce content for this church's staff to review and publish. Respond with
ONLY a JSON object, no other text:

{{
  "titles": ["3 candidate video titles, following the title strategy below"],
  "description": "A YouTube description of 100-180 words: what the message is \
about and who it is for. No links, no invented service times, no church address.",
  "chapters": [
    {{"label": "Short section name", "quote": "the exact sentence from the \
transcript where this section begins"}}
  ],
  "quotes": ["4-8 sentences quoted EXACTLY from the transcript, each one \
strong enough to stand alone as a graphic or a short clip"],
  "social": [
    {{"platform": "facebook|instagram", "body": "A post drawing on the message"}}
  ]
}}

Rules that override anything else:
- Every string in "quotes", and every "quote" in "chapters", must be copied
  WORD FOR WORD from the transcript. Do not tidy grammar, do not shorten, do
  not merge two sentences. Anything not found verbatim is discarded.
- Do not state a fact about this church that the transcript does not contain —
  no service times, no addresses, no event dates, no staff names.
- Never name the preacher or anyone else unless the transcript names them
  unmistakably. A wrong name is worse than none.
- Write nothing that claims the church believes something the message did not say.
{style}
"""


def _parse(raw: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.MULTILINE)
    data = json.loads(text.strip())
    if not isinstance(data, dict):
        raise ValueError("packet response was not a JSON object")
    return data


def build_packet(sermon, church) -> dict:
    """Generate the packet for one sermon. Raises on an unusable response."""
    if not sermon.transcript:
        raise ValueError("sermon has no transcript")

    profile = profile_for(church)
    series_line = f", part of the series \"{sermon.series}\"" if sermon.series else ""
    prompt = _PACKET_PROMPT.format(
        title=(sermon.title or "").replace('"', "'"),
        preached_on=sermon.published_at.strftime("%B %-d, %Y"),
        series_line=series_line,
        style=style_prompt_block(church, profile),
    )
    answer = call_gemini(
        prompt,
        sermon.transcript[:MAX_PROMPT_TRANSCRIPT],
        [],
        "You prepare publishable content for a local church from what was "
        "actually preached. You quote; you do not compose quotations.",
    )
    data = _parse(answer)

    try:
        segments = json.loads(sermon.transcript_segments) if sermon.transcript_segments else []
    except (ValueError, TypeError):
        segments = []

    return _assemble(data, sermon, segments)


def _assemble(data: dict, sermon, segments) -> dict:
    """Filter the model's output down to what is provably in the transcript."""
    transcript = sermon.transcript or ""

    quotes = []
    rejected = 0
    for raw in (data.get("quotes") or []):
        quote = str(raw).strip().strip('"')
        if not verify_quote(quote, transcript):
            rejected += 1
            continue
        start = locate_quote(quote, segments)
        quotes.append({
            "text": quote,
            "start": start if start >= 0 else None,
            "timestamp": format_timestamp(start),
            "clip_url": (f"{sermon.video_url}&t={int(start)}s"
                         if start >= 0 and sermon.video_id else None),
        })
        if len(quotes) >= MAX_QUOTES:
            break

    chapters = []
    for raw in (data.get("chapters") or []):
        if not isinstance(raw, dict):
            continue
        anchor = str(raw.get("quote") or "").strip()
        label = str(raw.get("label") or "").strip()
        if not label:
            continue
        start = locate_quote(anchor, segments) if anchor else -1.0
        # A chapter without a timestamp is not a chapter — YouTube needs the
        # time, and a guessed one would send viewers to the wrong place.
        if start < 0:
            continue
        chapters.append({"label": label[:80], "start": start,
                         "timestamp": format_timestamp(start)})
    chapters.sort(key=lambda c: c["start"])
    # YouTube requires the first chapter to be at 0:00 or it renders none.
    if chapters and chapters[0]["start"] > 0:
        chapters.insert(0, {"label": "Introduction", "start": 0.0, "timestamp": "0:00"})

    social = []
    for raw in (data.get("social") or []):
        if not isinstance(raw, dict):
            continue
        body = str(raw.get("body") or "").strip()
        if body:
            social.append({
                "platform": str(raw.get("platform") or "facebook").strip().lower()[:30],
                "body": body,
            })

    if rejected:
        log.info("[PACKET] discarded %d unverifiable quote(s) for sermon_id=%s",
                 rejected, sermon.id)

    return {
        "titles": [str(t).strip() for t in (data.get("titles") or []) if str(t).strip()][:5],
        "description": str(data.get("description") or "").strip(),
        "chapters": chapters,
        "quotes": quotes,
        "social": social,
        "quotes_rejected": rejected,
        "has_timings": bool(segments),
    }
