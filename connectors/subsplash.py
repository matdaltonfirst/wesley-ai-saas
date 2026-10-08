"""Subsplash: manual and CSV only, by design.

There is no sync here. This class exists so Subsplash appears on the Integrations
page with an honest explanation instead of a missing row, and so an official
integration, if Subsplash ever offers one, has an obvious home (see
``streaming_sources.SubsplashSource``). The church leaves Subsplash in April 2027.
"""

from streaming_sources import SubsplashSource

from .base import Connector


class SubsplashConnector(Connector):
    key = "subsplash"
    label = "Subsplash"
    description = "Weekly stream numbers. Entered by hand or imported from a CSV."
    docs_anchor = "subsplash"
    interval_minutes = 0

    def configured(self) -> bool:
        return True

    def connected(self) -> bool:
        return False

    def manual_only(self) -> str:
        return SubsplashSource().explain()

    def sync(self, ctx):
        raise NotImplementedError("Subsplash has no API to sync from.")
