"""Tests for settings routes: branding, church URL, staff management."""

import pytest
from models import db, User
from werkzeug.security import generate_password_hash


# ── Branding ──────────────────────────────────────────────────────────────────

class TestBranding:
    def test_get_branding_requires_auth(self, client):
        res = client.get("/api/church/branding")
        assert res.status_code == 401

    def test_get_branding_authenticated(self, auth_client):
        res = auth_client.get("/api/church/branding")
        assert res.status_code == 200
        data = res.get_json()
        assert "bot_name" in data
        assert "welcome_message" in data
        assert "primary_color" in data

    def test_save_branding_valid(self, auth_client):
        res = auth_client.post("/api/church/branding", json={
            "bot_name": "FaithBot",
            "welcome_message": "Welcome to our church!",
            "primary_color": "#1a2b3c",
            "bot_subtitle": "Your church assistant",
            "church_city": "Nashville, TN",
            "starter_questions": ["What time is Sunday service?"],
        })
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

        # Verify the value was actually saved
        get_res = auth_client.get("/api/church/branding")
        saved = get_res.get_json()
        assert saved["bot_name"] == "FaithBot"
        assert saved["primary_color"] == "#1a2b3c"

    def test_save_branding_empty_bot_name(self, auth_client):
        res = auth_client.post("/api/church/branding", json={
            "bot_name": "",
            "welcome_message": "Hello!",
        })
        assert res.status_code == 400
        assert "bot name" in res.get_json()["error"].lower()

    def test_save_branding_empty_welcome_message(self, auth_client):
        res = auth_client.post("/api/church/branding", json={
            "bot_name": "Wesley",
            "welcome_message": "",
        })
        assert res.status_code == 400

    def test_save_branding_invalid_color(self, auth_client):
        res = auth_client.post("/api/church/branding", json={
            "bot_name": "Wesley",
            "welcome_message": "Hello!",
            "primary_color": "not-a-color",
        })
        assert res.status_code == 400
        assert "hex" in res.get_json()["error"].lower()

    def test_save_branding_valid_color_formats(self, auth_client):
        for color in ["#ffffff", "#000000", "#1A2B3C"]:
            res = auth_client.post("/api/church/branding", json={
                "bot_name": "Wesley",
                "welcome_message": "Hello!",
                "primary_color": color,
            })
            assert res.status_code == 200, f"Color {color} should be valid"

    def test_save_branding_requires_auth(self, client):
        res = client.post("/api/church/branding", json={
            "bot_name": "Wesley",
            "welcome_message": "Hello!",
        })
        assert res.status_code == 401


# ── Church settings (website URL) ────────────────────────────────────────────

class TestChurchSettings:
    def test_get_settings_requires_auth(self, client):
        res = client.get("/api/church/settings")
        assert res.status_code == 401

    def test_get_settings_authenticated(self, auth_client):
        res = auth_client.get("/api/church/settings")
        assert res.status_code == 200
        data = res.get_json()
        assert "website_url" in data
        assert "page_count" in data

    def test_save_valid_url(self, auth_client):
        res = auth_client.post("/api/church/settings", json={
            "website_url": "https://www.mychurch.org",
        })
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

    def test_save_url_without_scheme_rejected(self, auth_client):
        res = auth_client.post("/api/church/settings", json={
            "website_url": "www.mychurch.org",
        })
        assert res.status_code == 400
        assert "http" in res.get_json()["error"].lower()

    def test_save_url_too_long(self, auth_client):
        res = auth_client.post("/api/church/settings", json={
            "website_url": "https://example.com/" + "a" * 490,
        })
        assert res.status_code == 400
        assert "500" in res.get_json()["error"]

    def test_save_empty_url_clears_value(self, auth_client):
        # First set a URL
        auth_client.post("/api/church/settings", json={"website_url": "https://mychurch.org"})
        # Then clear it
        res = auth_client.post("/api/church/settings", json={"website_url": ""})
        assert res.status_code == 200

    def test_save_settings_requires_auth(self, client):
        res = client.post("/api/church/settings", json={"website_url": "https://x.com"})
        assert res.status_code == 401


# ── Staff management ──────────────────────────────────────────────────────────
