"""The weekly streaming numbers: storage priority, honest rollups, CSV, routes."""

from datetime import date, timedelta

import pytest

import streaming as S
from models import AuditLog, StreamingNumber, StreamingNumberEdit, db
from tests.conftest import login, make_user

SUN = date(2026, 10, 4)      # a Sunday


def save(d=SUN, label="Sunday service", platform="youtube", source="manual", **values):
    row, outcome = S.save_number(d, label, platform, values, source, by="t@x")
    db.session.commit()
    return row, outcome


# ── Parsing ───────────────────────────────────────────────────────────────────

class TestParsing:
    @pytest.mark.parametrize("raw,expected", [("1,234", 1234), ("1234.0", 1234), (" 56 ", 56), ("", None),
                                              ("N/A", None), ("-", None), (None, None), (0, 0)])
    def test_counts(self, raw, expected):
        assert S.parse_count(raw, "total_views") == expected

    @pytest.mark.parametrize("raw", ["-5", "12.5", "abc", "1e3x", "999999999999"])
    def test_bad_counts_are_refused_in_plain_words(self, raw):
        with pytest.raises(S.StreamingError):
            S.parse_count(raw, "total_views")

    @pytest.mark.parametrize("raw", ["2026-10-04", "10/4/2026", "10/04/26", "October 4, 2026", "Oct 4, 2026"])
    def test_dates(self, raw):
        assert S.parse_date(raw) == SUN

    def test_a_bad_date_says_what_to_use(self):
        with pytest.raises(S.StreamingError, match="2026-10-04"):
            S.parse_date("next sunday")

    @pytest.mark.parametrize("raw,expected", [("YouTube", "youtube"), ("fb", "facebook"), ("Facebook Live", "facebook"),
                                              ("Subsplash", "subsplash"), ("Resi", "other")])
    def test_platforms(self, raw, expected):
        assert S.parse_platform(raw) == expected


# ── Storage rules ─────────────────────────────────────────────────────────────

class TestStorage:
    def test_a_new_number_is_created_with_its_source(self, church):
        row, outcome = save(total_views=500, peak_concurrent=80)
        assert outcome == "created" and row.total_views == 500
        assert S.row_dict(row)["sources"] == {"peak_concurrent": "manual", "total_views": "manual"}

    def test_saving_the_same_values_changes_nothing_and_writes_no_history(self, church):
        save(total_views=500)
        _, outcome = save(total_views=500)
        assert outcome == "unchanged"
        assert StreamingNumberEdit.query.count() == 1          # only the first write

    def test_every_change_is_in_the_history_with_before_and_after(self, church):
        row, _ = save(total_views=500)
        S.save_number(SUN, "Sunday service", "youtube", {"total_views": 650}, "manual", by="c@x", reason="Found the real total")
        db.session.commit()
        edits = S.history(row.id)
        assert edits[0]["old"] == "500" and edits[0]["new"] == "650" and edits[0]["reason"] == "Found the real total"
        assert edits[0]["by"] == "c@x"

    def test_api_cannot_overwrite_a_manual_correction(self, church):
        save(total_views=500)
        row, outcome = save(source="api", total_views=999)
        assert outcome == "skipped" and row.total_views == 500

    def test_api_cannot_overwrite_csv_but_csv_can_overwrite_api(self, church):
        save(source="api", total_views=100)
        _, o = save(source="csv", total_views=120)
        assert o == "updated"
        row, o = save(source="api", total_views=130)
        assert o == "skipped" and row.total_views == 120

    def test_csv_cannot_overwrite_manual(self, church):
        save(total_views=500)
        row, o = save(source="csv", total_views=1)
        assert o == "skipped" and row.total_views == 500

    def test_a_correction_to_one_field_leaves_other_api_fields_updating(self, church):
        save(source="api", total_views=100, peak_concurrent=40)
        save(source="manual", total_views=150)
        row, _ = save(source="api", total_views=200, peak_concurrent=55)
        assert row.total_views == 150 and row.peak_concurrent == 55

    def test_clearing_a_number_is_a_recorded_change(self, church):
        row, _ = save(total_views=500)
        row, o = save(total_views=None)
        assert o == "updated" and row.total_views is None
        assert S.history(row.id)[0]["new"] is None

    def test_labels_are_trimmed_and_defaulted(self, church):
        row, _ = save(label="  Modern   9:30  ", total_views=1)
        assert row.service_label == "Modern 9:30"
        assert save(label="", platform="facebook", total_views=1)[0].service_label == "Sunday service"


