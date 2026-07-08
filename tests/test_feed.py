"""
tests/test_feed.py — Mixtape

Regression tests for the "Friends Listening Now" feed (Issue #2).

Before the fix, RECENT_THRESHOLD was a rolling 24-hour window, so a
friend's listening event from the previous evening was still shown as
"listening now" the next morning. These tests pin down the expected
behavior: only genuinely recent listens should appear.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def friends(app):
    """nova and darius are friends. darius shared/listened to a song."""
    with app.app_context():
        nova = User(username="nova", email="nova@example.com")
        darius = User(username="darius", email="darius@example.com")
        db.session.add_all([nova, darius])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=nova.id, friend_id=darius.id))
        db.session.execute(friendships.insert().values(user_id=darius.id, friend_id=nova.id))

        song = Song(title="Late Night Drive", artist="The Wanderers", shared_by=darius.id)
        db.session.add(song)
        db.session.commit()

        yield {"nova": nova, "darius": darius, "song": song}


def test_listening_now_excludes_late_previous_night_event(app, friends):
    """
    A friend's listen from ~10 hours ago (e.g. 11pm the night before, checked
    at 9am) should NOT appear in 'listening now'. This is the exact scenario
    nova reported: darius's 11pm listen was still showing up at 9am.
    """
    with app.app_context():
        nova = friends["nova"]
        darius = friends["darius"]
        song = friends["song"]

        event = ListeningEvent(
            user_id=darius.id,
            song_id=song.id,
            listened_at=datetime.now(timezone.utc) - timedelta(hours=10),
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(nova.id)
        friend_usernames = [entry["friend"]["username"] for entry in feed]
        assert "darius" not in friend_usernames


def test_listening_now_includes_genuinely_recent_event(app, friends):
    """A friend who listened a few minutes ago should still show up."""
    with app.app_context():
        nova = friends["nova"]
        darius = friends["darius"]
        song = friends["song"]

        event = ListeningEvent(
            user_id=darius.id,
            song_id=song.id,
            listened_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        db.session.add(event)
        db.session.commit()

        feed = get_friends_listening_now(nova.id)
        friend_usernames = [entry["friend"]["username"] for entry in feed]
        assert "darius" in friend_usernames


def test_listening_now_empty_for_user_with_no_friends(app):
    """A user with no friends gets an empty feed, not an error."""
    with app.app_context():
        loner = User(username="loner", email="loner@example.com")
        db.session.add(loner)
        db.session.commit()

        assert get_friends_listening_now(loner.id) == []
