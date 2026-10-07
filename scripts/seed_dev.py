"""Fill a LOCAL development database with fake data so the app runs end to end.

    python scripts/seed_dev.py

Safe by construction: refuses to run when DATABASE_URL is set (that is Postgres,
i.e. a real deployment) or when DATA_DIR already holds a database with users.

Creates one account per role (plus one with no role) on the church domain. All use the
password in SEED_PASSWORD, which defaults to the throwaway value below. It is
for local use only and appears nowhere else.
"""
import os
import sys
from datetime import date, datetime, timedelta

DEFAULT_PASSWORD = "dev-only-password-123"


def main() -> int:
    if os.getenv("DATABASE_URL"):
        print("Refusing to seed: DATABASE_URL is set, so this is not a local database.")
        return 1

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from werkzeug.security import generate_password_hash

    from app import app
    from config import ORG_DOMAIN
    from models import (
        CalendarEvent, ChurchCalendar, CommsRequest, Document, GuestConnection,
        QnAPair, Sermon, SermonSource, TextSnippet, User, UserRole,
        WidgetConversation, WidgetMessage, db,
    )
    from organization import get_org

    password = os.getenv("SEED_PASSWORD", DEFAULT_PASSWORD)
    with app.app_context():
        if User.query.count():
            print("Refusing to seed: this database already has users.")
            return 1

        org = get_org()
        org.website_url = "https://example.org"
        org.church_city = "Dalton, GA"
        pw = generate_password_hash(password, method="pbkdf2:sha256")
        people = {
            "dev-admin": ["admin"], "dev-comms": ["comms"], "dev-assistant": ["admin_assistant"],
            "dev-family": ["family"], "dev-music": ["music"], "dev-pastor": ["pastoral"],
            "dev-norole": [],
        }
        admin = None
        for local, roles in people.items():
            u = User(email=f"{local}@{ORG_DOMAIN}", password_hash=pw)
            db.session.add(u)
            db.session.flush()
            for r in roles:
                db.session.add(UserRole(user_id=u.id, role=r))
            admin = admin or u

        db.session.add_all([
            TextSnippet(title="Service times", category="Service & Worship",
                        content="Modern worship is Sundays at 9:30 AM. Traditional worship is Sundays at 11:00 AM."),
            TextSnippet(title="Nursery", category="Practical Info",
                        content="The nursery is open for infants through age 3 during both services."),
            TextSnippet(title="Staff door code (fake)", category="Practical Info", audience="staff",
                        content="The staff entrance code is 0000. Staff only: never shown to the chatbot."),
            QnAPair(question="Do you baptize infants?",
                    answer="Yes. Contact the church office to schedule a baptism with one of our pastors."),
        ])

        cal = ChurchCalendar(url="https://example.org/fake.ics", label="Church calendar", event_count=3)
        db.session.add(cal)
        db.session.flush()
        today = datetime.combine(date.today(), datetime.min.time())
        for i, title in enumerate(["Fall Festival", "Youth Night", "Community Dinner"], start=1):
            db.session.add(CalendarEvent(calendar_id=cal.id, title=title, location="Fellowship Hall",
                                         starts_at=today + timedelta(days=7 * i, hours=18),
                                         ends_at=today + timedelta(days=7 * i, hours=20)))

        source = SermonSource(channel_url="https://youtube.com/@example", channel_id="UCdevfake000000000000000",
                              channel_title="Example Church")
        db.session.add(source)
        db.session.flush()
        db.session.add(Sermon(source_id=source.id, video_id="devfake0001", title="WHO IS MY NEIGHBOR?",
                              published_at=today - timedelta(days=3), status="ingested",
                              summary="A message on the parable of the Good Samaritan.",
                              main_points="Love is concrete\nMercy crosses boundaries",
                              scriptures="Luke 10:25-37", transcript="Fake transcript text for development."))

        db.session.add(CommsRequest(submitter_id=1, submitter_name=admin.email, request_type="graphic",
                                    event_name="Fall Festival", event_date=date.today() + timedelta(days=30),
                                    target_audience="community", timeline="2_4_weeks",
                                    deliverables=["Flyer", "Social Post"], triage_code="yellow",
                                    production_tier=1, estimated_completion="5-7 days"))
        db.session.add(GuestConnection(name="Pat Visitor", email="pat@example.org",
                                       interest_area="Youth", opening_message="Do you have a youth group?"))
        wc = WidgetConversation(session_id="devfakesession0001")
        db.session.add(wc)
        db.session.flush()
        db.session.add_all([
            WidgetMessage(widget_conversation_id=wc.id, role="user", content="What time is church?"),
            WidgetMessage(widget_conversation_id=wc.id, role="assistant",
                          content="Modern worship is at 9:30 AM and Traditional is at 11:00 AM."),
        ])
        db.session.commit()

    print(f"Seeded. Sign in as dev-admin, dev-comms, dev-assistant, dev-family, dev-music, "
          f"dev-pastor or dev-norole at @{ORG_DOMAIN}.")
    print("The password is SEED_PASSWORD, or the default in scripts/seed_dev.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