# ── Rollups ───────────────────────────────────────────────────────────────────

class TestWeeklySummary:
    def test_totals_add_views_across_platforms(self, church):
        save(platform="youtube", total_views=300); save(platform="facebook", total_views=120)
        w = S.weekly_summary(4, today=SUN)["weeks"][-1]
        assert w["total_views"] == 420 and w["platforms_with_views"] == 2

    def test_peaks_are_never_added(self, church):
        save(platform="youtube", peak_concurrent=90, total_views=1); save(platform="facebook", peak_concurrent=60, total_views=1)
        w = S.weekly_summary(4, today=SUN)["weeks"][-1]
        assert w["highest_peak"] == 90                      # not 150

    def test_two_services_the_same_week_sum_views_but_take_the_highest_peak(self, church):
        save(label="Modern", total_views=100, peak_concurrent=40)
        save(label="Traditional", total_views=200, peak_concurrent=70)
        y = S.weekly_summary(4, today=SUN)["weeks"][-1]["platforms"]["youtube"]
        assert y["total_views"] == 300 and y["peak_concurrent"] == 70 and y["services"] == 2

    def test_week_over_week_change(self, church):
        save(d=SUN - timedelta(days=7), total_views=400); save(d=SUN, total_views=500)
        w = S.weekly_summary(4, today=SUN)["weeks"][-1]
        assert w["views_change"] == 100 and w["views_change_pct"] == 25.0 and w["comparable"]

    def test_a_change_is_flagged_when_the_platforms_differ(self, church):
        save(d=SUN - timedelta(days=7), platform="youtube", total_views=400)
        save(d=SUN, platform="youtube", total_views=300); save(d=SUN, platform="facebook", total_views=200)
        w = S.weekly_summary(4, today=SUN)["weeks"][-1]
        assert w["total_views"] == 500 and w["comparable"] is False

    def test_no_change_is_invented_when_a_week_is_empty(self, church):
        save(d=SUN, total_views=500)
        w = S.weekly_summary(4, today=SUN)["weeks"][-1]
        assert w["views_change"] is None and w["views_change_pct"] is None

    def test_a_percentage_from_zero_is_not_computed(self, church):
        save(d=SUN - timedelta(days=7), total_views=0); save(d=SUN, total_views=50)
        assert S.weekly_summary(4, today=SUN)["weeks"][-1]["views_change_pct"] is None

    def test_a_week_with_only_blank_numbers_has_no_total(self, church):
        save(peak_concurrent=50)
        assert S.weekly_summary(4, today=SUN)["weeks"][-1]["total_views"] is None

    def test_the_summary_carries_the_honesty_notes(self, church):
        notes = " ".join(S.weekly_summary(4, today=SUN)["notes"])
        assert "not unique people" in notes and "never added together" in notes

    def test_a_sunday_belongs_to_the_week_that_ends_with_it(self):
        assert S.week_start(SUN) == date(2026, 9, 28) and S.week_start(date(2026, 9, 28)) == date(2026, 9, 28)

    def test_the_number_of_weeks_is_bounded(self, church):
        assert len(S.weekly_summary(10_000, today=SUN)["weeks"]) == 104


# ── CSV ───────────────────────────────────────────────────────────────────────

