"""The long-form half of the Monday packet: a blog post and a group guide.

Split from sermon_packet.py deliberately. The packet's five short outputs come
back in one JSON object; an 1,800-word article and a full discussion guide do
not belong in that same response — JSON-escaping a long article is fragile, and
one malformed field would cost the quotes and social posts too. So these are
separate calls that fail independently: a blog post that does not come back
must not take Monday's quotes with it.

The verbatim rule from sermon_packet.py carries over unchanged. Anything inside
quotation marks is checked against the transcript and the piece is rejected if
it is not there, because a sentence the pastor did not say, published under the
church's name, is worse than no post at all. The prompts also steer away from
quoting the preacher at all: a church's blog reads as its own writing, not as a
transcript with commentary.
"""

import logging
import re

from content import profile_for, style_prompt_block
from helpers import call_gemini
from sermon_packet import MAX_PROMPT_TRANSCRIPT

log = logging.getLogger("wesley")

# Long enough to be a real article rather than a recap. Churches writing this
# way weekly land around 1,800 words; below roughly 900 it stops working as a
# standalone page and reads as a summary of something you missed.
BLOG_MIN_WORDS = 700

_SYSTEM = (
    "You write for a local church's own website. You work from what was "
    "preached, but you are writing an article, not describing a recording."
)


_BLOG_PROMPT = """\
Below is the transcript of a sermon titled "{title}", preached on {preached_on}\
{series_line}.

Write the church's weekly article drawn from this message.

Return MARKDOWN only — no JSON, no preamble, no commentary. Begin with a single
`# ` heading, then the article body under `## ` section headings.

Shape:
- A title on the first line as `# `. Either a short hook with a colon and a
  subtitle, or a bare question. It should read like an article title, not a
  sermon title.
- 1,400 to 1,900 words in the body.
- Four or five `## ` sections. Each heading is a phrase carrying an idea from
  the message — "Grace Puts Us All on Equal Footing", "What Jesus Does Not Say".
  Never "Introduction", "Conclusion", "Main Point", "Application".
- Continuous prose. No bullet lists, no numbered lists, no bold run-in labels.

Voice:
- Open on a scene, a tension, or an observation — the thing that makes the idea
  matter. Never open by referring to a service or a message.
- Write to the reader as "you", and about the congregation as "we".
- Close with an invitation or a question put to the reader. Not a hard sell.

Rules that override anything else:
- NEVER mention the sermon, the message, the service, Sunday, the recording,
  the preacher, or the person who preached. This article stands on its own; a
  reader arriving from a search engine has no idea a service happened.
- Use NO quotation marks anywhere in the article. Not around what was preached,
  not around scripture, not for emphasis. Render every idea in the article's own
  prose — "Paul writes in Romans 8 that nothing can separate us" rather than a
  quoted sentence. An article containing a quotation that is not word for word
  in the transcript is discarded entirely, and a Bible verse rendered from
  memory is exactly how a misquotation reaches the church's website.
- Scripture is referred to conversationally by book and chapter — "in John 20",
  "Paul writes in Romans 8". Do not invent a verse number and do not quote a
  translation you are unsure of.
- State no fact about this church the transcript does not contain: no service
  times, no addresses, no staff names, no event details.
- Do not claim the church believes something the message did not say.
{style}
--- TRANSCRIPT ---
{transcript}
"""


_GUIDE_PROMPT = """\
Below is the transcript of a sermon titled "{title}", preached on {preached_on}\
{series_line}.

Write a small group discussion guide for groups meeting this week.

Respond with ONLY a JSON object, no other text:

{{
  "scripture": "The main passage, as book and chapter — e.g. 'John 20' or \
'Matthew 20:1-16'. Empty string if the message did not work from one passage.",
  "summary": "2-3 sentences a leader can read aloud to set up the discussion.",
  "opening": "One warm-up question anyone can answer without a Bible and \
without having been present on Sunday.",
  "digging_in": ["3-4 questions about the passage itself — what it says, what \
is happening, what the people in it do"],
  "applying": ["2-3 questions that move from the passage to the group's own \
lives. Concrete, not rhetorical."],
  "prayer": "A short prompt for how the group might pray together, in one or \
two sentences.",
  "challenge": "One specific thing to try before the group meets again."
}}

Rules that override anything else:
- Questions must be answerable by someone who was not there on Sunday. Never
  write "as we heard on Sunday", "in this week's message", or "the pastor said".
- Use NO quotation marks anywhere in the guide. Refer to what a passage says in
  your own words. A quoted sentence not found word for word in the transcript
  discards the guide.
- Name only a passage the message actually worked from. If you are unsure of
  the chapter, give the book alone. Never invent a verse range.
- Open questions, not questions with one right answer. A question a group can
  answer "yes" to in unison is a wasted question.
- State no fact about this church the transcript does not contain.
{style}
--- TRANSCRIPT ---
{transcript}
"""


def _series_line(sermon) -> str:
    return f", part of the series \"{sermon.series}\"" if sermon.series else ""


