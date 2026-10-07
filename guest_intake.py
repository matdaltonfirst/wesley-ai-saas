"""Recording a website guest, syncing them to Planning Center, and telling staff.

The public chatbot calls ``record_guest`` and gets back nothing but success. It
is the only place the public path touches staff records, and it can write but
never read them: the visitor learns nothing about who works here or who else
has filled in the form.
"""

import logging
import threading

from flask import current_app

from config import APP_URL, FROM_EMAIL, SUPPORT_EMAIL
from emails import send_guest_connection_email
from models import GuestConnection, PcoConnection, db
from organization import get_org
from permissions import people_with

log = logging.getLogger("wesley")


def _staff_to_notify() -> list:
    """Emails of everyone whose role may update guest connections."""
    return sorted({u.email for u in people_with("guests.write")})


def record_guest(name, email, phone, interest_area, opening_message) -> None:
    """Store the guest, queue a Planning Center sync, notify staff. Raises on a save failure."""
    gc = GuestConnection(
        name=name, email=email, phone=phone or None, interest_area=interest_area,
        opening_message=opening_message or None,
    )
    db.session.add(gc)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    app = current_app._get_current_object()

    if PcoConnection.query.filter_by(auto_sync=True).first():
        import pco
        pco.queue_guest_sync(gc)
        gc_id = gc.id

        def _sync():
            try:
                with app.app_context():
                    target = GuestConnection.query.get(gc_id)
                    if target:
                        pco.sync_guest_connection(target)
            except Exception:
                log.exception("Background guest sync failed for guest_connection_id=%s", gc_id)
        threading.Thread(target=_sync, daemon=True).start()

    church_name = get_org().name
    dashboard_url = APP_URL.rstrip("/") + "/dashboard#guest-connections"
    for to in _staff_to_notify():
        def _send(to=to):
            try:
                with app.app_context():
                    send_guest_connection_email(
                        to, church_name, name, email, phone, interest_area,
                        opening_message, dashboard_url, FROM_EMAIL, SUPPORT_EMAIL)
            except Exception:
                log.exception("Failed sending guest connection email to %s", to)
        threading.Thread(target=_send, daemon=True).start()