class TestCsv:
    def test_import_creates_rows_and_reports_counts(self, church):
        text = "date,platform,peak_concurrent,total_views\n2026-10-04,YouTube,\"1,200\",3400\n10/4/2026,Facebook,,800\n"
        r = S.import_csv(text, "c@x")
        assert (r["created"], r["updated"], r["errors"]) == (2, 0, [])
        assert StreamingNumber.query.filter_by(platform="youtube").one().peak_concurrent == 1200

    def test_preview_saves_nothing(self, church):
        r = S.import_csv("date,platform,total_views\n2026-10-04,youtube,10\n", "c@x", commit=False)
        assert r["created"] == 1 and r["committed"] is False and StreamingNumber.query.count() == 0

    def test_import_twice_changes_nothing_the_second_time(self, church):
        text = "date,platform,total_views\n2026-10-04,youtube,10\n"
        S.import_csv(text, "c@x")
        r = S.import_csv(text, "c@x")
        assert (r["created"], r["updated"], r["unchanged"]) == (0, 0, 1)

    def test_bad_rows_are_reported_by_line_and_good_rows_still_load(self, church):
        text = "date,platform,total_views\n2026-10-04,youtube,10\nnonsense,youtube,5\n2026-10-11,youtube,-3\n2026-10-18,youtube,7\n"
        r = S.import_csv(text, "c@x")
        assert r["created"] == 2 and [e["line"] for e in r["errors"]] == [3, 4]

    def test_a_row_repeated_in_the_file_is_an_error(self, church):
        r = S.import_csv("date,platform,total_views\n2026-10-04,youtube,1\n2026-10-04,youtube,2\n", "c@x")
        assert r["created"] == 1 and "twice" in r["errors"][0]["error"]

    def test_manual_numbers_survive_an_import(self, church):
        save(total_views=500)
        r = S.import_csv("date,platform,total_views\n2026-10-04,youtube,1\n", "c@x")
        assert r["skipped"] == 1 and StreamingNumber.query.one().total_views == 500

    def test_missing_columns_explain_themselves(self, church):
        with pytest.raises(S.StreamingError, match="date column"):
            S.import_csv("platform,total_views\nyoutube,5\n", "c@x")
        with pytest.raises(S.StreamingError, match="platform"):
            S.import_csv("date,total_views\n2026-10-04,5\n", "c@x")
        with pytest.raises(S.StreamingError, match="at least one"):
            S.import_csv("date,platform\n2026-10-04,youtube\n", "c@x")

    def test_a_default_platform_covers_a_file_without_the_column(self, church):
        r = S.import_csv("Date,Views\n2026-10-04,55\n", "c@x", default_platform="subsplash")
        assert r["created"] == 1 and StreamingNumber.query.one().platform == "subsplash"

    def test_a_byte_order_mark_and_odd_headers_are_tolerated(self, church):
        r = S.import_csv("﻿Service Date, Channel ,Total Views\n2026-10-04,YT,9\n", "c@x")
        assert r["created"] == 1

    def test_export_round_trips_and_names_sources(self, church):
        save(total_views=500, peak_concurrent=80)
        text = S.export_csv(SUN - timedelta(days=7), SUN)
        assert "views_source" in text.splitlines()[0] and "manual" in text
        StreamingNumber.query.delete(); db.session.commit()
        S.import_csv(text, "c@x")
        assert StreamingNumber.query.one().total_views == 500

    def test_spreadsheet_formulas_cannot_ride_in_through_the_export(self, church):
        save(label="=HYPERLINK(\"http://evil\")", total_views=1)
        assert "'=HYPERLINK" in S.export_csv(SUN - timedelta(days=7), SUN)


# ── Routes and permissions ────────────────────────────────────────────────────

