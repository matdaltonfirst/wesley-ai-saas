"""Where streaming numbers can come from: one small interface.

The streaming pane does not care whether a figure arrived from a platform's API, a
CSV, or a person typing. A *source* is anything that can say which platform and
service dates it covers and, if it can, return numbers for a date range. Adding
Resi, or a self-hosted OBS multi-destination setup, later means writing one class
here (and, if it has an API, a connector that calls ``persist``); the pane, the
rollups, the edit history and the CSV export do not change.

Shipped sources:

* ``ManualEntrySource`` and ``CsvImportSource``: what Subsplash uses today.
* ``SubsplashSource``: a placeholder for an official integration. As of 7 October 2026
  there is no public Subsplash analytics or reporting API that we could find; the only
  SDK is unofficial and covers media series and items. The church is leaving Subsplash
  in April 2027, so no Subsplash-specific logic is built.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

import streaming


@dataclass
class SourceRow:
    service_date: date
    platform: str
    service_label: str = streaming.DEFAULT_LABEL
    peak_concurrent: Optional[int] = None
    total_views: Optional[int] = None
    watch_minutes: Optional[int] = None
    external_id: Optional[str] = None


class NotAvailable(Exception):
    """The source cannot return numbers by itself; the message says what to do instead."""


class StreamingSource:
    key = ""
    label = ""
    platform = "other"
    kind = "manual"          # manual | csv | api

    def available(self) -> bool:
        return self.kind != "api"

    def explain(self) -> str:
        return ""

    def fetch(self, start: date, end: date) -> list:
        raise NotAvailable(self.explain())


class ManualEntrySource(StreamingSource):
    key, label, kind = "manual", "Entered by hand", "manual"

    def explain(self):
        return "Use Enter numbers on the Streaming numbers page."


class CsvImportSource(StreamingSource):
    key, label, kind = "csv", "CSV import", "csv"

    def explain(self):
        return "Use Import CSV on the Streaming numbers page."


class SubsplashSource(StreamingSource):
    key, label, platform, kind = "subsplash", "Subsplash", "subsplash", "api"

    def available(self) -> bool:
        return False

    def explain(self):
        return ("Subsplash does not offer a public analytics or reporting API that we could find, so "
                "weekly numbers are entered by hand or imported from a CSV (copy them from the Subsplash "
                "dashboard). If Subsplash provides an official integration, it plugs in here.")


def persist(rows, source: str = "api", by: str = None) -> dict:
    """Save rows from any adapter through the same rules as everything else."""
    result = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    for r in rows:
        values = {k: getattr(r, k) for k in streaming.FIELDS if getattr(r, k) is not None}
        if not values:
            continue
        _, outcome = streaming.save_number(r.service_date, r.service_label, streaming.parse_platform(r.platform), values,
                                           source, by=by or source, external_id=r.external_id)
        result[outcome] += 1
    return result


SOURCES = [ManualEntrySource(), CsvImportSource(), SubsplashSource()]
