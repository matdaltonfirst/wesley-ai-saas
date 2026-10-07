"""Roles, the permission matrix, team management and the audit log."""

import json
from unittest.mock import patch

import pytest

import permissions as P
from models import AuditLog, RolePermission, User, UserRole, db
from tests.conftest import login, make_user


# ── Structure: default deny ───────────────────────────────────────────────────

# Endpoints that are intentionally open to the world or handle their own auth.
OPEN_PREFIXES = ("static", "public.", "auth.")
# Staff endpoints that only need a signed-in person by design (none today).
OPEN_ENDPOINTS = set()


class TestDefaultDeny:
    def test_every_non_public_route_declares_a_permission(self, app):
        undeclared = []
        for rule in app.url_map.iter_rules():
            endpoint = rule.endpoint
            if endpoint.startswith(OPEN_PREFIXES) or endpoint in OPEN_ENDPOINTS:
                continue
            view = app.view_functions[endpoint]
            if not getattr(view, "required_permissions", None):
                undeclared.append(f"{rule.rule} ({endpoint})")
        assert not undeclared, "routes without a permission check:\n" + "\n".join(undeclared)

    def test_every_declared_permission_exists(self, app):
        for rule in app.url_map.iter_rules():
            for p in getattr(app.view_functions[rule.endpoint], "required_permissions", ()):
                assert p in P.PERMISSIONS, (rule.rule, p)

    def test_a_person_with_no_role_can_do_nothing(self, client, church):
        login(client, make_user("norole@daltonfumc.com", []))
        for path in ("/", "/dashboard", "/api/conversations", "/api/documents", "/api/team",
                     "/api/guest-connections", "/api/packets", "/comms", "/api/qna"):
            assert client.get(path).status_code == 403, path
        assert client.post("/api/chat", json={"question": "hi"}).status_code == 403

    def test_every_role_default_only_uses_defined_permissions(self):
        for role, perms in P.DEFAULTS.items():
            assert role in P.ROLES and perms <= set(P.PERMISSIONS)


# ── Sensitive data does not exist as a permission ─────────────────────────────

class TestSensitiveDomains:
    def test_forbidden_domains_are_not_permissions(self):
        for key in P.PERMISSIONS:
            for word in P.FORBIDDEN_DOMAINS:
                assert word not in key, f"{key} names a forbidden domain"

    def test_nobody_has_giving_by_default_not_even_admin(self):
        assert all("data.giving_aggregate" not in perms for perms in P.DEFAULTS.values())

    def test_an_admin_cannot_grant_a_domain_that_does_not_exist(self, auth_client):
        for bad in ("data.pastoral_notes", "data.background_checks", "data.child_names",
                    "data.giving_by_donor"):
            res = auth_client.post("/api/permissions",
                                   json={"role": "admin", "permission": bad, "allowed": True})
            assert res.status_code == 400, bad

    def test_giving_aggregate_can_be_granted_by_name_and_is_audited(self, auth_client):
        res = auth_client.post("/api/permissions", json={
            "role": "pastoral", "permission": "data.giving_aggregate", "allowed": True})
        assert res.status_code == 200
        assert "data.giving_aggregate" in P.matrix()["pastoral"]
        entry = AuditLog.query.filter_by(action="permissions.change").one()
        assert entry.source == "pastoral:data.giving_aggregate"


# ── What each role can reach ──────────────────────────────────────────────────

# (role, path) -> allowed?  GET endpoints that do not call out to anything.
ACCESS = [
    ("admin",           "/api/team", True),
    ("admin",           "/api/audit", True),
    ("admin",           "/api/documents", True),
    ("comms",           "/api/documents", True),
    ("comms",           "/api/team", False),
    ("comms",           "/api/audit", False),
    ("comms",           "/api/packets", True),
    ("admin_assistant", "/api/guest-connections", True),
    ("admin_assistant", "/api/documents", True),
    ("admin_assistant", "/api/team", False),
    ("admin_assistant", "/api/church/theology", False),
    ("family",          "/api/guest-connections", False),
    ("family",          "/api/documents", False),
    ("family",          "/api/packets", False),
    ("family",          "/api/conversations", True),
    ("music",           "/api/guest-connections", False),
    ("music",           "/api/widget/conversations", False),
    ("music",           "/api/conversations", True),
    ("pastoral",        "/api/guest-connections", True),
    ("pastoral",        "/api/packets", True),
    ("pastoral",        "/api/church/theology", True),
    ("pastoral",        "/api/documents", False),
    ("pastoral",        "/api/team", False),
]


