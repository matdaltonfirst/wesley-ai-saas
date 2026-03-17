"""Communications Request Triage routes."""

import os
from datetime import date, datetime

from flask import (
    Blueprint, request, jsonify, render_template,
    redirect, url_for, abort,
)
from flask_login import login_required, current_user

from models import db, CommsRequest
from comms_triage import determine_priority, determine_tier, generate_triage_explanation

comms_bp = Blueprint("comms", __name__)

# Valid values for each enum-like field
_VALID_REQUEST_TYPES  = {"graphic", "video"}
_VALID_AUDIENCES      = {"community", "church_members", "small_group"}
_VALID_TIMELINES      = {"this_week", "2_4_weeks", "1_plus_month"}
_VALID_STATUSES       = {"in_queue", "in_progress", "completed", "cancelled"}
_VALID_DELIVERABLES   = {"Flyer", "Slides", "Social Post", "Landing Page", "Other"}

_SORT_MAP = {
    "date_submitted": CommsRequest.created_at.desc(),
    "highest_priority": db.case(
        {"red": 1, "yellow": 2, "green": 3, "blue": 4},
        value=CommsRequest.triage_code,
        else_=5,
    ),
    "lowest_priority": db.case(
        {"red": 4, "yellow": 3, "green": 2, "blue": 1},
        value=CommsRequest.triage_code,
        else_=0,
    ),
    "event_date": CommsRequest.event_date.asc(),
}


def _get_anthropic_client():
    """Lazily create an Anthropic client from the environment API key."""
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(api_key=api_key)


def _run_triage(req: CommsRequest) -> None:
    """Populate triage_code, production_tier, estimated_completion, and
    triage_explanation on the given CommsRequest (does NOT commit)."""
    triage_code, est_completion = determine_priority(
        timeline=req.timeline,
        request_type=req.request_type,
        deliverables=req.deliverables,
        key_info=req.key_info_text or "",
        special_notes=req.special_notes or "",
    )
    tier = determine_tier(req.target_audience)

    req.triage_code          = triage_code
    req.production_tier      = tier
    req.estimated_completion = est_completion

    try:
        client = _get_anthropic_client()
        explanation = generate_triage_explanation(
            anthropic_client=client,
            request_data={
                "event_name":      req.event_name,
                "target_audience": req.target_audience,
                "timeline":        req.timeline,
                "deliverables":    req.deliverables,
                "special_notes":   req.special_notes,
            },
            priority=triage_code,
            tier=tier,
            est_completion=est_completion,
        )
        req.triage_explanation = explanation
    except Exception:
        # Non-fatal: triage values are already set; explanation will be blank
        req.triage_explanation = None


# ── Dashboard ─────────────────────────────────────────────────────────────────

@comms_bp.route("/comms")
@login_required
def comms_dashboard():
    church_id = current_user.church_id
    total     = CommsRequest.query.filter_by(church_id=church_id).count()
    red       = CommsRequest.query.filter_by(church_id=church_id, triage_code="red").count()
    yellow    = CommsRequest.query.filter_by(church_id=church_id, triage_code="yellow").count()
    green     = CommsRequest.query.filter_by(church_id=church_id, triage_code="green").count()
    blue      = CommsRequest.query.filter_by(church_id=church_id, triage_code="blue").count()
    in_queue  = CommsRequest.query.filter_by(church_id=church_id, status="in_queue").count()
    in_prog   = CommsRequest.query.filter_by(church_id=church_id, status="in_progress").count()

    return render_template(
        "comms/dashboard.html",
        church_name=current_user.church.name,
        user_email=current_user.email,
        user_role=current_user.role,
        stats={
            "total":       total,
            "red":         red,
            "yellow":      yellow,
            "green":       green,
            "blue":        blue,
            "in_queue":    in_queue,
            "in_progress": in_prog,
        },
    )


# ── New request form ──────────────────────────────────────────────────────────

