"""Who may do what: roles, permissions, and the default matrix.

The matrix below is the code form of docs/ROLES.md. Three rules shape it:

1. **Default deny.** A person can do only what one of their roles grants.
2. **Sensitive domains do not exist here.** Pastoral care notes, background
   checks and children's names are not permissions at all, so no role, override
   or admin screen can ever grant them. ``FORBIDDEN_DOMAINS`` exists so a test
   can prove nobody added them later.
3. **Giving is defined but granted to no role.** An admin must grant it by name,
   and it is aggregate and read-only by definition.

Admins can override the defaults per role (``RolePermission`` rows). An override
can only use permissions defined here.
"""

from functools import wraps

from flask import g, jsonify, redirect, request, url_for
from flask_login import current_user

ADMIN = "admin"
COMMS = "comms"
ASSISTANT = "admin_assistant"
FAMILY = "family"
MUSIC = "music"
PASTORAL = "pastoral"

ROLES = {
    ADMIN: "Admin",
    COMMS: "Communications and Production",
    ASSISTANT: "Administrative Assistant",
    FAMILY: "Family Ministries",
    MUSIC: "Music and Worship",
    PASTORAL: "Pastoral",
}

# permission key -> (group, plain-language description)
PERMISSIONS = {
    # Modules
    "chat.use":              ("Modules", "Use the staff AI chat"),
    "sunday.read":           ("Modules", "See Sunday Content drafts"),
    "sunday.write":          ("Modules", "Edit and regenerate Sunday Content"),
    "sunday.approve":        ("Modules", "Approve Sunday Content"),
    "events.read":           ("Modules", "See event checklists and the timeline"),
    "events.write":          ("Modules", "Change event checklists"),
    "requests.submit":       ("Modules", "Submit communications requests"),
    "requests.manage":       ("Modules", "See and triage the communications queue"),
    "streaming.read":        ("Modules", "See the weekly streaming numbers"),
    "streaming.write":       ("Modules", "Enter and correct streaming numbers"),
    "guests.read":           ("Modules", "See guest connections"),
    "guests.write":          ("Modules", "Update and sync guest connections"),
    "chatlogs.read":         ("Modules", "Read public chatbot conversations and analytics"),
    "chatlogs.write":        ("Modules", "Correct public chatbot answers"),
    "kb.read":               ("Modules", "See the knowledge base (documents, snippets, Q&A)"),
    "kb.write":              ("Modules", "Change the knowledge base"),
    "sources.write":         ("Modules", "Manage the website crawl, calendar feeds and sermon source"),
    "integrations.read":     ("Modules", "See the integrations status page"),
    "integrations.manage":   ("Modules", "Connect and disconnect integrations"),
    "team.manage":           ("Modules", "Manage people, roles and permissions"),
    "instructions.read":     ("Modules", "See the public chatbot instructions and local practice"),
    "instructions.write":    ("Modules", "Change the public chatbot instructions and local practice"),
    "audit.read":            ("Modules", "Read the audit log"),
    # Data the AI and dashboards may read
    "data.calendar":         ("Data", "Calendar and events"),
    "data.services":         ("Data", "Service plans, songs and team schedules"),
    "data.groups":           ("Data", "Groups and memberships"),
    "data.registrations":    ("Data", "Event registrations"),
    "data.people_basic":     ("Data", "People: name, email and phone only"),
    "data.checkins_counts":  ("Data", "Check-in attendance, aggregate counts only"),
    "data.sermons":          ("Data", "Sermons and episodes"),
    "data.streams":          ("Data", "Stream and platform numbers"),
    "data.email_stats":      ("Data", "Email campaign statistics"),
    "data.text_in_church":   ("Data", "Text In Church contacts and conversations"),
    "data.giving_aggregate": ("Data", "Giving, aggregate and read-only"),
}

# Never permissions. A test asserts none of these words appear in PERMISSIONS.
FORBIDDEN_DOMAINS = (
    "pastoral_notes", "pastoral_care", "background_check", "checkin_names",
    "child_names", "giving_donor", "giving_by_donor",
)

# Everyone may chat (the AI draws on the shared staff knowledge base), submit a
# request, and see the calendar. Seeing the knowledge-base screens is separate.
_EVERYONE = {"chat.use", "requests.submit", "data.calendar"}

# Giving is the one permission no role gets by default, Admin included.
_NOT_BY_DEFAULT = {"data.giving_aggregate"}

