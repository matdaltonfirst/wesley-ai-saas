"""Tests for the streaming chat endpoints.

Streaming changes the failure surface: an answer can now fail *after* the
visitor has already read part of it, and the database write happens inside the
response generator rather than before it. These cover both.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from helpers import sse_event, stream_gemini
from models import db, Conversation, Message, UsageDaily, WidgetMessage
from usage import STAFF, WIDGET


def _events(response):
    """Parse an SSE body into a list of payload dicts."""
    body = response.get_data(as_text=True)
    out = []
    for block in body.split("\n\n"):
        line = block.strip()
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out


def _streamer(*pieces, usage=None, fail_after=None):
    """A stream_gemini double yielding fixed pieces, optionally then failing."""
    def fake(question, context, history, system_instruction, usage_sink=None, **kwargs):
        sink = kwargs.get("usage")
        for index, piece in enumerate(pieces):
            if fail_after is not None and index == fail_after:
                raise RuntimeError("503 service unavailable")
            yield piece
        if fail_after is not None and fail_after >= len(pieces):
            raise RuntimeError("503 service unavailable")
        if sink is not None and usage:
            sink.update(usage)
    return fake


# ── The event encoder ────────────────────────────────────────────────────────

class TestSseEncoding:
    def test_newlines_in_text_do_not_terminate_the_event(self):
        """A model answer containing a blank line would otherwise split into
        two events and corrupt the stream."""
        encoded = sse_event({"type": "delta", "text": "line one\n\nline two"})
        assert encoded.count("\n\n") == 1
        assert encoded.endswith("\n\n")

    def test_payload_round_trips(self):
        encoded = sse_event({"type": "done", "sources": [{"title": "A"}]})
        assert json.loads(encoded[len("data: "):]) == {
            "type": "done", "sources": [{"title": "A"}],
        }


# ── stream_gemini ────────────────────────────────────────────────────────────

class TestStreamGemini:
    def test_yields_text_and_records_usage_from_the_final_chunk(self):
        chunks = [
            SimpleNamespace(text="Hello ", usage_metadata=None),
            SimpleNamespace(text="world.", usage_metadata=SimpleNamespace(
                prompt_token_count=40, candidates_token_count=8, total_token_count=48)),
        ]
        client = SimpleNamespace(models=SimpleNamespace(
            generate_content_stream=lambda **kw: iter(chunks)))
        usage = {}

        with patch("helpers._build_request",
                   return_value=(client, [], None, ["gemini-2.5-flash-lite"])):
            pieces = list(stream_gemini("q", "", [], "sys", usage=usage))

        assert "".join(pieces) == "Hello world."
        assert usage["total_tokens"] == 48

    def test_a_model_that_is_down_falls_back_before_any_output(self):
        calls = []

        def generate(**kw):
            calls.append(kw["model"])
            if kw["model"] == "primary":
                raise RuntimeError("503 unavailable")
            return iter([SimpleNamespace(text="from fallback", usage_metadata=None)])

        client = SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate))
        with patch("helpers._build_request",
                   return_value=(client, [], None, ["primary", "fallback"])):
            pieces = list(stream_gemini("q", "", [], "sys"))

        assert "".join(pieces) == "from fallback"
        assert calls == ["primary", "fallback"]


# ── Widget streaming ─────────────────────────────────────────────────────────

class TestWidgetStreaming:
    def test_deltas_then_done_with_sources(self, client, church):
        with patch("routes.widget.stream_gemini",
                   side_effect=_streamer("Worship ", "is at 10am. [1]")), \
             patch("routes.widget.load_church_web_content", return_value=[
                 {"content": "Worship is at 10am.", "source": "Times",
                  "location": "https://church.org/times"}]), \
             patch("routes.widget.load_chatbot_documents", return_value=[]), \
             patch("routes.widget.load_curated_content", return_value=[]):
            res = client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "What time is worship?",
            })

        assert res.status_code == 200
        assert res.mimetype == "text/event-stream"
        events = _events(res)
        assert [e["text"] for e in events if e["type"] == "delta"] == [
            "Worship ", "is at 10am. [1]"]
        final = events[-1]
        assert final["type"] == "done"
        assert final["saved"] is True
        assert final["sources"][0]["title"] == "Times"
        assert final["session_id"]

    def test_the_answer_is_persisted(self, client, church):
        with patch("routes.widget.stream_gemini", side_effect=_streamer("Hello.")):
            res = client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "Hi?",
            })
        _events(res)  # drain the generator so its commit runs

        saved = WidgetMessage.query.filter_by(role="assistant").all()
        assert [m.content for m in saved] == ["Hello."]

    def test_proxy_buffering_is_disabled(self, client, church):
        """Without this header a buffering proxy holds the whole response and
        the visitor sees nothing until the end — the thing being fixed."""
        with patch("routes.widget.stream_gemini", side_effect=_streamer("Hi.")):
            res = client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "Hi?",
            })
        assert res.headers["X-Accel-Buffering"] == "no"
        assert res.headers["Access-Control-Allow-Origin"] == "*"

    def test_failure_mid_stream_emits_an_error_event(self, client, church):
        with patch("routes.widget.stream_gemini",
                   side_effect=_streamer("Partial ", fail_after=1)):
            res = client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "Hi?",
            })
        events = _events(res)
        assert events[0]["type"] == "delta"
        assert events[-1]["type"] == "error"
        assert "unavailable" in events[-1]["error"].lower()
        # A failed answer is not written to the transcript.
        assert WidgetMessage.query.filter_by(role="assistant").count() == 0

    def test_an_empty_answer_is_reported_rather_than_saved(self, client, church):
        with patch("routes.widget.stream_gemini", side_effect=_streamer("", "  ")):
            res = client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "Hi?",
            })
        assert _events(res)[-1]["type"] == "error"
        assert WidgetMessage.query.filter_by(role="assistant").count() == 0

    def test_billing_gate_applies_to_the_stream_endpoint(self, client, church):
        from datetime import datetime, timedelta
        church.billing_exempt = False
        church.trial_ends_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()

        with patch("routes.widget.stream_gemini") as streamer:
            res = client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "Hi?",
            })
        assert res.status_code == 402
        streamer.assert_not_called()

    def test_validation_rejections_are_json_not_a_stream(self, client, church):
        """The client only falls back to reading JSON when the response is not
        ok, so rejections must not arrive as events."""
        res = client.post("/api/widget/chat/stream", json={"church_id": church.id})
        assert res.status_code == 400
        assert res.mimetype == "application/json"

    def test_usage_is_metered_from_the_stream(self, client, church):
        streamer = _streamer("Hi.", usage={
            "model": "gemini-2.5-flash-lite", "prompt_tokens": 30,
            "response_tokens": 5, "total_tokens": 35,
        })
        with patch("routes.widget.stream_gemini", side_effect=streamer):
            res = client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "Hi?",
            })
        _events(res)

        row = UsageDaily.query.filter_by(church_id=church.id, surface=WIDGET).one()
        assert row.total_tokens == 35


# ── Staff streaming ──────────────────────────────────────────────────────────

class TestStaffStreaming:
    def test_deltas_then_done_and_persistence(self, auth_client, church):
        with patch("routes.chat.stream_gemini",
                   side_effect=_streamer("Here is ", "an outline.")):
            res = auth_client.post("/api/chat/stream", json={"question": "Sermon help"})

        assert res.status_code == 200
        events = _events(res)
        assert "".join(e["text"] for e in events if e["type"] == "delta") == \
            "Here is an outline."
        assert events[-1]["type"] == "done"
        assert events[-1]["conversation_id"]

        saved = Message.query.filter_by(role="assistant").all()
        assert [m.content for m in saved] == ["Here is an outline."]

    def test_requires_authentication(self, client):
        res = client.post("/api/chat/stream", json={"question": "Hi"})
        assert res.status_code == 401

    def test_billing_gate_applies(self, auth_client, church):
        from datetime import datetime, timedelta
        church.billing_exempt = False
        church.trial_ends_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()

        with patch("routes.chat.stream_gemini") as streamer:
            res = auth_client.post("/api/chat/stream", json={"question": "Hi"})
        assert res.status_code == 402
        streamer.assert_not_called()

    def test_usage_is_metered_from_the_stream(self, auth_client, church):
        streamer = _streamer("Draft.", usage={
            "model": "gemini-2.5-flash-lite", "prompt_tokens": 900,
            "response_tokens": 100, "total_tokens": 1000,
        })
        with patch("routes.chat.stream_gemini", side_effect=streamer):
            res = auth_client.post("/api/chat/stream", json={"question": "Bulletin"})
        _events(res)

        row = UsageDaily.query.filter_by(church_id=church.id, surface=STAFF).one()
        assert row.total_tokens == 1000


# ── Session detachment ───────────────────────────────────────────────────────

class TestTurnCarriesNoOrmInstances:
    """A streaming response generator runs *after* its request context is gone,
    so any ORM object it closed over is detached and every attribute access
    raises. This shipped once and cost the answer's feedback controls and its
    database row; the Flask test client keeps the context alive, so no
    request-level test catches it. These pin the invariant that prevents it.
    """

    def _assert_plain(self, turn):
        from models import db as _db
        for key, value in turn.items():
            assert not isinstance(value, _db.Model), (
                f"turn[{key!r}] is a live ORM instance; the streaming generator "
                f"cannot reach it. Carry its id and re-query instead."
            )

    def test_widget_turn_is_plain_data(self, app, church):
        from routes.widget import _prepare_widget_turn
        with app.test_request_context():
            turn = _prepare_widget_turn({
                "church_id": church.id, "question": "What time is worship?",
            })
        self._assert_plain(turn)
        assert isinstance(turn["wconv_id"], int)

    def test_staff_turn_is_plain_data(self, app, admin_user):
        from flask_login import login_user
        from routes.chat import _prepare_chat_turn
        with app.test_request_context():
            login_user(admin_user)
            turn = _prepare_chat_turn({"question": "Sermon help"})
        self._assert_plain(turn)
        assert isinstance(turn["conv_id"], int)

    def test_the_user_message_is_committed_before_streaming_begins(self, app, church):
        """Nothing may be left pending in a session the generator cannot reach."""
        from routes.widget import _prepare_widget_turn
        with app.test_request_context():
            _prepare_widget_turn({"church_id": church.id, "question": "Hi?"})
            assert not db.session.new
            assert not db.session.dirty
        assert WidgetMessage.query.filter_by(role="user").count() == 1


# ── Both paths stay in step ──────────────────────────────────────────────────

class TestBlockingAndStreamingAgree:
    def test_both_widget_paths_produce_the_same_answer_and_sources(self, client, church):
        web = [{"content": "Worship is at 10am.", "source": "Times",
                "location": "https://church.org/times"}]
        answer = "Worship is at 10am. [1]"
        loaders = {
            "routes.widget.load_church_web_content": web,
            "routes.widget.load_chatbot_documents": [],
            "routes.widget.load_curated_content": [],
        }
        with patch(list(loaders)[0], return_value=web), \
             patch(list(loaders)[1], return_value=[]), \
             patch(list(loaders)[2], return_value=[]), \
             patch("routes.widget.call_gemini", return_value=answer), \
             patch("routes.widget.stream_gemini", side_effect=_streamer(answer)):
            blocking = client.post("/api/widget/chat", json={
                "church_id": church.id, "question": "What time is worship?"}).get_json()
            streamed = _events(client.post("/api/widget/chat/stream", json={
                "church_id": church.id, "question": "What time is worship?"}))[-1]

        assert blocking["answer"] == answer
        assert blocking["sources"] == streamed["sources"]
