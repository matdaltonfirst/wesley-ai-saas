"""Errors a connector can raise, each carrying a message safe to show staff."""


class ConnectorError(Exception):
    """Base. ``kind`` drives health: auth, rate_limit, upstream, config or other."""
    kind = "other"

    def __init__(self, message: str, kind: str = None):
        super().__init__(message)
        if kind:
            self.kind = kind


class AuthError(ConnectorError):
    """The provider refused our credentials: expired, revoked, or never granted."""
    kind = "auth"


class RateLimited(ConnectorError):
    kind = "rate_limit"


class UpstreamError(ConnectorError):
    """The provider is down or returned something we cannot use."""
    kind = "upstream"


class NotConfigured(ConnectorError):
    """Missing keys, access not granted yet, or the feature is off."""
    kind = "config"