class TestRoleAccess:
    @pytest.mark.parametrize("role,path,allowed", ACCESS)
    def test_access(self, client, church, role, path, allowed):
        login(client, make_user(f"{role}@daltonfumc.com", [role]))
        status = client.get(path).status_code
        assert (status != 403) == allowed, f"{role} {path} -> {status}"

    def test_a_person_with_two_roles_gets_both(self, client, church):
        login(client, make_user("both@daltonfumc.com", ["comms", "pastoral"]))
        assert client.get("/api/documents").status_code == 200          # comms
        assert client.get("/api/church/theology").status_code == 200    # pastoral

    def test_writes_need_the_write_permission_not_just_read(self, client, church):
        login(client, make_user("a@daltonfumc.com", ["admin_assistant"]))
        assert client.get("/api/qna").status_code == 200                # kb.read
        assert client.post("/api/qna", json={"question": "q", "answer": "a"}).status_code == 403

    def test_a_denied_request_is_logged(self, client, church):
        login(client, make_user("m@daltonfumc.com", ["music"]))
        client.get("/api/team")
        entry = AuditLog.query.filter_by(action="access.denied").one()
        assert entry.email == "m@daltonfumc.com" and entry.source == "/api/team"
        assert json.loads(entry.detail)["needs"] == ["team.manage"]

    def test_the_forbidden_page_is_shown_to_a_browser(self, client, church):
        login(client, make_user("m@daltonfumc.com", ["music"]))
        res = client.get("/dashboard")
        assert res.status_code == 403 and b"do not have access" in res.data


class TestOverrides:
    def test_an_admin_can_grant_and_revoke_a_permission_for_a_role(self, auth_client, client, church):
        assert auth_client.post("/api/permissions", json={
            "role": "music", "permission": "guests.read", "allowed": True}).status_code == 200
        assert "guests.read" in P.matrix()["music"]
        assert RolePermission.query.count() == 1
        auth_client.post("/api/permissions", json={
            "role": "music", "permission": "guests.read", "allowed": False})
        assert RolePermission.query.count() == 0          # back to default: no override row

    def test_the_override_takes_effect_for_people_in_that_role(self, app, client, church):
        from flask import g
        music = make_user("m@daltonfumc.com", ["music"])
        login(client, music)
        assert client.get("/api/guest-connections").status_code == 403
        db.session.add(RolePermission(role="music", permission="guests.read", allowed=True))
        db.session.commit()
        g.pop("_perm_cache", None)
        assert client.get("/api/guest-connections").status_code == 200

    def test_admins_cannot_lock_themselves_out_of_team_management(self, auth_client):
        res = auth_client.post("/api/permissions", json={
            "role": "admin", "permission": "team.manage", "allowed": False})
        assert res.status_code == 400
        assert "team.manage" in P.matrix()["admin"]

    def test_unknown_roles_and_non_boolean_values_are_rejected(self, auth_client):
        assert auth_client.post("/api/permissions", json={"role": "wizard", "permission": "chat.use", "allowed": True}).status_code == 400
        assert auth_client.post("/api/permissions", json={"role": "music", "permission": "chat.use", "allowed": "yes"}).status_code == 400

    def test_the_matrix_endpoint_lists_roles_permissions_and_defaults(self, auth_client):
        data = auth_client.get("/api/permissions").get_json()
        assert {r["key"] for r in data["roles"]} == set(P.ROLES)
        assert "chat.use" in data["matrix"]["music"] and "data.giving_aggregate" not in data["matrix"]["admin"]


# ── Team ──────────────────────────────────────────────────────────────────────

