"""Public signup is closed unless explicitly enabled."""


def test_signup_api_rejected_when_closed(app, client):
    app.config["SIGNUP_ENABLED"] = False
    try:
        res = client.post("/api/auth/signup", json={
            "email": "x@example.com", "password": "password123", "church_name": "X"})
        assert res.status_code == 403
    finally:
        app.config["SIGNUP_ENABLED"] = True


def test_signup_page_redirects_and_login_hides_link(app, client):
    app.config["SIGNUP_ENABLED"] = False
    try:
        res = client.get("/signup")
        assert res.status_code == 302 and "/login" in res.headers["Location"]
        assert b"Create one" not in client.get("/login").data
    finally:
        app.config["SIGNUP_ENABLED"] = True
