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
# Raised from six: a six-word run is usually a clause rather than a thought,
# and a fragment that is provably verbatim still reads badly on a graphic.
MIN_QUOTE_WORDS = 8
# How many of a church's own past titles to show as style examples.
TITLE_EXAMPLES = 12
# How far ahead to look for events worth mentioning in a post.
EVENT_DAYS = 10
MAX_EVENTS = 8

# Livestream placeholders and service recordings carry no title style worth
# copying — a church whose feed is half "Traditional Service" would otherwise
# learn that its house style is to name nothing.
_GENERIC_TITLE = re.compile(
    r"^\s*(the\s+)?"
    r"(traditional|modern|contemporary|blended|early|late|sunday|morning|evening|"
    r"weekly|online|live)?\s*"
    r"(service|worship|livestream|live stream|broadcast|gathering|mass)\b.*$",
    re.IGNORECASE,
)


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


# Spoken disfluencies and caption errors survive verbatim extraction intact:
# "murdered at birth" arrives as "of of the Israelites", and a restart becomes
# "a lot of time to a lot of time". Both are genuinely what was said and both
# are unusable on a graphic. Detected by repetition rather than repaired,
# because repairing would break the guarantee that the words are exactly his.
_ADJACENT_REPEAT = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)


def has_disfluency(quote: str) -> bool:
    """True when a quote contains a stutter or a restarted phrase."""
    normalised = _normalise(quote)
    if _ADJACENT_REPEAT.search(normalised):
        return True
    # A three-word run appearing twice inside one sentence is a restart, not
    # rhetoric; deliberate repetition ("over and over again") does not repeat
    # a whole trigram.
    words = normalised.split()
    seen = set()
    for i in range(len(words) - 2):
        trigram = tuple(words[i:i + 3])
        if trigram in seen:
            return True
        seen.add(trigram)
    return False


def verify_quote(quote: str, transcript: str) -> bool:
    """True when *quote* is usable: actually said, long enough, and fluent."""
    if not quote or not transcript:
        return False
    if len(quote.split()) < MIN_QUOTE_WORDS:
        return False
    if has_disfluency(quote):
        return False
    return _normalise(quote) in _normalise(transcript)


# Quotation marks the model might use, straight and curly.
_QUOTED_SPAN = re.compile(r'["“”\u201c\u201d]([^"“”\u201c\u201d]{10,300})["“”\u201c\u201d]')
# Below this a quoted span is a scare-quoted word or a title, not a quotation.
MIN_SOCIAL_QUOTE_WORDS = 5


def quoted_spans_are_real(body: str, transcript: str) -> bool:
    """True unless the post quotes something the transcript does not contain.

    Short quoted spans are left alone — a single scare-quoted word or a series
    title in quotes is not a claim about what was said.
    """
    for span in _QUOTED_SPAN.findall(body or ""):
        span = span.strip()
        if len(span.split()) < MIN_SOCIAL_QUOTE_WORDS:
            continue
        if _normalise(span) not in _normalise(transcript):
            return False
    return True


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
  "quotes": ["4-8 sentences quoted EXACTLY from the transcript"],
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

Choosing quotes — accuracy is not enough, these go on graphics:
- Each must be a COMPLETE THOUGHT that makes sense to someone who did not hear
  the message: a subject doing something, not a dangling participial phrase.
  A run of words can be perfectly verbatim and still unusable as a quotation.
- Prefer a sentence that would make a stranger stop scrolling: an image, a
  turn of phrase, a claim with weight. Skip transitions, throat-clearing, and
  anything that only means something in context ("and that is the third point").
- 8 to 40 words. Longer does not fit a graphic; shorter is rarely a thought.

Writing the posts — this is where generated content usually gives itself away:
- Write FROM the message, not ABOUT the video. Never use the words "this
  message", "this sermon", "this week's message", "join us as we explore",
  "unpacks", "dives into", or "discover". A post that describes a recording
  reads like a press release; a post that says the thing the sermon said reads
  like a church talking to its people.