class TestTeam:
    def test_add_a_person_with_roles(self, auth_client, church):
        res = auth_client.post("/api/team", json={
            "email": "Carrie@DaltonFUMC.com", "display_name": "Carrie Ashcraft",
            "roles": ["admin_assistant"]})
        assert res.status_code == 201
        user = User.query.filter_by(email="carrie@daltonfumc.com").one()
        assert user.roles == {"admin_assistant"} and user.display_name == "Carrie Ashcraft"
        assert AuditLog.query.filter_by(action="team.add").count() == 1

    def test_only_church_domain_addresses_can_be_added(self, auth_client):
        assert auth_client.post("/api/team", json={"email": "x@gmail.com", "roles": []}).status_code == 400
        assert auth_client.post("/api/team", json={"email": "x@daltonfumc.com.evil.com", "roles": []}).status_code == 400

    def test_unknown_roles_and_duplicates_are_rejected(self, auth_client):
        assert auth_client.post("/api/team", json={"email": "a@daltonfumc.com", "roles": ["wizard"]}).status_code == 400
        auth_client.post("/api/team", json={"email": "a@daltonfumc.com", "roles": []})
        assert auth_client.post("/api/team", json={"email": "a@daltonfumc.com", "roles": []}).status_code == 400

    def test_change_roles_is_audited_with_before_and_after(self, auth_client):
        person = make_user("a@daltonfumc.com", ["music"])
        res = auth_client.patch(f"/api/team/{person.id}", json={"roles": ["comms", "pastoral"]})
        assert res.status_code == 200
        assert User.query.get(person.id).roles == {"comms", "pastoral"}
        detail = json.loads(AuditLog.query.filter_by(action="team.update").one().detail)
        assert detail["before"]["roles"] == ["music"] and detail["after"]["roles"] == ["comms", "pastoral"]

    def test_the_last_admin_cannot_be_demoted_or_deactivated(self, auth_client, admin_user):
        assert auth_client.patch(f"/api/team/{admin_user.id}", json={"roles": ["comms"]}).status_code == 400
        assert auth_client.patch(f"/api/team/{admin_user.id}", json={"active": False}).status_code == 400
        assert "admin" in User.query.get(admin_user.id).roles

    def test_an_admin_can_step_down_when_another_admin_exists(self, auth_client, admin_user):
        make_user("second@daltonfumc.com", ["admin"])
        assert auth_client.patch(f"/api/team/{admin_user.id}", json={"roles": ["comms"]}).status_code == 200

    def test_nobody_can_deactivate_themselves(self, auth_client, admin_user):
        make_user("second@daltonfumc.com", ["admin"])
        res = auth_client.patch(f"/api/team/{admin_user.id}", json={"active": False})
        assert res.status_code == 400 and "yourself" in res.get_json()["error"]

    def test_a_deactivated_person_cannot_sign_in(self, auth_client, client):
        person = make_user("a@daltonfumc.com", ["comms"])
        auth_client.patch(f"/api/team/{person.id}", json={"active": False})
        res = client.post("/api/auth/login", json={"email": person.email, "password": person._plaintext_password})
        assert res.status_code == 401

    def test_only_team_managers_can_use_these_endpoints(self, client, church):
        person = make_user("c@daltonfumc.com", ["comms"])
        login(client, person)
        assert client.get("/api/team").status_code == 403
        assert client.post("/api/team", json={"email": "z@daltonfumc.com", "roles": []}).status_code == 403
        assert client.patch(f"/api/team/{person.id}", json={"roles": ["admin"]}).status_code == 403
        assert User.query.get(person.id).roles == {"comms"}      # no self-promotion


# ── Audit log ─────────────────────────────────────────────────────────────────

class TestAuditLog:
    def test_ai_chat_records_which_sources_and_never_the_question(self, auth_client, admin_user):
        from models import QnAPair
        db.session.add(QnAPair(question="Do you baptize infants?", answer="Yes, call the office."))
        db.session.commit()
        secret = "my-very-private-question-xyz"
        with patch("routes.chat.call_gemini", return_value="ok"):
            auth_client.post("/api/chat", json={"question": f"Do you baptize infants? {secret}"})
        entry = AuditLog.query.filter_by(action="ai.chat").one()
        assert entry.email == admin_user.email
        assert secret not in (entry.detail or "") and secret not in (entry.source or "")
        detail = json.loads(entry.detail)
        assert detail["surface"] == "staff_chat" and detail["question_chars"] > 0
        assert any(s["type"] == "approved_answer" for s in detail["sources"])

    def test_the_log_can_be_read_with_audit_permission_and_filtered(self, auth_client):
        auth_client.get("/api/team")
        data = auth_client.get("/api/audit?action=auth").get_json()
        assert data["entries"] and all(e["action"].startswith("auth") for e in data["entries"])

    def test_only_audit_readers_can_read_it(self, client, church):
        login(client, make_user("c@daltonfumc.com", ["comms"]))
        assert client.get("/api/audit").status_code == 403

    def test_there_is_no_way_to_edit_or_delete_the_log(self, app):
        for rule in app.url_map.iter_rules():
            if rule.rule.startswith("/api/audit"):
                assert rule.methods - {"GET", "HEAD", "OPTIONS"} == set()

    def test_sign_in_and_out_are_logged(self, client, church):
        person = make_user("a@daltonfumc.com", ["admin"])
        login(client, person)
        client.get("/logout")
        actions = [e.action for e in AuditLog.query.order_by(AuditLog.id)]
        assert "auth.login" in actions and "auth.logout" in actions

    def test_a_failed_password_login_is_logged_without_the_password(self, client, church):
        make_user("a@daltonfumc.com", ["admin"])
        client.post("/api/auth/login", json={"email": "a@daltonfumc.com", "password": "wrong-password-123"})
        entry = AuditLog.query.filter_by(action="auth.denied").one()
        assert "wrong-password-123" not in (entry.detail or "")

    def test_a_logging_failure_never_breaks_the_action(self, auth_client):
        with patch("audit.db.session.add", side_effect=RuntimeError("log db down")):
            assert auth_client.get("/api/team").status_code == 200

    def test_the_nightly_cleanup_jobs_do_not_touch_the_audit_log(self):
        import inspect, app as appmod
        for job in (appmod.nightly_cleanup_job, appmod.nightly_widget_cleanup_job):
            assert "AuditLog" not in inspect.getsource(job)


