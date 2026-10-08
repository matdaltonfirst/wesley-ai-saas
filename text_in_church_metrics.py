"""Texting numbers the Text In Church API does not provide, computed from synced data.

Everything here reads only ``tic_*`` tables. Names appear only in
``guests_awaiting_followup`` and the caller must hold ``data.text_in_church``.
"""

from datetime import datetime, timedelta
from statistics import median

from models import TicConnectCard, TicContact, TicConversation, TicMessage


def _since(days):
    return datetime.utcnow() - timedelta(days=days)


def message_volume(days: int = 28) -> dict:
    rows = TicMessage.query.filter(TicMessage.sent_at >= _since(days)).all()
    human_out = [m for m in rows if not m.incoming and not m.automated]
    return {"days": days, "incoming": sum(1 for m in rows if m.incoming),
            "outgoing_human": len(human_out), "outgoing_automated": sum(1 for m in rows if not m.incoming and m.automated)}


def new_contacts(days: int = 28) -> int:
    return TicContact.query.filter(TicContact.created_at >= _since(days)).count()


def connect_cards(days: int = 28) -> int:
    return TicConnectCard.query.filter(TicConnectCard.submitted_at >= _since(days)).count()


def response_times(days: int = 28) -> dict:
    """Minutes from each incoming text to the next human reply in the same conversation.

    Automated replies do not count as a response. Texts nobody has answered yet are
    reported separately and are not folded into the average.
    """
    msgs = (TicMessage.query.filter(TicMessage.sent_at >= _since(days + 7), TicMessage.conv_id.isnot(None))
            .order_by(TicMessage.conv_id, TicMessage.sent_at).all())
    by_conv = {}
    for m in msgs:
        by_conv.setdefault(m.conv_id, []).append(m)
    cutoff, waits, unanswered = _since(days), [], 0
    for items in by_conv.values():
        for i, m in enumerate(items):
            if not m.incoming or m.sent_at is None or m.sent_at < cutoff:
                continue
            reply = next((r for r in items[i + 1:] if not r.incoming and not r.automated and r.sent_at), None)
            if reply:
                waits.append((reply.sent_at - m.sent_at).total_seconds() / 60)
            else:
                unanswered += 1
    return {"days": days, "answered": len(waits), "unanswered": unanswered,
            "median_minutes": round(median(waits), 1) if waits else None,
            "average_minutes": round(sum(waits) / len(waits), 1) if waits else None}


def guests_awaiting_followup(days: int = 30, min_age_hours: int = 12) -> list:
    """People who recently reached out or filled a connect card and have had no human reply."""
    now = datetime.utcnow()
    out = []
    for c in TicContact.query.filter(TicContact.created_at >= _since(days), TicContact.active.is_(True)).all():
        if c.optout_sms or c.created_at is None or now - c.created_at < timedelta(hours=min_age_hours):
            continue
        convs = [v.conv_id for v in TicConversation.query.filter_by(contact_id=c.contact_id)]
        replied = TicMessage.query.filter(
            TicMessage.conv_id.in_(convs or ["-"]), TicMessage.incoming.is_(False), TicMessage.automated.is_(False)).first()
        if replied is None:
            out.append({"contact_id": c.contact_id, "name": " ".join(p for p in (c.first_name, c.last_name) if p) or "Unknown",
                        "since": c.created_at.isoformat() + "Z", "source": c.source or ""})
    return sorted(out, key=lambda g: g["since"])


def summary(days: int = 28) -> dict:
    return {"messages": message_volume(days), "new_contacts": new_contacts(days),
            "connect_cards": connect_cards(days), "response_times": response_times(days),
            "awaiting_followup": guests_awaiting_followup()}