DEFAULTS = {
    ADMIN: set(PERMISSIONS) - _NOT_BY_DEFAULT,
    COMMS: _EVERYONE | {
        "sunday.read", "sunday.write", "sunday.approve", "events.read", "events.write",
        "requests.manage", "streaming.read", "streaming.write", "guests.read",
        "chatlogs.read", "chatlogs.write", "kb.read", "kb.write", "sources.write",
        "integrations.read", "data.services", "data.groups", "data.registrations",
        "data.people_basic", "data.checkins_counts", "data.sermons", "data.streams",
        "data.email_stats", "data.text_in_church",
    },
    ASSISTANT: _EVERYONE | {
        "sunday.read", "events.read", "requests.manage", "streaming.read",
        "streaming.write", "guests.read", "guests.write", "chatlogs.read", "kb.read",
        "integrations.read", "data.services", "data.groups", "data.registrations",
        "data.people_basic", "data.checkins_counts", "data.sermons", "data.streams",
        "data.email_stats", "data.text_in_church",
    },
    FAMILY: _EVERYONE | {
        "events.read", "streaming.read", "data.groups", "data.registrations",
        "data.people_basic", "data.checkins_counts", "data.streams",
    },
    MUSIC: _EVERYONE | {
        "events.read", "data.services", "data.people_basic", "data.sermons",
    },
    PASTORAL: _EVERYONE | {
        "sunday.read", "sunday.approve", "events.read", "streaming.read",
        "guests.read", "chatlogs.read", "instructions.read", "data.services",
        "data.groups", "data.registrations", "data.people_basic",
        "data.checkins_counts", "data.sermons", "data.streams", "data.email_stats",
        "data.text_in_church",
    },
}

# The person who can change roles can never lose the ability to do so by editing
# the matrix: these are always on for Admin regardless of overrides.
_ADMIN_LOCKED = {"team.manage", "audit.read"}


def is_known_role(role: str) -> bool:
    return role in ROLES


def is_known_permission(permission: str) -> bool:
    return permission in PERMISSIONS


def matrix() -> dict:
    """The effective matrix: {role: set(permission)}, defaults plus overrides."""
    from models import RolePermission

    result = {role: set(perms) for role, perms in DEFAULTS.items()}
    for row in RolePermission.query.all():
        if row.role not in result or row.permission not in PERMISSIONS:
            continue
        (result[row.role].add if row.allowed else result[row.role].discard)(row.permission)
    result[ADMIN] |= _ADMIN_LOCKED
    return result


def effective_permissions(user) -> set:
    """Everything this person may do. Empty for someone with no role."""
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return set()
    key = (user.id, frozenset(user.roles))
    cached = g.get("_perm_cache") if _has_context() else None
    if cached is not None and cached[0] == key:
        return cached[1]
    by_role = matrix()
    perms = set()
    for role in user.roles:
        perms |= by_role.get(role, set())
    if _has_context():
        g._perm_cache = (key, perms)
    return perms


def _has_context() -> bool:
    from flask import has_app_context
    return has_app_context()


def can(permission: str, user=None) -> bool:
    """Whether *user* (default: the signed-in person) holds *permission*."""
    if permission not in PERMISSIONS:
        raise KeyError(f"Unknown permission {permission!r}")
    return permission in effective_permissions(user if user is not None else current_user)


def require(*permissions: str):
    """Route decorator: signed in, and holding every listed permission.

    An unauthenticated caller gets the same response ``login_required`` gave
    (401 for the API, a redirect for pages). A signed-in caller without the
    permission gets 403 and an audit-log entry.
    """
    for p in permissions:
        if p not in PERMISSIONS:
            raise KeyError(f"Unknown permission {p!r}")

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required."}), 401
                return redirect(url_for("auth.login_page"))
            held = effective_permissions(current_user)
            missing = [p for p in permissions if p not in held]
            if missing:
                from audit import log_event
                log_event("access.denied", source=request.path,
                          detail={"needs": missing, "method": request.method})
                if request.path.startswith("/api/") or request.method != "GET":
                    return jsonify({"error": "You do not have permission to do that."}), 403
                from flask import render_template
                return render_template("forbidden.html"), 403
            return fn(*args, **kwargs)
        wrapper.required_permissions = permissions
        return wrapper
    return decorator


def require_any(*permissions: str):
    """Like ``require`` but any one of the listed permissions is enough."""
    for p in permissions:
        if p not in PERMISSIONS:
            raise KeyError(f"Unknown permission {p!r}")

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required."}), 401
                return redirect(url_for("auth.login_page"))
            if not set(permissions) & effective_permissions(current_user):
                from audit import log_event
                log_event("access.denied", source=request.path,
                          detail={"needs_any": list(permissions), "method": request.method})
                if request.path.startswith("/api/") or request.method != "GET":
                    return jsonify({"error": "You do not have permission to do that."}), 403
                from flask import render_template
                return render_template("forbidden.html"), 403
            return fn(*args, **kwargs)
        wrapper.required_permissions = permissions
        return wrapper
    return decorator


def people_with(permission: str) -> list:
    """Active people whose roles grant *permission*, for notifications."""
    from models import User, UserRole
    roles = [r for r, perms in matrix().items() if permission in perms]
    if not roles:
        return []
    return (User.query.join(UserRole, UserRole.user_id == User.id)
            .filter(UserRole.role.in_(roles), User.active.is_(True))
            .order_by(User.email).distinct().all())


def permission_table() -> list:
    """For the admin screen: [{key, group, label}] in display order."""
    return [{"key": k, "group": v[0], "label": v[1]} for k, v in PERMISSIONS.items()]