class TestRoutes:
    def test_assistant_can_read_and_write(self, client, church):
        login(client, make_user("aa@daltonfumc.com", ["admin_assistant"]))
        r = client.post("/api/streaming/numbers", json={"service_date": "2026-10-04", "platform": "youtube",
                                                        "total_views": "1,500", "peak_concurrent": "210"})
        assert r.status_code == 201 and r.get_json()["number"]["total_views"] == 1500
        assert client.get("/streaming").status_code == 200
        assert len(client.get("/api/streaming/numbers?start=2026-09-01&end=2026-10-30").get_json()["numbers"]) == 1

    def test_family_can_read_but_not_write(self, client, church):
        login(client, make_user("f@daltonfumc.com", ["family"]))
        assert client.get("/api/streaming/summary").status_code == 200
        assert client.post("/api/streaming/numbers", json={"service_date": "2026-10-04", "platform": "youtube", "total_views": 1}).status_code == 403
        assert client.post("/api/streaming/import", json={"csv": "x"}).status_code == 403

    def test_music_cannot_see_it_at_all(self, client, church):
        login(client, make_user("m@daltonfumc.com", ["music"]))
        assert client.get("/streaming").status_code == 403 and client.get("/api/streaming/summary").status_code == 403

    def test_changing_an_existing_number_needs_a_reason(self, auth_client):
        body = {"service_date": "2026-10-04", "platform": "youtube", "total_views": 1}
        assert auth_client.post("/api/streaming/numbers", json=body).status_code == 201
        res = auth_client.post("/api/streaming/numbers", json={**body, "total_views": 2})
        assert res.status_code == 400 and "why" in res.get_json()["error"]
        assert auth_client.post("/api/streaming/numbers", json={**body, "total_views": 2, "reason": "typo"}).status_code == 200

    @pytest.mark.parametrize("body,fragment", [
        ({"service_date": "nope", "platform": "youtube", "total_views": 1}, "date"),
        ({"service_date": "2026-10-04", "platform": "", "total_views": 1}, "Platform"),
        ({"service_date": "2026-10-04", "platform": "youtube"}, "at least one"),
        ({"service_date": "2026-10-04", "platform": "youtube", "total_views": "-4"}, "negative"),
        ({"service_date": "2999-01-01", "platform": "youtube", "total_views": 1}, "future"),
    ])
    def test_bad_input_is_refused_with_a_reason(self, auth_client, body, fragment):
        res = auth_client.post("/api/streaming/numbers", json=body)
        assert res.status_code == 400 and fragment in res.get_json()["error"]

    def test_edits_are_audited(self, auth_client):
        auth_client.post("/api/streaming/numbers", json={"service_date": "2026-10-04", "platform": "youtube", "total_views": 1})
        assert AuditLog.query.filter_by(action="streaming.edit").count() == 1

    def test_history_endpoint(self, auth_client):
        n = auth_client.post("/api/streaming/numbers", json={"service_date": "2026-10-04", "platform": "youtube", "total_views": 1}).get_json()["number"]
        assert auth_client.get(f"/api/streaming/numbers/{n['id']}/history").get_json()["history"][0]["new"] == "1"
        assert auth_client.get("/api/streaming/numbers/9999/history").status_code == 404

    def test_csv_download_and_upload_through_the_endpoints(self, auth_client):
        import io
        data = {"file": (io.BytesIO(b"date,platform,total_views\n2026-10-04,youtube,12\n"), "n.csv"), "commit": "false"}
        r = auth_client.post("/api/streaming/import", data=data, content_type="multipart/form-data").get_json()
        assert r["created"] == 1 and StreamingNumber.query.count() == 0
        data = {"file": (io.BytesIO(b"date,platform,total_views\n2026-10-04,youtube,12\n"), "n.csv")}
        assert auth_client.post("/api/streaming/import", data=data, content_type="multipart/form-data").get_json()["created"] == 1
        res = auth_client.get("/api/streaming/export.csv?start=2026-09-01&end=2026-10-30")
        assert res.mimetype == "text/csv" and b"2026-10-04" in res.data and "attachment" in res.headers["Content-Disposition"]

    def test_oversized_and_binary_uploads_are_refused(self, auth_client):
        import io
        big = {"file": (io.BytesIO(b"a" * 2_100_000), "big.csv")}
        assert auth_client.post("/api/streaming/import", data=big, content_type="multipart/form-data").status_code == 400
        binary = {"file": (io.BytesIO(b"\xff\xfe\x00\x01"), "x.csv")}
        assert auth_client.post("/api/streaming/import", data=binary, content_type="multipart/form-data").status_code == 400

    def test_an_absurd_date_range_is_refused(self, auth_client):
        assert auth_client.get("/api/streaming/numbers?start=1990-01-01&end=2026-01-01").status_code == 400
