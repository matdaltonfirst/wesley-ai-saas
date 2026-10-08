"""Weekly streaming numbers: storage rules, weekly rollups, CSV in and out.

Honesty rules, because these figures end up in reports and a wrong sum is worse
than a missing one:

* Everything is **views**, never people. One person watching on two platforms,
  or leaving and rejoining, is counted more than once, so totals are labelled
  "total views" and no figure is ever called viewers or unique.
* **Peak concurrent is never added up.** Peaks happen at different moments on
  different platforms, so the sum is a number that never existed. The weekly
  figure shown is the highest single-platform peak, labelled as such.
* Every field remembers where it came from (api, csv or manual) and every change
  is written to an edit history.
* A person's correction is never silently overwritten: priority is
  manual > csv > api, so a nightly API sync cannot undo a fix.
"""

import csv
import io
import json
import re
from datetime import date, datetime, timedelta

from models import StreamingNumber, StreamingNumberEdit, db

PLATFORMS = {
    "youtube": "YouTube",
    "facebook": "Facebook",
    "subsplash": "Subsplash",
    "other": "Other",
}
FIELDS = ("peak_concurrent", "total_views", "watch_minutes")
FIELD_LABELS = {
    "peak_concurrent": "Peak concurrent viewers",
    "total_views": "Total views",
    "watch_minutes": "Watch time (minutes)",
}
SOURCES = ("api", "csv", "manual")
PRIORITY = {"api": 1, "csv": 2, "manual": 3}
DEFAULT_LABEL = "Sunday service"
MAX_NUMBER = 100_000_000


class StreamingError(ValueError):
    """A problem with what a person entered, worded for them."""


# ── Parsing ──────────────────────────────────────────────────────────────────

_PLATFORM_WORDS = {
    "youtube": "youtube", "yt": "youtube", "you tube": "youtube",
    "facebook": "facebook", "fb": "facebook", "facebook live": "facebook", "meta": "facebook",
    "subsplash": "subsplash", "sub splash": "subsplash",
}


def parse_platform(raw) -> str:
    key = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if key in PLATFORMS:
        return key
    if key in _PLATFORM_WORDS:
        return _PLATFORM_WORDS[key]
    if not key:
        raise StreamingError("Platform is required.")
    return "other"


