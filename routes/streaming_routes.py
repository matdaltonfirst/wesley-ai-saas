"""The weekly streaming numbers: the page, the data, manual entry, CSV in and out."""

from datetime import date, timedelta

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import current_user

import streaming as S
from audit import log_event
from models import StreamingNumber
from organization import get_org
from permissions import can, require

streaming_bp = Blueprint("streaming", __name__)


def _range():
    try:
        end = S.parse_date(request.args.get("end")) if request.args.get("end") else date.today()
        start = S.parse_date(request.args.get("start")) if request.args.get("start") else end - timedelta(weeks=12)
    except S.StreamingError as exc:
        raise ValueError(str(exc))
    if start > end or (end - start).days > 366 * 3:
        raise ValueError("Choose a start date before the end date, within three years.")
    return start, end


@streaming_bp.route("/streaming")
@require("streaming.read")
def streaming_page():
    return render_template(
        "streaming.html", church_name=get_org().name, user_email=current_user.email,
        can_write=can("streaming.write"), platforms=S.PLATFORMS,
    )


@streaming_bp.route("/api/streaming/summary")
@require("streaming.read")
def summary():
    try:
        weeks = int(request.args.get("weeks", 12))
    except ValueError:
        weeks = 12
    return jsonify(S.weekly_summary(weeks))


@streaming_bp.route("/api/streaming/numbers")
@require("streaming.read")
def numbers():
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"numbers": S.numbers_between(start, end), "can_write": can("streaming.write")})


@streaming_bp.route("/api/streaming/numbers", methods=["POST"])
@require("streaming.write")
def save():
    """Enter or correct one platform's numbers for one service."""
    data = request.get_json(silent=True) or {}
    try:
        d = S.parse_date(data.get("service_date"))
        platform = S.parse_platform(data.get("platform"))
        if d > date.today() + timedelta(days=1):
            raise S.StreamingError("That date is in the future.")
        values = {f: S.parse_count(data.get(f), f) for f in S.FIELDS if f in data}
        if not values:
            raise S.StreamingError("Enter at least one number.")
        label = S.clean_label(data.get("service_label"))
        existing = StreamingNumber.query.filter_by(service_date=d, service_label=label, platform=platform).first()
        reason = (data.get("reason") or "").strip()
        if existing and not reason:
            raise S.StreamingError("Say briefly why you are changing an existing number (for the edit history).")
        row, outcome = S.save_number(d, label, platform, values, "manual", by=current_user.email,
                                     reason=reason or "Entered by hand", note=data.get("note"))
    except S.StreamingError as exc:
        return jsonify({"error": str(exc)}), 400
    from models import db
    db.session.commit()
    log_event("streaming.edit", source=f"{d.isoformat()} {platform}", detail={"outcome": outcome})
    return jsonify({"ok": True, "outcome": outcome, "number": S.row_dict(row)}), 201 if outcome == "created" else 200


@streaming_bp.route("/api/streaming/numbers/<int:number_id>/history")
@require("streaming.read")
def number_history(number_id):
    if not StreamingNumber.query.get(number_id):
        return jsonify({"error": "Not found."}), 404
    return jsonify({"history": S.history(number_id)})


@streaming_bp.route("/api/streaming/export.csv")
@require("streaming.read")
def export():
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    log_event("streaming.export", source=f"{start} to {end}")
    return Response(S.export_csv(start, end), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="streaming-{start}-to-{end}.csv"'})


@streaming_bp.route("/api/streaming/import", methods=["POST"])
@require("streaming.write")
def import_numbers():
    """Upload a CSV. Send commit=false first to preview what would change."""
    text = None
    if "file" in request.files:
        raw = request.files["file"].read(2_000_001)
        if len(raw) > 2_000_000:
            return jsonify({"error": "That file is too large (2 MB limit)."}), 400
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return jsonify({"error": "That file is not plain text. Save it as CSV (UTF-8) and try again."}), 400
    else:
        text = (request.get_json(silent=True) or {}).get("csv")
    if not text or not text.strip():
        return jsonify({"error": "No file was provided."}), 400
    form = request.form if request.files else (request.get_json(silent=True) or {})
    commit = str(form.get("commit", "true")).lower() not in ("false", "0", "no")
    platform = form.get("platform") or None
    try:
        result = S.import_csv(text, current_user.email, commit=commit, default_platform=platform)
    except S.StreamingError as exc:
        return jsonify({"error": str(exc)}), 400
    if commit:
        log_event("streaming.import", detail={k: result[k] for k in ("created", "updated", "unchanged", "skipped")}
                  | {"errors": len(result["errors"])})
    return jsonify(result)
