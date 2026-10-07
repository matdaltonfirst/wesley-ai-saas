"""Calling Gemini: retries, model fallback, streaming, token counts.

No database, no request, no user: only text in, text out. Both the staff path
and the public chatbot use it, and neither can reach more than the prompt and
context they pass in.
"""

import json
import logging
import os
import time
from typing import Optional

from google import genai
from google.genai import types

from config import GEMINI_MODEL, GEMINI_FALLBACK_MODEL

log = logging.getLogger("wesley")


def friendly_gemini_error(exc: Exception) -> tuple[str, int]:
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


def _record_gemini_usage(usage: dict, response, model: str) -> None:
    """Copy the token counts Gemini already returned into *usage*.

    Defensive throughout: a missing or renamed usage field must never turn a
    successful answer into an error, so every count falls back to zero.
    """
    meta = getattr(response, "usage_metadata", None)

    def count(name):
        return int(getattr(meta, name, 0) or 0) if meta else 0

    prompt = count("prompt_token_count")
    response_tokens = count("candidates_token_count")
    usage.update({
        "model": model,
        "prompt_tokens": prompt,
        "response_tokens": response_tokens,
        "total_tokens": count("total_token_count") or (prompt + response_tokens),
    })


def sse_event(payload: dict) -> str:
    """Encode one server-sent event.

    Newlines inside the JSON would terminate the event early, so the payload is
    serialised without them — json.dumps escapes newlines inside strings, and
    the separators keep the encoding compact.
    """
    return "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"


def _build_request(question, context, history, system_instruction):
    """Assemble the client, contents, config, and model order for one ask.

    Shared by the blocking and streaming paths so they can never drift — the
    prompt, the citation instructions, and the fallback order are identical
    whichever one a caller uses.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")

    client = genai.Client(api_key=api_key)

    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    current_text = (
        "[Relevant church information:]\n"
        f"{context}\n---\n"
        "Use only sources that directly support your answer. Cite each factual claim "
        "drawn from a numbered source with its bracketed number, such as [1]. Do not "
        "cite a source unless it supports that claim. Use the smallest number of sources "
        "needed, preferring a page specifically about the question over home pages, blog "
        "posts, or pages where the fact appears only incidentally. If the sources do not support an "
        f"answer, say that the information is unavailable and do not add a citation.\n\n{question}"
        if context.strip()
        else question
    )
    contents.append(types.Content(role="user", parts=[types.Part(text=current_text)]))

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    models = [GEMINI_MODEL]
    if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
        models.append(GEMINI_FALLBACK_MODEL)

    return client, contents, config, models


def _is_rate_limit(err: str) -> bool:
    return "429" in err or "quota" in err or "rate" in err or "exhausted" in err


def _model_is_broken(err: str) -> bool:
    """Whether to try the fallback model: the model itself is retired or down
    (404/500/503), as opposed to an auth failure or a bad request."""
    return ("404" in err or "not found" in err or "503" in err
            or "unavailable" in err or "500" in err or "internal" in err)


def _attempt_models(models, run):
    """Run *run(model)* against each model with retry and fallback.

    *run* is called with a model name and may either return a value or, for the
    streaming path, a generator that is consumed by the caller. Retries only
    happen before any output has been produced.
    """
    last_exc: Exception = Exception("Unknown error")
    for model_idx, model in enumerate(models):
        for attempt in range(3):
            try:
                return run(model)
            except Exception as e:
                last_exc = e
                if _is_rate_limit(str(e).lower()) and attempt < 2:
                    time.sleep(2 ** attempt + 1)  # 2s, then 3s
                    continue
                break  # non-retryable, or retries exhausted: consider fallback
        if _model_is_broken(str(last_exc).lower()) and model_idx < len(models) - 1:
            log.warning("[GEMINI] model %s failed (%s); falling back to %s",
                        model, last_exc, models[model_idx + 1])
            continue
        raise last_exc
    raise last_exc


def call_gemini(
    question: str, context: str, history: list[dict], system_instruction: str,
    usage: Optional[dict] = None,
) -> str:
    """Ask Gemini and return the answer text.

    Pass *usage* to receive the call's token counts — it is populated in place
    rather than returned, so the return type stays a plain string for every
    existing caller and test double.
    """
    client, contents, config, models = _build_request(
        question, context, history, system_instruction)

    def run(model):
        response = client.models.generate_content(
            model=model, contents=contents, config=config)
        if usage is not None:
            _record_gemini_usage(usage, response, model)
        return response.text

    return _attempt_models(models, run)


def stream_gemini(
    question: str, context: str, history: list[dict], system_instruction: str,
    usage: Optional[dict] = None,
):
    """Yield the answer in pieces as Gemini produces them.

    Retry and model fallback only apply before the first piece is yielded: once
    text has reached the visitor, restarting on another model would replay the
    answer from the beginning. After that point an error propagates, and the
    caller decides what to show alongside what was already streamed.
    """
    client, contents, config, models = _build_request(
        question, context, history, system_instruction)

    def run(model):
        # Consume the first chunk inside the retry wrapper so a model that is
        # down fails here, where falling back is still safe.
        stream = client.models.generate_content_stream(
            model=model, contents=contents, config=config)
        iterator = iter(stream)
        first = next(iterator, None)
        return model, first, iterator

    model, first, iterator = _attempt_models(models, run)

    last_chunk = first
    for chunk in ([first] if first is not None else []):
        if getattr(chunk, "text", None):
            yield chunk.text
    for chunk in iterator:
        last_chunk = chunk
        if getattr(chunk, "text", None):
            yield chunk.text

    # Token counts arrive on the final chunk of the stream.
    if usage is not None and last_chunk is not None:
        _record_gemini_usage(usage, last_chunk, model)


