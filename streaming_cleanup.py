"""One-off tidy of streaming numbers that sync created before service labels were strict.

Before 14 Oct 2026 any live stream with an unrecognised title became a "Sunday service" number, so
sermon clips streamed on Sunday afternoon added rows with 0 to 3 views. This removes only rows that
(1) were written entirely by a sync (every field's source is "api", every edit by a sync), and
(2) have no stream on that day that the current title rules call a service.
Anything a person typed, imported or corrected is never touched.
"""
import streaming
from models import StreamingNumber, StreamingNumberEdit, YoutubeVideo, SocialPost, db

SYNC_AUTHORS = ("YouTube sync", "Facebook sync")


def find_stray_rows() -> list:
    from connectors.youtube import YouTubeConnector
    stray = []
    for n in StreamingNumber.query.filter(StreamingNumber.platform.in_(("youtube", "facebook")),
                                          StreamingNumber.service_label == streaming.DEFAULT_LABEL):
        sources = set((n.sources or {}).values()) if isinstance(n.sources, dict) else set()
        if sources and sources != {"api"}:
            continue
        edits = StreamingNumberEdit.query.filter_by(number_id=n.id).all()
        if any(e.source != "api" or (e.by or "") not in SYNC_AUTHORS for e in edits):
            continue
        titles = []
        if n.platform == "youtube":
            for v in YoutubeVideo.query.filter(YoutubeVideo.actual_start.isnot(None)):
                if YouTubeConnector.local_date(v.actual_start) == n.service_date:
                    titles.append(v.title)
        else:
            for p in SocialPost.query.filter_by(platform="facebook", kind="live"):
                if p.created_at and YouTubeConnector.local_date(p.created_at) == n.service_date:
                    titles.append(p.caption)
        if any(YouTubeConnector.service_label(t) == streaming.DEFAULT_LABEL for t in titles):
            continue
        stray.append(n)
    return stray


def remove_stray_rows(dry_run: bool = True) -> list:
    rows = find_stray_rows()
    summary = [(n.service_date.isoformat(), n.platform, n.service_label, n.total_views) for n in rows]
    if not dry_run:
        for n in rows:
            StreamingNumberEdit.query.filter_by(number_id=n.id).delete()
            db.session.delete(n)
        db.session.commit()
    return summary


if __name__ == "__main__":
    import sys
    from app import create_app
    with create_app().app_context():
        done = "--do-it" in sys.argv
        for line in remove_stray_rows(dry_run=not done):
            print(("removed" if done else "would remove"), line)
