"""A fake Text In Church, so the connector and everything built on it can run
before the church has API access (``TEXT_IN_CHURCH_MOCK=1``).

Deterministic, with a plausible week: new guests who text in, a few who have been
answered and a few who have not, connect card submissions, and automated replies.
Names are obviously fake. Pagination, ``start_date`` and ``limit``/``offset`` behave
like the real endpoints.
"""

from datetime import datetime, timedelta

FIRST = ["Alex", "Blair", "Casey", "Devon", "Emery", "Finley", "Gray", "Harper", "Indigo", "Jules"]
LAST = ["Sample", "Example", "Placeholder", "Demo", "Testperson"]


def _now():
    return datetime.utcnow().replace(microsecond=0)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _build():
    now = _now()
    contacts, conversations, messages, cards = [], [], [], []
    mid = 1
    for i in range(1, 11):
        created = now - timedelta(days=i * 2, hours=i)
        contacts.append({
            "contact_id": str(1000 + i), "contact_first_name": FIRST[i - 1], "contact_last_name": LAST[i % len(LAST)],
            "contact_create_date": _fmt(created), "contact_source": "connect_card" if i % 3 == 0 else "text",
            "contact_active": "1", "contact_optout_sms": "0", "contact_email": f"fake{i}@example.invalid",
            "primary_phone": f"+1555000{i:04d}",
        })
        conversations.append({"conv_id": str(2000 + i), "contact_id": str(1000 + i), "conv_archived": "0"})
        # They text in; every other contact gets a human reply after a while.
        messages.append({"msg_id": str(mid), "conv_id": str(2000 + i), "msg_incoming": "1", "automated": "0",
                         "msg_send_time": _fmt(created + timedelta(minutes=1)), "msg_content": f"Hi, I'm new. Do you have a youth group? ({FIRST[i-1]})"})
        mid += 1
        messages.append({"msg_id": str(mid), "conv_id": str(2000 + i), "msg_incoming": "0", "automated": "1",
                         "msg_send_time": _fmt(created + timedelta(minutes=2)), "msg_content": "Thanks for texting Dalton First UMC!"})
        mid += 1
        if i % 2 == 0:
            messages.append({"msg_id": str(mid), "conv_id": str(2000 + i), "msg_incoming": "0", "automated": "0",
                             "msg_send_time": _fmt(created + timedelta(hours=3 + i)), "msg_content": "Welcome! Yes, we meet Wednesdays at 6."})
            mid += 1
        if i % 3 == 0:
            cards.append({"id": str(3000 + i), "contact_id": str(1000 + i), "collection_name": "Sunday Connect Card",
                          "created": _fmt(created)})
    return contacts, conversations, messages, cards


def respond(endpoint: str, params: dict):
    contacts, conversations, messages, cards = _build()
    data = {"contact.php": contacts, "conversation.php": conversations, "message.php": messages,
            "connectCardSubmission.php": cards}.get(endpoint)
    if data is None:
        raise KeyError(endpoint)
    if endpoint == "message.php" and params.get("start_date"):
        data = [m for m in data if m["msg_send_time"][:10] >= params["start_date"]]
    offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1500))
    return data[offset:offset + limit]
