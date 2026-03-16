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
