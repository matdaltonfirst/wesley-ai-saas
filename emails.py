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


def send_welcome_email(to_email: str, church_name: str, trial_ends_at, from_email: str, app_url: str, support_email: str) -> None:
    """Send a branded welcome email to a new signup via Resend."""
    html = render_template(
        "emails/welcome.html",
        church_name=church_name,
        trial_date=trial_ends_at.strftime("%B %d, %Y"),
        app_url=app_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": f"Welcome to Wesley AI, {church_name}!",
            "html": html,
        })
    except Exception as exc:
        log.error("Welcome email failed for %s: %s", to_email, exc)


def send_trial_expiring_email(to_email: str, church_name: str, trial_ends_at, from_email: str, app_url: str, support_email: str) -> None:
    """Send a trial-expiring warning email (4 days before trial ends) via Resend."""
    html = render_template(
        "emails/trial_expiring.html",
        church_name=church_name,
        trial_date=trial_ends_at.strftime("%B %d, %Y"),
        app_url=app_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "Your Wesley AI trial ends in 4 days",
            "html": html,
        })
    except Exception as exc:
        log.error("Trial expiring email failed for %s: %s", to_email, exc)


def send_payment_confirmation_email(to_email: str, church_name: str, from_email: str, app_url: str, support_email: str) -> None:
    """Send a payment confirmation email after a successful Stripe checkout via Resend."""
    html = render_template(
        "emails/payment_confirmation.html",
        church_name=church_name,
        app_url=app_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "Your Wesley AI subscription is active",
            "html": html,
        })
    except Exception as exc:
        log.error("Payment confirmation email failed for %s: %s", to_email, exc)


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


def send_invite_email(to_email: str, church_name: str, invite_url: str, from_email: str, support_email: str) -> None:
    """Send a branded staff invitation email via Resend."""
    html = render_template(
        "emails/invite.html",
        church_name=church_name,
        invite_url=invite_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": f"You've been invited to join {church_name} on Wesley AI",
            "html": html,
        })
    except Exception as exc:
        log.error("Invite email failed for %s: %s", to_email, exc)


# ── Manual billing emails ─────────────────────────────────────────────────────

def send_stripe_invite_email(
    to_email: str, church_name: str, checkout_url: str,
    from_email: str, support_email: str,
) -> None:
    """Send a Stripe subscription invite to a church currently on manual billing."""
    html = render_template(
        "emails/stripe_invite.html",
        church_name=church_name,
        checkout_url=checkout_url,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "Time to set up your Wesley AI subscription",
            "html": html,
        })
    except Exception as exc:
        log.error("Stripe invite email failed for %s: %s", to_email, exc)


def send_manual_expiring_30_email(
    to_email: str, church_name: str, expires_date: str,
    from_email: str, support_email: str,
) -> None:
    """Warn a church that their manual subscription expires in ~30 days."""
    html = render_template(
        "emails/manual_expiring_30.html",
        church_name=church_name,
        expires_date=expires_date,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "Your Wesley AI subscription renews soon",
            "html": html,
        })
    except Exception as exc:
        log.error("Manual expiring-30 email failed for %s: %s", to_email, exc)


def send_manual_expiring_7_email(
    to_email: str, church_name: str, expires_date: str,
    from_email: str, support_email: str,
) -> None:
    """Warn a church that their manual subscription expires in ~7 days."""
    html = render_template(
        "emails/manual_expiring_7.html",
        church_name=church_name,
        expires_date=expires_date,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "Your Wesley AI subscription expires in 7 days",
            "html": html,
        })
    except Exception as exc:
        log.error("Manual expiring-7 email failed for %s: %s", to_email, exc)


def send_manual_expired_email(
    to_email: str, church_name: str, expires_date: str,
    from_email: str, support_email: str,
) -> None:
    """Notify a church that their manual subscription has expired."""
    html = render_template(
        "emails/manual_expired.html",
        church_name=church_name,
        expires_date=expires_date,
        support_email=support_email,
    )
    try:
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": "Your Wesley AI subscription has expired",
            "html": html,
        })
    except Exception as exc:
        log.error("Manual expired email failed for %s: %s", to_email, exc)


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