@comms_bp.route("/comms/new", methods=["GET"])
@login_required
def comms_new_get():
    return render_template(
        "comms/new_request.html",
        church_name=current_user.church.name,
        user_email=current_user.email,
        user_role=current_user.role,
        today=date.today().isoformat(),
    )


@comms_bp.route("/comms/new", methods=["POST"])
@login_required
def comms_new_post():
    f = request.form

    # ── Required fields ──────────────────────────────────────────────────────
    request_type     = (f.get("request_type") or "").strip()
    event_name       = (f.get("event_name") or "").strip()
    event_date_str   = (f.get("event_date") or "").strip()
    target_audience  = (f.get("target_audience") or "").strip()
    timeline         = (f.get("timeline") or "").strip()
    deliverables     = f.getlist("deliverables")

    # ── Optional fields ──────────────────────────────────────────────────────
    ministry_department = (f.get("ministry_department") or "").strip()
    key_info_text       = (f.get("key_info_text") or "").strip()
    special_notes       = (f.get("special_notes") or "").strip()

    # ── Validation ───────────────────────────────────────────────────────────
    errors = []
    if request_type not in _VALID_REQUEST_TYPES:
        errors.append("Invalid request type.")
    if not event_name:
        errors.append("Event name is required.")
    if len(event_name) > 200:
        errors.append("Event name must be 200 characters or fewer.")
    if not event_date_str:
        errors.append("Event date is required.")
    if target_audience not in _VALID_AUDIENCES:
        errors.append("Invalid target audience.")
    if timeline not in _VALID_TIMELINES:
        errors.append("Invalid timeline.")
    if not deliverables:
        errors.append("Select at least one deliverable.")
    invalid_del = [d for d in deliverables if d not in _VALID_DELIVERABLES]
    if invalid_del:
        errors.append("One or more invalid deliverables.")

    event_date = None
    if event_date_str:
        try:
            event_date = date.fromisoformat(event_date_str)
        except ValueError:
            errors.append("Invalid event date format.")

    if errors:
        return render_template(
            "comms/new_request.html",
            church_name=current_user.church.name,
            user_email=current_user.email,
            user_role=current_user.role,
            today=date.today().isoformat(),
            errors=errors,
            form_data=f,
        ), 400

    # ── Build and triage the request ─────────────────────────────────────────
    req = CommsRequest(
        church_id           = current_user.church_id,
        submitter_id        = current_user.id,
        submitter_name      = current_user.email,
        ministry_department = ministry_department[:100] if ministry_department else None,
        request_type        = request_type,
        event_name          = event_name,
        event_date          = event_date,
        target_audience     = target_audience,
        timeline            = timeline,
        deliverables        = deliverables,
        key_info_text       = key_info_text[:5000] if key_info_text else None,
        special_notes       = special_notes[:2000] if special_notes else None,
    )
    _run_triage(req)
    db.session.add(req)
    db.session.commit()

    return redirect(url_for("comms.comms_my_requests"))


# ── My requests ───────────────────────────────────────────────────────────────

@comms_bp.route("/comms/my-requests")
@login_required
def comms_my_requests():
    requests = (
        CommsRequest.query
        .filter_by(church_id=current_user.church_id, submitter_id=current_user.id)
        .order_by(CommsRequest.created_at.desc())
        .all()
    )
    return render_template(
        "comms/my_requests.html",
        church_name=current_user.church.name,
        user_email=current_user.email,
        user_role=current_user.role,
        requests=requests,
        today=date.today(),
    )


# ── Admin queue ───────────────────────────────────────────────────────────────

