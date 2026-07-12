"""Tests for the scheduled re-crawl logic in crawler.py."""

from datetime import datetime, timedelta
from unittest.mock import patch

from models import db, Church
from crawler import run_due_recrawls


def _make_church(name, website_url=None, last_crawled_at=None):
    c = Church(
        name=name,
        website_url=website_url,
        last_crawled_at=last_crawled_at,
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
        billing_exempt=True,
    )
    db.session.add(c)
    db.session.commit()
    return c


def _cleanup(*churches):
    for c in churches:
        db.session.delete(c)
    db.session.commit()


class TestRunDueRecrawls:
    def test_recrawls_only_stale_churches_with_a_website(self, app):
        stale = _make_church(
            "Stale Church", "https://stale.example.org",
            last_crawled_at=datetime.utcnow() - timedelta(days=8),
        )
        fresh = _make_church(
            "Fresh Church", "https://fresh.example.org",
            last_crawled_at=datetime.utcnow() - timedelta(days=1),
        )
        never = _make_church("Never Crawled", "https://new.example.org")
        no_site = _make_church("No Website")

        with patch("crawler.crawl_church_website",
                   return_value={"pages_crawled": 3, "pages_failed": 0,
                                 "error": None, "method": "requests"}) as mock_crawl:
            crawled = run_due_recrawls()

        assert set(crawled) == {stale.id, never.id}
        called_ids = {call.args[0] for call in mock_crawl.call_args_list}
        assert called_ids == {stale.id, never.id}
        _cleanup(stale, fresh, never, no_site)

    def test_claims_church_before_crawling(self, app):
        """last_crawled_at is bumped before the crawl so a second scheduler
        pass (or a crash mid-crawl) cannot pick the same church again."""
        stale = _make_church(
            "Claim Church", "https://claim.example.org",
            last_crawled_at=datetime.utcnow() - timedelta(days=30),
        )

        def crawl_and_recheck(church_id, url):
            # While the crawl is "running", the church must no longer be due.
            assert run_due_recrawls() == []
            return {"pages_crawled": 1, "pages_failed": 0,
                    "error": None, "method": "requests"}

        with patch("crawler.crawl_church_website", side_effect=crawl_and_recheck):
            crawled = run_due_recrawls()

        assert crawled == [stale.id]
        _cleanup(stale)

    def test_disabled_by_config(self, app):
        stale = _make_church(
            "Disabled Church", "https://disabled.example.org",
            last_crawled_at=datetime.utcnow() - timedelta(days=30),
        )
        with patch("config.AUTO_RECRAWL_DAYS", 0), \
             patch("crawler.crawl_church_website") as mock_crawl:
            assert run_due_recrawls() == []
        mock_crawl.assert_not_called()
        _cleanup(stale)
