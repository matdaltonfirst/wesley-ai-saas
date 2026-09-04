"""Tests for the onboarding wizard flow."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from denominations import PROFILES
from models import db, PcoConnection


class TestOnboardingPage:
    def test_completed_church_redirects_to_chat(self, auth_client, church):
        church.onboarding_complete = True
        db.session.commit()
        res = auth_client.get("/onboarding")
        assert res.status_code == 302

    def test_step_param_resumes_even_when_complete(self, auth_client, church):
        church.onboarding_complete = True
        db.session.commit()
        res = auth_client.get("/onboarding?step=5&pco=connected")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "RESUME_STEP = 5" in html
        assert 'PCO_RESULT = "connected"' in html

    def test_wizard_renders_all_six_steps(self, auth_client, church):
        church.onboarding_complete = False
        db.session.commit()
        res = auth_client.get("/onboarding")
        html = res.get_data(as_text=True)
        for marker in ("Connect your events calendar", "Connect your YouTube channel",
                       "Connect Planning Center", "Step 6 of 6", "obCalSkip",
                       "obYtSkip", "obPcoSkip"):
            assert marker in html, marker
        church.onboarding_complete = True
        db.session.commit()


class TestOnboardingDenomination:
    def test_wizard_shows_friendly_names_and_preselects_nothing(self, auth_client, church):
        church.onboarding_complete = False
        db.session.commit()
        res = auth_client.get("/onboarding")
        html = res.get_data(as_text=True)
        try:
            assert "Denominational affiliation" in html
            assert '<option value="" selected>Select your affiliation…</option>' in html
            for profile in PROFILES.values():
                assert f'<option value="{profile.key}">' in html
                assert profile.display_name in html
            # UMC must not be preselected for a brand-new church.
            assert '<option value="umc" selected>' not in html
            # The choice's consequence is explained, not just labelled.
            assert "theological and denominational" in html
        finally:
            church.onboarding_complete = True
            db.session.commit()

    def test_step1_saves_selected_denomination(self, auth_client, church):
        res = auth_client.post("/api/onboarding/step1", json={
            "church_name": "Cornerstone Fellowship",
            "church_city": "Dalton, GA",
            "denomination": "non_denominational",
        })
        assert res.status_code == 200
        db.session.refresh(church)
        assert church.denomination == "non_denominational"
        assert church.denomination_profile_version == PROFILES["non_denominational"].version
        assert church.denomination_updated_at is not None
        assert church.onboarding_complete is True
        church.denomination = "umc"
        db.session.commit()

    def test_step1_requires_a_denomination(self, auth_client, church):
        res = auth_client.post("/api/onboarding/step1", json={
            "church_name": "Cornerstone Fellowship",
        })
        assert res.status_code == 400
        assert "affiliation" in res.get_json()["error"].lower()

    @pytest.mark.parametrize("bad", ["episcopal", "UMC", 5, ["umc"], {"k": "v"}])
    def test_step1_rejects_client_supplied_keys(self, auth_client, church, bad):
        res = auth_client.post("/api/onboarding/step1", json={
            "church_name": "Cornerstone Fellowship",
            "denomination": bad,
        })
        assert res.status_code == 400
        db.session.refresh(church)
        assert church.denomination == "umc"


class TestPcoOnboardingRoundTrip:
    def test_callback_returns_to_wizard(self, auth_client, church):
        with patch("pco.PCO_CLIENT_ID", "cid"), patch("pco.PCO_CLIENT_SECRET", "sec"):
            auth_client.get("/pco/connect?return=onboarding")
            with auth_client.session_transaction() as sess:
                state = sess["pco_oauth_state"]
                assert sess["pco_return"] == "onboarding"
            with patch("routes.pco_routes.pco.exchange_code", return_value={
                        "access_token": "a", "refresh_token": "r", "expires_in": 7200}), \
                 patch("routes.pco_routes.pco.encrypt_token", side_effect=lambda t: t), \
                 patch("routes.pco_routes.pco.get_organization_name",
                       return_value="Grace UMC"):
                res = auth_client.get(f"/pco/callback?code=x&state={state}")
        assert res.status_code == 302
        assert res.headers["Location"].endswith("/onboarding?step=5&pco=connected")
        PcoConnection.query.filter_by(church_id=church.id).delete()
        db.session.commit()

    def test_callback_defaults_to_dashboard(self, auth_client, church):
        with patch("pco.PCO_CLIENT_ID", "cid"), patch("pco.PCO_CLIENT_SECRET", "sec"):
            auth_client.get("/pco/connect")
            with auth_client.session_transaction() as sess:
                state = sess["pco_oauth_state"]
            with patch("routes.pco_routes.pco.exchange_code", return_value={
                        "access_token": "a", "refresh_token": "r", "expires_in": 7200}), \
                 patch("routes.pco_routes.pco.encrypt_token", side_effect=lambda t: t), \
                 patch("routes.pco_routes.pco.get_organization_name",
                       return_value="Grace UMC"):
                res = auth_client.get(f"/pco/callback?code=x&state={state}")
        assert res.status_code == 302
        assert res.headers["Location"].endswith("/dashboard#integrations")
        PcoConnection.query.filter_by(church_id=church.id).delete()
        db.session.commit()

    def test_denied_consent_returns_to_wizard(self, auth_client, church):
        with patch("pco.PCO_CLIENT_ID", "cid"), patch("pco.PCO_CLIENT_SECRET", "sec"):
            auth_client.get("/pco/connect?return=onboarding")
            with auth_client.session_transaction() as sess:
                state = sess["pco_oauth_state"]
            res = auth_client.get(f"/pco/callback?error=access_denied&state={state}")
        assert res.status_code == 302
        assert res.headers["Location"].endswith("/onboarding?step=5&pco=denied")
