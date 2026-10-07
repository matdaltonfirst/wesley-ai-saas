"""Email sending functions (all use Resend)."""

import logging

import resend
from flask import render_template

log = logging.getLogger("wesley")


def send_reset_email(to_email: str, reset_url: str, from_email: str, support_email: str) -> None:
    """Send a branded HTML password-reset email via Resend."""
    html = render_template(
        "emails/reset_password.html",
        reset_url=reset_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "Reset your Wesley AI password",
            "html": html,
        })
    except Exception as exc:
        log.error("Password reset email failed for %s: %s", to_email, exc)


def send_guest_connection_email(
    to_email: str, church_name: str,
    guest_name: str, guest_email: str, guest_phone: str,
    interest_area: str, opening_message: str,
    dashboard_url: str, from_email: str, support_email: str,
) -> None:
    """Notify church staff that a new guest connection was submitted via the chat widget."""
    html = render_template(
        "emails/guest_connection.html",
        church_name=church_name,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        interest_area=interest_area,
        opening_message=opening_message,
        dashboard_url=dashboard_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": f"New Guest Connection — {guest_name}",
            "html": html,
        })
    except Exception as exc:
        log.error("Guest connection email failed for %s: %s", to_email, exc)


def send_weekly_digest_email(
    to_email: str, church_name: str, stats: dict,
    from_email: str, app_url: str, support_email: str,
) -> None:
    """Send the Monday-morning widget activity digest via Resend."""
    html = render_template(
        "emails/weekly_digest.html",
        church_name=church_name,
        stats=stats,
        app_url=app_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": f"Your week with Wesley — {stats['conversations']} conversations at {church_name}",
            "html": html,
        })
    except Exception as exc:
        log.error("Weekly digest email failed for %s: %s", to_email, exc)


def send_sermon_packet_email(
    to_email: str, church_name: str, sermon, content: dict,
    from_email: str, app_url: str, support_email: str,
) -> None:
    """Send the Monday packet — Sunday's message, ready to post."""
    html = render_template(
        "emails/sermon_packet.html",
        church_name=church_name,
        sermon=sermon,
        content=content,
        app_url=app_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            # Names the sermon rather than the product: it should read as a
            # note about Sunday, not as a notification from software.
            "subject": f"Sunday, ready to post — {sermon.title}",
            "html": html,
        })
    except Exception as exc:
        log.error("Sermon packet email failed for %s: %s", to_email, exc)