# ── Home screen and audience controls ────────────────────────────────────────

class TestHome:
    def test_each_role_sees_cards_for_what_it_can_do(self, client, church):
        from models import GuestConnection
        db.session.add(GuestConnection(name="A", email="a@b.co")); db.session.commit()
        login(client, make_user("aa@daltonfumc.com", ["admin_assistant"]))
        html = client.get("/home").get_data(as_text=True)
        assert "Guests to follow up" in html and "Communications queue" in html
        assert "People and roles" not in html

    def test_music_sees_only_the_basics(self, client, church):
        login(client, make_user("m@daltonfumc.com", ["music"]))
        html = client.get("/home").get_data(as_text=True)
        assert "Ask Wesley" in html and "Guests to follow up" not in html

    def test_a_person_with_no_role_is_told_to_ask_an_admin(self, client, church):
        login(client, make_user("n@daltonfumc.com", []))
        assert client.get("/home").status_code == 403

    def test_the_dashboard_nav_only_lists_what_the_role_can_use(self, client, church):
        login(client, make_user("p@daltonfumc.com", ["pastoral"]))
        html = client.get("/dashboard").get_data(as_text=True)
        assert 'data-panel="guest-connections"' in html and 'data-panel="sermon-packets"' in html
        assert 'data-panel="files"' not in html and 'data-panel="team"' not in html
        assert 'data-panel="audit"' not in html


class TestCalendarAudience:
    def test_a_calendar_can_be_made_staff_only_and_back(self, auth_client, church):
        from models import ChurchCalendar
        cal = ChurchCalendar(url="https://x/a.ics", label="Staff meetings")
        db.session.add(cal); db.session.commit()
        assert cal.audience == "public"
        assert auth_client.patch(f"/api/calendars/{cal.id}", json={"audience": "staff"}).status_code == 200
        assert ChurchCalendar.query.get(cal.id).audience == "staff"
        assert auth_client.patch(f"/api/calendars/{cal.id}", json={"audience": "everyone"}).status_code == 400
        assert AuditLog.query.filter_by(action="calendar.audience").count() == 1

    def test_only_source_managers_can_change_it(self, client, church):
        from models import ChurchCalendar
        cal = ChurchCalendar(url="https://x/a.ics", label="x"); db.session.add(cal); db.session.commit()
        login(client, make_user("m@daltonfumc.com", ["music"]))
        assert client.patch(f"/api/calendars/{cal.id}", json={"audience": "staff"}).status_code == 403


class TestSnippetAndQnaAudience:
    def test_new_items_default_to_public_and_can_be_staff_only(self, auth_client):
        a = auth_client.post("/api/snippets", json={"title": "t", "content": "c"}).get_json()["snippet"]
        b = auth_client.post("/api/snippets", json={"title": "t2", "content": "c2", "audience": "staff"}).get_json()["snippet"]
        assert a["audience"] == "public" and b["audience"] == "staff"
        q = auth_client.post("/api/qna", json={"question": "q", "answer": "a", "audience": "staff"}).get_json()["pair"]
        assert q["audience"] == "staff"
        assert auth_client.patch(f"/api/qna/{q['id']}", json={"audience": "public"}).get_json()["pair"]["audience"] == "public"

    def test_an_unrecognised_audience_means_staff_only(self, auth_client):
        s = auth_client.post("/api/snippets", json={"title": "t", "content": "c", "audience": "everyone"}).get_json()["snippet"]
        assert s["audience"] == "staff"