- Address the reader as "you", or speak as "we" meaning this congregation.
- Lead with the idea, not with the fact that a service happened.
- One idea per post. Do not summarise the whole message.
{title_examples}{events}{style}
"""


_TITLE_EXAMPLES_BLOCK = """
This church's own recent video titles. Match their pattern, length and register
— this is what their audience already recognises. Do not reuse their wording:
{titles}
"""

_EVENTS_BLOCK = """
Coming up at this church in the next few days. Where one genuinely connects to
the message, a post may mention it; never force a link, and never invent a
detail (a time, a place, an age range) that is not written here:
{events}
"""


def past_title_examples(church_id: int, exclude_id=None) -> list[str]:
    """A church's own recent sermon titles, as the style sample for new ones.

    A church's back catalogue is a better description of how it titles things
    than any setting it could be asked to fill in, and it costs nothing to
    collect. Generic service recordings are filtered out because they would
    teach the opposite of a house style.
    """
    from models import Sermon

    rows = (
        Sermon.query
        .filter(Sermon.church_id == church_id, Sermon.status == "ingested")
        .order_by(Sermon.published_at.desc())
        .limit(TITLE_EXAMPLES * 4)
        .all()
    )
    titles = []
    for row in rows:
        if exclude_id and row.id == exclude_id:
            continue
        title = (row.title or "").strip()
        if not title or _GENERIC_TITLE.match(title):
            continue
        if title not in titles:
            titles.append(title)
        if len(titles) >= TITLE_EXAMPLES:
            break
    return titles


def upcoming_events(church_id: int) -> list[str]:
    """Short descriptions of what is coming up, for the week's posts."""
    from datetime import datetime, timedelta

    from calendar_feed import _format_when
    from models import CalendarEvent

    now = datetime.utcnow()
    events = (
        CalendarEvent.query
        .filter(
            CalendarEvent.church_id == church_id,
            CalendarEvent.starts_at >= now,
            CalendarEvent.starts_at <= now + timedelta(days=EVENT_DAYS),
        )
        .order_by(CalendarEvent.starts_at)
        .limit(MAX_EVENTS)
        .all()
    )
    described = []
    for event in events:
        line = f"{event.title} — {_format_when(event)}"
        if event.location:
            line += f" ({event.location})"
        described.append(line)
    return described


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

    examples = past_title_examples(church.id, exclude_id=sermon.id)
    title_examples = _TITLE_EXAMPLES_BLOCK.format(
        titles="\n".join(f"  - {t}" for t in examples)) if examples else ""

    events = upcoming_events(church.id)
    events_block = _EVENTS_BLOCK.format(
        events="\n".join(f"  - {e}" for e in events)) if events else ""

    prompt = _PACKET_PROMPT.format(
        title=(sermon.title or "").replace('"', "'"),
        preached_on=sermon.published_at.strftime("%B %-d, %Y"),
        series_line=series_line,
        title_examples=title_examples,
        events=events_block,
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
    social_rejected = 0
    for raw in (data.get("social") or []):
        if not isinstance(raw, dict):
            continue
        body = str(raw.get("body") or "").strip()
        if not body:
            continue
        # A post may quote the message, and a quotation inside a post is
        # published exactly as widely as one on a graphic. The verbatim filter
        # covered only the quotes array, which left the easier hole open.
        if not quoted_spans_are_real(body, transcript):
            social_rejected += 1
            continue
        social.append({
            "platform": str(raw.get("platform") or "facebook").strip().lower()[:30],
            "body": body,
        })

    if rejected or social_rejected:
        log.info("[PACKET] discarded %d quote(s) and %d post(s) as unverifiable "
                 "for sermon_id=%s", rejected, social_rejected, sermon.id)

    return {
        "titles": [str(t).strip() for t in (data.get("titles") or []) if str(t).strip()][:5],
        "description": str(data.get("description") or "").strip(),
        "chapters": chapters,
        "quotes": quotes,
        "social": social,
        "quotes_rejected": rejected,
        "social_rejected": social_rejected,
        "has_timings": bool(segments),
    }
