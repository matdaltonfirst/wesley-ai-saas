"""Connector registry. Add a connector here and it appears everywhere.

The Integrations page, the scheduler, the webhook route and the health checks
all read this list; nothing else needs to know a connector exists.
"""

import importlib

# (key, module, class). Imported lazily so a broken connector cannot stop the app.
_CONNECTORS = [
    ("planning_center", "connectors.planning_center", "PlanningCenterConnector"),
    ("youtube", "connectors.youtube", "YouTubeConnector"),
    ("facebook", "connectors.meta", "MetaConnector"),
    ("constant_contact", "connectors.constant_contact", "ConstantContactConnector"),
    ("text_in_church", "connectors.text_in_church", "TextInChurchConnector"),
    ("subsplash", "connectors.subsplash", "SubsplashConnector"),
]

_cache = {}


def keys() -> list:
    return [k for k, _, _ in _CONNECTORS]


def get(key: str):
    if key not in _cache:
        for k, module, cls in _CONNECTORS:
            if k == key:
                _cache[key] = getattr(importlib.import_module(module), cls)()
                break
        else:
            raise KeyError(key)
    return _cache[key]


def all_connectors() -> list:
    return [get(k) for k in keys()]