def _preamble(sermon, church):
    """The formatting arguments both prompts share."""
    profile = profile_for(church)
    return {
        "title": (sermon.title or "").replace('"', "'"),
        "preached_on": sermon.published_at.strftime("%B %-d, %Y"),
        "series_line": _series_line(sermon),
        "style": style_prompt_block(church, profile),
        "transcript": (sermon.transcript or "")[:MAX_PROMPT_TRANSCRIPT],
    }


_FENCE = re.compile(r"^```(?:markdown|md)?\s*|\s*```$", re.MULTILINE)
# Bracketed citation markers, in case the retrieval instructions ever bleed in.
_CITATION = re.compile(r"\s*\[\d+\]")


def _split_title(markdown: str):
    """Pull the leading `# ` heading off the article, returning (title, body)."""
    text = _CITATION.sub("", _FENCE.sub("", (markdown or "").strip())).strip()
    match = re.match(r"#\s+(.+?)\s*\n(.*)$", text, re.S)
    if not match:
        return "", text
    return match.group(1).strip(), match.group(2).strip()


# A quoted fragment of three words or fewer is emphasis, not an attributed
# quotation; holding it to the verbatim standard only produces noise.
MIN_FLAGGED_WORDS = 4


def unverified_spans(body: str, transcript: str) -> list[str]:
    """Quoted passages long enough to read as attribution that are not verbatim.

    Deliberately NOT a filter. The packet's quotes and social posts are hard
    filtered, because those are lifted straight onto a graphic or into a post
    with nobody reading them again. An article is a 1,600-word draft that a
    person edits before it reaches the website, and the thing the model most
    often quotes is the passage the sermon was about — scripture, rendered from
    a translation rather than from what the preacher said aloud. Discarding a
    good article over that produced a feature that failed about half the time.

    So these are surfaced for a human to check instead of being thrown away.
    The guarantee that matters — no sentence invented and attributed to the
    preacher — is kept by never quoting the preacher at all, and by the hard
    filter that still governs quotes and social posts.
    """
    from sermon_packet import _QUOTED_SPAN, verify_quote
    out = []
    for match in _QUOTED_SPAN.finditer(body or ""):
        span = match.group(1).strip()
        if len(span.split()) < MIN_FLAGGED_WORDS:
            continue
        if verify_quote(span, transcript):
            continue
        if span not in out:
            out.append(span)
    return out[:8]


def build_blog(sermon, church) -> dict:
    """Generate the weekly article. Raises on an unusable response."""
    if not sermon.transcript:
        raise ValueError("sermon has no transcript")

    # The transcript rides inside the prompt rather than as context: the shared
    # context path prepends citation instructions, which would put bracketed
    # markers through the middle of an article.
    title, body = _split_title(
        call_gemini(_BLOG_PROMPT.format(**_preamble(sermon, church)),
                    "", [], _SYSTEM))
    if not body:
        raise ValueError("blog response was empty")

    words = len(body.split())
    if words < BLOG_MIN_WORDS:
        raise ValueError(f"blog came back too short ({words} words)")

    unverified = unverified_spans(body, sermon.transcript or "")
    if unverified:
        log.info("[LONGFORM] blog for sermon_id=%s has %d quotation(s) to check",
                 sermon.id, len(unverified))
    return {"title": title, "body": body, "words": words,
            "unverified": unverified}


def _clean_list(values, limit: int) -> list[str]:
    out = []
    for raw in (values or []):
        text = str(raw).strip()
        if text:
            out.append(text[:400])
        if len(out) >= limit:
            break
    return out


def build_guide(sermon, church) -> dict:
    """Generate the small group guide. Raises on an unusable response."""
    if not sermon.transcript:
        raise ValueError("sermon has no transcript")

    from sermon_packet import _parse

    data = _parse(call_gemini(
        _GUIDE_PROMPT.format(**_preamble(sermon, church)), "", [], _SYSTEM))

    guide = {
        "scripture":  str(data.get("scripture") or "").strip()[:120],
        "summary":    str(data.get("summary") or "").strip()[:800],
        "opening":    str(data.get("opening") or "").strip()[:400],
        "digging_in": _clean_list(data.get("digging_in"), 4),
        "applying":   _clean_list(data.get("applying"), 3),
        "prayer":     str(data.get("prayer") or "").strip()[:500],
        "challenge":  str(data.get("challenge") or "").strip()[:400],
    }
    if not guide["digging_in"] and not guide["applying"]:
        raise ValueError("guide came back with no questions")

    joined = " ".join(
        [guide["summary"], guide["opening"], guide["prayer"], guide["challenge"]]
        + guide["digging_in"] + guide["applying"])
    guide["unverified"] = unverified_spans(joined, sermon.transcript or "")
    return guide


def build_longform(sermon, church) -> dict:
    """Both long-form pieces, each failing on its own.

    Returned as a dict of what succeeded plus an error string per piece, rather
    than raising: a guide that failed to parse should not cost the church its
    article, and neither should cost it Monday's quotes.
    """
    out = {}
    for key, fn in (("blog", build_blog), ("guide", build_guide)):
        try:
            out[key] = fn(sermon, church)
        except Exception as exc:
            out[key] = None
            out[f"{key}_error"] = str(exc)[:300]
            log.warning("[LONGFORM] %s failed for sermon_id=%s: %s",
                        key, sermon.id, exc)
    return out
