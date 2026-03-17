"""Shared pytest fixtures for Wesley AI tests."""

import shutil
import tempfile
from pathlib import Path

import pytest
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

from app import create_app
from models import db as _db, Church, User, SystemPrompt


@pytest.fixture(scope="session")
def app():
    """Session-scoped Flask test application using an in-memory SQLite database.

    The app is created once per test session. Individual test fixtures handle
    their own data setup and teardown to keep tests isolated.
    """
    application = create_app(testing=True)

    # Use a throw-away temp directory for file uploads so tests never touch
    # the real data/uploads folder and cleanup is automatic.
    _tmp_uploads = Path(tempfile.mkdtemp(prefix="wesley_test_uploads_"))
    application.config["UPLOADS_DIR"] = _tmp_uploads

    ctx = application.app_context()
    ctx.push()

    _db.create_all()

    # Seed the system prompt row that many routes expect to exist
    if not SystemPrompt.query.get(1):
        _db.session.add(SystemPrompt(id=1, content="You are a helpful church assistant."))
        _db.session.commit()

    yield application

    _db.session.remove()
    _db.drop_all()
    ctx.pop()
    shutil.rmtree(_tmp_uploads, ignore_errors=True)


@pytest.fixture
def client(app):
    """A plain (unauthenticated) Flask test client."""
    return app.test_client()


# ── Data fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def church(app):
    """A test church with an active billing-exempt trial."""
    c = Church(
        name="Grace Community Church",
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
        billing_exempt=True,
    )
    _db.session.add(c)
    _db.session.commit()
    _db.session.refresh(c)
    yield c
    # Teardown — delete dependent records first to avoid FK errors
    User.query.filter_by(church_id=c.id).delete()
    Church.query.filter_by(id=c.id).delete()
    _db.session.commit()


_TEST_PASSWORD = "SecureTestPass1!"


@pytest.fixture(autouse=True)
def reset_db_session(app):
    """Roll back any uncommitted DB changes after every test and clear Flask-Login's
    cached user from ``g`` so login state cannot leak between tests.

    Flask-Login stores the current user in ``flask.g._login_user``.  Because we
    push a single long-lived app context for the whole test session, ``g``
    persists across requests.  Clearing ``_login_user`` forces Flask-Login to
    re-evaluate the session cookie on the next request, so unauthenticated test
    clients correctly receive 401 responses even after a previous test logged in.
    """
    yield
    # Clear Flask-Login's user cache so the next test starts unauthenticated.
    from flask import g
    g.pop("_login_user", None)
    _db.session.rollback()


@pytest.fixture
def admin_user(app, church):
    """An admin user belonging to the test church."""
    u = User(
        email="admin@gracecc.org",
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256"),
        church_id=church.id,
        role="admin",
    )
    _db.session.add(u)
    _db.session.commit()
    _db.session.refresh(u)
    # Stash the plaintext password so tests can use it without knowing the constant
    u._plaintext_password = _TEST_PASSWORD
    yield u
    User.query.filter_by(id=u.id).delete()
    _db.session.commit()


@pytest.fixture
def auth_client(client, admin_user):
    """A test client already logged in as the admin user.

    Returns the client so tests can make authenticated requests directly.
    """
    res = client.post("/api/auth/login", json={
        "email": admin_user.email,
        "password": admin_user._plaintext_password,
    })
    assert res.status_code == 200, f"Login failed in fixture: {res.get_json()}"
    return client