def parse_date(raw) -> date:
    text = str(raw or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise StreamingError(f"'{text}' is not a date I can read. Use a format like 2026-10-04.")


def parse_count(raw, field):
    """An integer, or None for blank. Accepts 1,234 and 1234.0; rejects negatives and text."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text == "" or text.lower() in ("n/a", "na", "-", "none", "null"):
        return None
    try:
        number = float(text)
    except ValueError:
        raise StreamingError(f"{FIELD_LABELS[field]} must be a whole number, not '{raw}'.")
    if number != int(number):
        raise StreamingError(f"{FIELD_LABELS[field]} must be a whole number, not '{raw}'.")
    number = int(number)
    if number < 0:
        raise StreamingError(f"{FIELD_LABELS[field]} cannot be negative.")
    if number > MAX_NUMBER:
        raise StreamingError(f"{FIELD_LABELS[field]} is unreasonably large ({number:,}).")
    return number


def clean_label(raw) -> str:
    label = re.sub(r"\s+", " ", str(raw or "").strip())
    return label[:60] or DEFAULT_LABEL


# ── Storage with history ─────────────────────────────────────────────────────

def _sources(row) -> dict:
    try:
        return json.loads(row.sources) if row.sources else {}
    except ValueError:
        return {}


def save_number(service_date, service_label, platform, values: dict, source: str, by: str = None,
                reason: str = None, external_id: str = None, note=None, _fields=None):
    """Create or update one row, field by field, writing history for each change.

    *values* maps field name to an int or None. A field absent from *values* is
    left alone; a field present with None clears it. Returns (row, outcome) where
    outcome is "created", "updated", "unchanged" or "skipped" (every change was
    blocked by a higher-priority source).
    """
    if source not in SOURCES:
        raise StreamingError("Unknown source.")
    label = clean_label(service_label)
    row = StreamingNumber.query.filter_by(
        service_date=service_date, service_label=label, platform=platform).first()
    created = row is None
    if created:
        row = StreamingNumber(service_date=service_date, service_label=label, platform=platform)
        db.session.add(row)
        db.session.flush()
    srcs = _sources(row)

    changed, blocked = [], []
    for field in FIELDS:
        if field not in values:
            continue
        new = values[field]
        old = getattr(row, field)
        if new == old:
            if new is not None and PRIORITY[source] > PRIORITY.get(srcs.get(field, "api"), 0):
                srcs[field] = source        # same value, now confirmed by a stronger source
            continue
        current_source = srcs.get(field)
        if current_source and old is not None and PRIORITY[source] < PRIORITY[current_source]:
            blocked.append(field)
            continue
        setattr(row, field, new)
        srcs[field] = source if new is not None else None
        if source == "api" and current_source == "api" and old is not None:
            changed.append(field)       # views keep rising on their own; that is not an edit
            continue
        db.session.add(StreamingNumberEdit(
            number_id=row.id, by=by or source, field=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
            source=source, reason=(reason or "")[:300] or None))
        changed.append(field)

    if external_id and row.external_id != external_id and source == "api":
        row.external_id = external_id
    if note is not None:
        row.note = (note or "")[:300] or None
    row.sources = json.dumps({k: v for k, v in srcs.items() if v})
    if changed or created:
        row.updated_by = by or source
    db.session.flush()

    if created:
        outcome = "created"
    elif changed:
        outcome = "updated"
    elif blocked:
        outcome = "skipped"
    else:
        outcome = "unchanged"
    return row, outcome


def row_dict(row) -> dict:
    srcs = _sources(row)
    return {
        "id": row.id, "service_date": row.service_date.isoformat(), "service_label": row.service_label,
        "platform": row.platform, "platform_label": PLATFORMS.get(row.platform, row.platform),
        "peak_concurrent": row.peak_concurrent, "total_views": row.total_views,
        "watch_minutes": row.watch_minutes,
        "sources": {f: srcs.get(f) for f in FIELDS if getattr(row, f) is not None},
        "external_id": row.external_id or "", "note": row.note or "",
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "updated_by": row.updated_by or "",
    }


def history(number_id: int, limit: int = 50) -> list:
    rows = (StreamingNumberEdit.query.filter_by(number_id=number_id)
            .order_by(StreamingNumberEdit.id.desc()).limit(limit).all())
    return [{"at": r.at.isoformat() + "Z", "by": r.by or "", "field": r.field,
             "field_label": FIELD_LABELS.get(r.field, r.field), "old": r.old_value, "new": r.new_value,
             "source": r.source, "reason": r.reason or ""} for r in rows]


# ── Weekly rollups ───────────────────────────────────────────────────────────

def week_start(d: date) -> date:
    """Monday of the week containing *d*. A service on Sunday closes its week."""
    return d - timedelta(days=d.weekday())


def _pct(new, old):
    if new is None or old in (None, 0):
        return None
    return round((new - old) / old * 100, 1)


def weekly_summary(weeks: int = 12, today: date = None) -> dict:
    """Per week and platform totals, week-over-week change, and a trend series."""
    today = today or date.today()
    weeks = max(1, min(weeks, 104))
    first = week_start(today) - timedelta(weeks=weeks - 1)
    rows = (StreamingNumber.query.filter(StreamingNumber.service_date >= first - timedelta(weeks=1))
            .order_by(StreamingNumber.service_date).all())

    def blank():
        return {"total_views": None, "peak_concurrent": None, "watch_minutes": None, "services": 0}

    by_week = {}
    for r in rows:
        w = week_start(r.service_date)
        plat = by_week.setdefault(w, {}).setdefault(r.platform, blank())
        plat["services"] += 1
        if r.total_views is not None:
            plat["total_views"] = (plat["total_views"] or 0) + r.total_views
        if r.watch_minutes is not None:
            plat["watch_minutes"] = (plat["watch_minutes"] or 0) + r.watch_minutes
        if r.peak_concurrent is not None:
            # Highest single service peak on that platform: peaks are never summed.
            plat["peak_concurrent"] = max(plat["peak_concurrent"] or 0, r.peak_concurrent)

    def week_entry(w):
        plats = by_week.get(w, {})
        views = [p["total_views"] for p in plats.values() if p["total_views"] is not None]
        peaks = [p["peak_concurrent"] for p in plats.values() if p["peak_concurrent"] is not None]
        minutes = [p["watch_minutes"] for p in plats.values() if p["watch_minutes"] is not None]
        return {
            "week_start": w.isoformat(), "week_end": (w + timedelta(days=6)).isoformat(),
            "platforms": plats,
            "total_views": sum(views) if views else None,
            "platforms_with_views": len(views),
            "highest_peak": max(peaks) if peaks else None,
            "watch_minutes": sum(minutes) if minutes else None,
        }

    series = []
    for i in range(weeks):
        w = first + timedelta(weeks=i)
        entry = week_entry(w)
        prev = week_entry(w - timedelta(weeks=1))
        entry["views_change"] = (None if entry["total_views"] is None or prev["total_views"] is None
                                 else entry["total_views"] - prev["total_views"])
        entry["views_change_pct"] = _pct(entry["total_views"], prev["total_views"])
        # A change is only fair when both weeks counted the same platforms.
        entry["comparable"] = (entry["platforms_with_views"] == prev["platforms_with_views"]
                               and entry["platforms_with_views"] > 0)
        series.append(entry)
    return {
        "weeks": series,
        "platforms": PLATFORMS,
        "notes": [
            "Totals are total views, added across platforms. They are not unique people: someone who "
            "watched on two platforms, or came back to a replay, is counted each time.",
            "Peak concurrent viewers are never added together. The weekly figure is the highest "
            "single-platform peak.",
        ],
    }


def numbers_between(start: date, end: date) -> list:
    rows = (StreamingNumber.query
            .filter(StreamingNumber.service_date >= start, StreamingNumber.service_date <= end)
            .order_by(StreamingNumber.service_date.desc(), StreamingNumber.service_label,
                      StreamingNumber.platform).all())
    return [row_dict(r) for r in rows]


# ── CSV ──────────────────────────────────────────────────────────────────────

EXPORT_HEADER = ["service_date", "service_label", "platform", "peak_concurrent", "total_views",
                 "watch_minutes", "peak_source", "views_source", "watch_source", "note",
                 "last_updated_utc", "last_updated_by"]


def _safe(value) -> str:
    """Stop a spreadsheet from running a cell as a formula (CSV injection)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(start: date, end: date) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_HEADER)
    for r in numbers_between(start, end):
        s = r["sources"]
        writer.writerow([r["service_date"], _safe(r["service_label"]), r["platform"],
                         r["peak_concurrent"] if r["peak_concurrent"] is not None else "",
                         r["total_views"] if r["total_views"] is not None else "",
                         r["watch_minutes"] if r["watch_minutes"] is not None else "",
                         s.get("peak_concurrent", ""), s.get("total_views", ""), s.get("watch_minutes", ""),
                         _safe(r["note"]), r["updated_at"] or "", _safe(r["updated_by"])])
    return out.getvalue()


