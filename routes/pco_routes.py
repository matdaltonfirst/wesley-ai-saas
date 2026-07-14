"""Planning Center routes: OAuth connect/callback, settings, and manual sync."""

import logging
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, redirect, session
from flask_login import login_required, current_user

import pco
from models import db, PcoConnection, GuestConnection
from pco import PcoError

log = logging.getLogger("wesley")

pco_bp = Blueprint("pco", __name__)


@pco_bp.route("/api/pco/status")
@login_required
def pco_status():
    if not pco.is_configured():
        return jsonify({
            "configured": False, "connected": False,
            "can_manage": current_user.role == "admin",
        })
    conn = PcoConnection.query.filter_by(church_id=current_user.church_id).first()
    if not conn:
        return jsonify({
            "configured": True, "connected": False,
            "can_manage": current_user.role == "admin",
        })
    return jsonify({
        "configured": True,
        "connected": True,
        "organization_name": conn.organization_name or "",
        "auto_sync": conn.auto_sync,
        "workflow_id": conn.workflow_id or "",
        "workflow_name": conn.workflow_name or "",
        "can_manage": current_user.role == "admin",
    })


@pco_bp.route("/pco/connect")
@login_required
def pco_connect():
    if not pco.is_configured():
        return "Planning Center integration is not enabled on this server.", 400
    if current_user.role != "admin":
        return "Only church admins can connect Planning Center.", 403
    state = secrets.token_urlsafe(24)
    session["pco_oauth_state"] = state
    # Remember where to land after the OAuth round-trip (dashboard or wizard)
    session["pco_return"] = (
        "onboarding" if request.args.get("return") == "onboarding" else "dashboard"
    )
    return redirect(pco.authorize_url(state))


@pco_bp.route("/pco/callback")
@login_required
def pco_callback():
    state = request.args.get("state", "")
    saved = session.pop("pco_oauth_state", None)
    if not saved or not secrets.compare_digest(state, saved):
        return "Sign-in session expired — please try connecting again.", 400
    return_to = session.pop("pco_return", "dashboard")

    def _destination(result):
        if return_to == "onboarding":
            return f"/onboarding?step=5&pco={result}"
        return "/dashboard#integrations"

    if request.args.get("error"):
        # User clicked "Deny" on the PCO consent screen
        return redirect(_destination("denied"))

    code = request.args.get("code", "")
    try:
        tokens = pco.exchange_code(code)
    except PcoError as exc:
        return str(exc), 502

    conn = PcoConnection.query.filter_by(church_id=current_user.church_id).first()
    if not conn:
        conn = PcoConnection(church_id=current_user.church_id)
        db.session.add(conn)
    conn.access_token = pco.encrypt_token(tokens["access_token"])
    conn.refresh_token = pco.encrypt_token(tokens["refresh_token"])
    conn.token_expires_at = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 7200))
    conn.connected_by_id = current_user.id
    db.session.commit()

    try:
        conn.organization_name = pco.get_organization_name(conn)
        db.session.commit()
    except PcoError:
        pass  # connection still works; name is cosmetic

    log.info("PCO connected: church_id=%d org=%r",
             conn.church_id, conn.organization_name)
    return redirect(_destination("connected"))


@pco_bp.route("/api/pco/disconnect", methods=["POST"])
@login_required
def pco_disconnect():
    if current_user.role != "admin":
        return jsonify({"error": "Only church admins can disconnect."}), 403
    conn = PcoConnection.query.filter_by(church_id=current_user.church_id).first()
    if conn:
        db.session.delete(conn)
        db.session.commit()
    return jsonify({"ok": True})


@pco_bp.route("/api/pco/workflows")
@login_required
def pco_workflows():
    conn = PcoConnection.query.filter_by(church_id=current_user.church_id).first()
    if not conn:
        return jsonify({"error": "Planning Center is not connected."}), 400
    try:
        return jsonify({"workflows": pco.list_workflows(conn)})
    except PcoError as exc:
        return jsonify({"error": str(exc)}), 502


@pco_bp.route("/api/pco/settings", methods=["POST"])
@login_required
def pco_settings():
    if current_user.role != "admin":
        return jsonify({"error": "Only church admins can change integration settings."}), 403
    conn = PcoConnection.query.filter_by(church_id=current_user.church_id).first()
    if not conn:
        return jsonify({"error": "Planning Center is not connected."}), 400
    data = request.get_json(silent=True) or {}
    if "auto_sync" in data:
        conn.auto_sync = bool(data["auto_sync"])
    if "workflow_id" in data:
        conn.workflow_id = (str(data["workflow_id"]).strip() or None)
        conn.workflow_name = (data.get("workflow_name") or "").strip()[:200] or None
    db.session.commit()
    return jsonify({"ok": True})


@pco_bp.route("/api/guest-connection/<int:gc_id>/sync-pco", methods=["POST"])
@login_required
def sync_guest_to_pco(gc_id):
    gc = GuestConnection.query.filter_by(
        id=gc_id, church_id=current_user.church_id
    ).first()
    if not gc:
        return jsonify({"error": "Guest connection not found."}), 404
    if not PcoConnection.query.filter_by(church_id=current_user.church_id).first():
        return jsonify({"error": "Planning Center is not connected."}), 400

    ok = pco.sync_guest_connection(gc, force=True)
    return jsonify({
        "ok": ok,
        "pco_person_id": gc.pco_person_id,
        "pco_url": pco.person_url(gc.pco_person_id) if gc.pco_person_id else None,
        "error": gc.pco_sync_error,
    }), (200 if ok else 502)
