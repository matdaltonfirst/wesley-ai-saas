"""Stripe billing routes: subscribe page, checkout, webhook, billing portal."""

import os
import logging
import threading
from datetime import datetime

import stripe
from flask import Blueprint, request, jsonify, render_template, redirect, url_for
from flask_login import login_required, current_user

from models import db, User, Church
from config import FROM_EMAIL, APP_URL, SUPPORT_EMAIL
from helpers import validate_csrf
from emails import send_payment_confirmation_email

log = logging.getLogger("wesley")

stripe_bp = Blueprint("stripe", __name__)


@stripe_bp.route("/subscribe")
@login_required
def subscribe_page():
    church = current_user.church
    days_left = None
    if church.trial_ends_at and church.trial_ends_at > datetime.utcnow():
        days_left = (church.trial_ends_at - datetime.utcnow()).days
    return render_template("subscribe.html",
                           user_email=current_user.email,
                           days_left=days_left)


@stripe_bp.route("/stripe/checkout", methods=["POST"])
@login_required
def stripe_checkout():
    validate_csrf()
    if not stripe.api_key:
        return "STRIPE_SECRET_KEY is not configured.", 500

    billing_cycle = request.form.get("billing_cycle", "monthly")
    if billing_cycle == "annual":
        price_id = os.getenv("STRIPE_ANNUAL_PRICE_ID")
        if not price_id:
            return "STRIPE_ANNUAL_PRICE_ID is not configured.", 500
    else:
        price_id = os.getenv("STRIPE_MONTHLY_PRICE_ID")
        if not price_id:
            return "STRIPE_MONTHLY_PRICE_ID is not configured.", 500

    church = current_user.church

    try:
        customer_id = church.stripe_customer_id
        if not customer_id:
            existing = stripe.Customer.list(email=current_user.email, limit=1)
            if existing.data:
                customer_id = existing.data[0].id
            else:
                customer = stripe.Customer.create(
                    email=current_user.email,
                    metadata={"church_id": str(current_user.church_id)},
                )
                customer_id = customer.id
            church.stripe_customer_id = customer_id
            db.session.commit()

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer=customer_id,
            client_reference_id=str(current_user.church_id),
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=url_for("stripe.stripe_success", _external=True),
            cancel_url=url_for("stripe.subscribe_page", _external=True),
        )
        return redirect(session.url, code=303)
    except stripe.StripeError as e:
        return render_template("subscribe.html",
                               user_email=current_user.email,
                               days_left=None,
                               stripe_error=getattr(e, "user_message", str(e)))


@stripe_bp.route("/stripe/success")
@login_required
def stripe_success():
    return render_template("stripe_success.html")


@stripe_bp.route("/billing/portal")
@login_required
def billing_portal():
    """Redirect the logged-in church admin to the Stripe Customer Portal."""
    if not stripe.api_key:
        return "STRIPE_SECRET_KEY is not configured.", 500

    church = current_user.church
    customer_id = church.stripe_customer_id

    if not customer_id:
        try:
            existing = stripe.Customer.list(email=current_user.email, limit=1)
            if existing.data:
                customer_id = existing.data[0].id
                church.stripe_customer_id = customer_id
                db.session.commit()
        except stripe.StripeError:
            pass

    if not customer_id:
        return render_template(
            "subscribe.html",
            user_email=current_user.email,
            days_left=None,
            stripe_error="No billing account found. Please subscribe first.",
        )

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=url_for("pages.management_dashboard", _external=True),
        )
        return redirect(portal_session.url, code=303)
    except stripe.StripeError as e:
        return render_template(
            "subscribe.html",
            user_email=current_user.email,
            days_left=None,
            stripe_error=getattr(e, "user_message", str(e)),
        )


@stripe_bp.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload       = request.get_data()
    sig_header    = request.headers.get("Stripe-Signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        return jsonify({"error": "Invalid payload"}), 400
    except stripe.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    etype = event["type"]

    if etype == "checkout.session.completed":
        sess        = event["data"]["object"]
        church_ref  = sess.get("client_reference_id")
        sub_id      = sess.get("subscription")
        customer_id = sess.get("customer")
        if church_ref and sub_id:
            try:
                church_id_int = int(church_ref)
            except (ValueError, TypeError):
                log.error("Stripe webhook: invalid client_reference_id=%r", church_ref)
                return jsonify({"ok": True})
            church = Church.query.get(church_id_int)
            if church:
                church.stripe_subscription_id = sub_id
                if customer_id and not church.stripe_customer_id:
                    church.stripe_customer_id = customer_id
                db.session.commit()
                log.info("Stripe webhook: church_id=%s subscribed (%s)", church_ref, sub_id)

                first_user = User.query.filter_by(church_id=church.id).order_by(User.id).first()
                if first_user:
                    _cname = church.name
                    _email = first_user.email
                    threading.Thread(
                        target=send_payment_confirmation_email,
                        args=(_email, _cname, FROM_EMAIL, APP_URL, SUPPORT_EMAIL),
                        daemon=True,
                    ).start()

    elif etype == "customer.subscription.deleted":
        sub    = event["data"]["object"]
        sub_id = sub["id"]
        church = Church.query.filter_by(stripe_subscription_id=sub_id).first()
        if church:
            church.stripe_subscription_id = None
            db.session.commit()
            log.info("Stripe webhook: subscription %s cancelled", sub_id)

    return jsonify({"ok": True})
