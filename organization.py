"""The one organization this app serves.

Everything that used to be looked up per church (name, branding, timezone,
local practice, feature flags) now comes from the single ``Organization`` row.
Call ``get_org()``; never query the table directly.
"""

import json

from flask import g, has_app_context

from config import ORG_NAME
from models import Organization, db


def get_org() -> Organization:
    """The organization row, created with defaults on a brand-new database.

    Cached on ``flask.g`` for the request. Outside a request it re-reads, so a
    scheduled job never holds a stale or detached row.
    """
    if has_app_context() and "org" in g:
        return g.org
    org = Organization.query.order_by(Organization.id).first()
    if org is None:
        org = Organization(name=ORG_NAME)
        db.session.add(org)
        db.session.commit()
    if has_app_context():
        g.org = org
    return org


def features(org=None) -> dict:
    raw = (org or get_org()).features
    try:
        data = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        data = {}
    return data if isinstance(data, dict) else {}


def feature_enabled(name: str, default: bool = True) -> bool:
    """Whether a feature flag is on. Unset flags use *default*."""
    return bool(features().get(name, default))


def public_widget_ids(org=None) -> set:
    """The ids the public endpoints accept as ``church_id``.

    The live website embed carries a legacy id. It is accepted for as long as the
    embed does; any other value is treated as an unknown church.
    """
    org = org or get_org()
    return {i for i in (org.id, org.legacy_widget_id) if i is not None}
