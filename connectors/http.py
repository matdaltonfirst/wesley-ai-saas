"""An HTTP client for connectors: timeouts, rate limiting, retries and backoff.

* 429 and 5xx are retried with exponential backoff and jitter, honoring
  ``Retry-After`` (capped, so a hostile header cannot stall a worker for an hour).
* A minimum gap between calls keeps one connector inside its provider's limits.
* 401 is passed to ``on_unauthorized`` once (to refresh a token) and retried; a
  second 401 raises ``AuthError``.
* ``request_fn`` and ``sleep`` are injectable so tests run instantly and offline.
"""

import logging
import random
import time

import requests

from .errors import AuthError, RateLimited, UpstreamError

log = logging.getLogger("wesley")

MAX_RETRY_AFTER = 60.0


class HttpClient:
    def __init__(self, name, *, min_interval=0.0, max_attempts=5, base_delay=1.0,
                 timeout=30, request_fn=None, sleep=None, on_unauthorized=None):
        self.name = name
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.timeout = timeout
        self._request = request_fn or requests.request
        self._sleep = sleep or time.sleep
        self._on_unauthorized = on_unauthorized
        self._last_call = None
        self.calls = 0

    def _pace(self):
        if self._last_call is not None:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
        self._last_call = time.monotonic()

    def _backoff(self, attempt, retry_after=None):
        if retry_after is not None:
            return min(float(retry_after), MAX_RETRY_AFTER)
        return min(self.base_delay * (2 ** attempt), MAX_RETRY_AFTER) * (0.5 + random.random() / 2)

    def request(self, method, url, *, headers=None, params=None, data=None, json=None,
                auth=None, expect_json=True):
        refreshed = False
        last = None
        for attempt in range(self.max_attempts):
            self._pace()
            self.calls += 1
            try:
                resp = self._request(method, url, headers=headers, params=params, data=data,
                                     json=json, auth=auth, timeout=self.timeout)
            except requests.RequestException as exc:
                last = UpstreamError(f"Could not reach {self.name} ({type(exc).__name__}).")
                self._sleep(self._backoff(attempt))
                continue

            status = resp.status_code
            if status == 401:
                if self._on_unauthorized and not refreshed:
                    refreshed = True
                    headers = self._on_unauthorized(headers or {})
                    continue
                raise AuthError(f"{self.name} did not accept our sign-in. It needs to be reconnected.")
            if status == 403 and "token" in (resp.text or "").lower()[:300]:
                raise AuthError(f"{self.name} refused access. It may need to be reconnected or granted more permissions.")
            if status == 429:
                last = RateLimited(f"{self.name} is limiting how fast we ask. We will try again later.")
                self._sleep(self._backoff(attempt, resp.headers.get("Retry-After")))
                continue
            if status >= 500:
                last = UpstreamError(f"{self.name} had a problem on its side (error {status}).")
                self._sleep(self._backoff(attempt))
                continue
            if status >= 400:
                detail = (resp.text or "")[:200].replace("\n", " ")
                raise UpstreamError(f"{self.name} rejected a request (error {status}): {detail}")
            if not expect_json:
                return resp
            try:
                return resp.json()
            except ValueError:
                raise UpstreamError(f"{self.name} returned something we could not read.")
        raise last or UpstreamError(f"{self.name} did not answer.")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)