@comms_bp.route("/comms/admin")
@login_required
def comms_admin():
    if current_user.role != "admin":
        abort(403)

    sort_key = request.args.get("sort", "date_submitted")
    order_by = _SORT_MAP.get(sort_key, CommsRequest.created_at.desc())

    active = (
        CommsRequest.query
        .filter(
            CommsRequest.church_id == current_user.church_id,
            CommsRequest.status.in_(["in_queue", "in_progress"]),
        )
        .order_by(order_by)
        .all()
    )
    completed = (
        CommsRequest.query
        .filter(
            CommsRequest.church_id == current_user.church_id,
            CommsRequest.status.in_(["completed", "cancelled"]),
        )
        .order_by(CommsRequest.completed_at.desc())
        .all()
    )

    total    = len(active) + len(completed)
    in_queue = sum(1 for r in active if r.status == "in_queue")
    in_prog  = sum(1 for r in active if r.status == "in_progress")
    done     = len(completed)

    return render_template(
        "comms/admin.html",
        church_name=current_user.church.name,
        user_email=current_user.email,
        user_role=current_user.role,
        active=active,
        completed=completed,
        today=date.today(),
        sort_key=sort_key,
        stats={
            "total":       total,
            "in_queue":    in_queue,
            "in_progress": in_prog,
            "completed":   done,
        },
    )


# ── Update status (admin only) ────────────────────────────────────────────────

@comms_bp.route("/comms/<string:req_id>/status", methods=["POST"])
@login_required
def comms_update_status(req_id):
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403

    req = CommsRequest.query.filter_by(
        id=req_id, church_id=current_user.church_id
    ).first_or_404()

    data   = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip()

    if status not in _VALID_STATUSES:
        return jsonify({"error": "Invalid status value."}), 400

    req.status = status
    if status in ("completed", "cancelled") and not req.completed_at:
        req.completed_at = datetime.utcnow()
    elif status in ("in_queue", "in_progress"):
        req.completed_at = None

    db.session.commit()
    return jsonify({"ok": True, "status": req.status})


# ── Re-evaluate triage ────────────────────────────────────────────────────────

@comms_bp.route("/comms/<string:req_id>/re-evaluate", methods=["POST"])
@login_required
def comms_re_evaluate(req_id):
    req = CommsRequest.query.filter_by(
        id=req_id, church_id=current_user.church_id
    ).first_or_404()

    # Staff can only re-evaluate their own requests; admins can re-evaluate any
    if current_user.role != "admin" and req.submitter_id != current_user.id:
        return jsonify({"error": "Forbidden."}), 403

    _run_triage(req)
    db.session.commit()

    return jsonify({
        "ok":                  True,
        "triage_code":         req.triage_code,
        "production_tier":     req.production_tier,
        "estimated_completion": req.estimated_completion,
        "triage_explanation":  req.triage_explanation,
    })


# ── Detail modal (JSON) ───────────────────────────────────────────────────────

@comms_bp.route("/comms/<string:req_id>/detail")
@login_required
def comms_detail(req_id):
    req = CommsRequest.query.filter_by(
        id=req_id, church_id=current_user.church_id
    ).first_or_404()

    # Staff can only view their own requests; admins can view all
    if current_user.role != "admin" and req.submitter_id != current_user.id:
        return jsonify({"error": "Forbidden."}), 403

    return jsonify({
        "id":                   req.id,
        "submitter_name":       req.submitter_name,
        "ministry_department":  req.ministry_department,
        "request_type":         req.request_type,
        "event_name":           req.event_name,
        "event_date":           req.event_date.isoformat() if req.event_date else None,
        "target_audience":      req.target_audience,
        "timeline":             req.timeline,
        "deliverables":         req.deliverables,
        "key_info_text":        req.key_info_text,
        "special_notes":        req.special_notes,
        "status":               req.status,
        "triage_code":          req.triage_code,
        "production_tier":      req.production_tier,
        "estimated_completion": req.estimated_completion,
        "triage_explanation":   req.triage_explanation,
        "created_at":           req.created_at.isoformat(),
        "completed_at":         req.completed_at.isoformat() if req.completed_at else None,
    })