_ALIASES = {
    "service_date": ("service_date", "date", "service date", "week", "sunday"),
    "service_label": ("service_label", "service", "service name", "label"),
    "platform": ("platform", "source", "channel"),
    "peak_concurrent": ("peak_concurrent", "peak", "peak viewers", "peak concurrent", "concurrent"),
    "total_views": ("total_views", "views", "total views", "plays"),
    "watch_minutes": ("watch_minutes", "watch time", "minutes watched", "watch minutes", "minutes"),
    "note": ("note", "notes"),
}
MAX_IMPORT_ROWS = 2000


def _header_map(fieldnames):
    lowered = {re.sub(r"\s+", " ", (f or "").strip().lower()): f for f in fieldnames or []}
    mapping = {}
    for canon, names in _ALIASES.items():
        for n in names:
            if n in lowered:
                mapping[canon] = lowered[n]
                break
    return mapping


def import_csv(text: str, by: str, commit: bool = True, default_platform: str = None) -> dict:
    """Load numbers from CSV text.

    With commit=False nothing is saved: it reports what would happen, so a person
    can check before anything changes. Errors are per row and never stop the others.
    """
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    mapping = _header_map(reader.fieldnames)
    if "service_date" not in mapping:
        raise StreamingError("The file needs a date column (service_date or date).")
    if "platform" not in mapping and not default_platform:
        raise StreamingError("The file needs a platform column, or choose a platform for the whole file.")
    if not any(f in mapping for f in FIELDS):
        raise StreamingError("The file needs at least one of: peak_concurrent, total_views, watch_minutes.")

    result = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": [], "rows": 0,
              "committed": commit}
    seen = set()
    for index, record in enumerate(reader, start=2):        # line 1 is the header
        if all(not (v or "").strip() for v in record.values() if isinstance(v, str)):
            continue
        result["rows"] += 1
        if result["rows"] > MAX_IMPORT_ROWS:
            result["errors"].append({"line": index, "error": f"Too many rows (limit {MAX_IMPORT_ROWS})."})
            break
        try:
            d = parse_date(record.get(mapping["service_date"]))
            platform = parse_platform(record.get(mapping["platform"]) if "platform" in mapping else default_platform)
            label = clean_label(record.get(mapping["service_label"]) if "service_label" in mapping else "")
            values = {f: parse_count(record.get(mapping[f]), f) for f in FIELDS if f in mapping}
            if all(v is None for v in values.values()):
                raise StreamingError("No numbers on this row.")
            key = (d, label, platform)
            if key in seen:
                raise StreamingError("This date, service and platform appears twice in the file.")
            seen.add(key)
            note = record.get(mapping["note"]) if "note" in mapping else None
            if commit:
                _, outcome = save_number(d, label, platform, values, "csv", by=by,
                                         reason="CSV import", note=note or None)
            else:
                existing = StreamingNumber.query.filter_by(
                    service_date=d, service_label=label, platform=platform).first()
                outcome = "created" if existing is None else (
                    "unchanged" if all(getattr(existing, f) == v for f, v in values.items()) else "updated")
            result[outcome] += 1
        except StreamingError as exc:
            result["errors"].append({"line": index, "error": str(exc)})
    if commit:
        db.session.commit()
    return result
