"""
tests/test_notifications.py — Mixtape

Regression tests for notification creation (Issue #4).

Before the fix, rate_song() saved the Rating but never called
create_notification(), so rating a friend's shared song produced no
notification at all, unlike add_to_playlist() which does notify.
"""

import pytest
from app import create_app, db
from models import User, Song, Playlist
from services.notification_service import rate_song, add_to_playlist, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_rater(app):
    """aaliya shares a song; kenji is a separate user who will rate it."""
    with app.app_context():
        aaliya = User(username="aaliya", email="aaliya@example.com")
        kenji = User(username="kenji", email="kenji@example.com")
        db.session.add_all([aaliya, kenji])
        db.session.flush()

        song = Song(title="Neon City", artist="Static Era", shared_by=aaliya.id)
        db.session.add(song)
        db.session.commit()

        yield {"aaliya": aaliya, "kenji": kenji, "song": song}


def test_rating_a_friends_song_creates_a_notification(app, sharer_and_rater):
    """
    Rating a song someone else shared should notify the sharer, the same
    way adding that song to a playlist does.
    """
    with app.app_context():
        aaliya = sharer_and_rater["aaliya"]
        kenji = sharer_and_rater["kenji"]
        song = sharer_and_rater["song"]

        rate_song(user_id=kenji.id, song_id=song.id, score=5)

        notifs = get_notifications(aaliya.id)
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_rated"
        assert "kenji" in notifs[0]["body"]


def test_rating_your_own_song_does_not_notify_yourself(app, sharer_and_rater):
    """Rating your own shared song should not generate a self-notification."""
    with app.app_context():
        aaliya = sharer_and_rater["aaliya"]
        song = sharer_and_rater["song"]

        rate_song(user_id=aaliya.id, song_id=song.id, score=4)

        assert get_notifications(aaliya.id) == []


def test_playlist_add_notification_still_works(app, sharer_and_rater):
    """
    Sanity check that the pre-existing, working notification path
    (adding a shared song to a playlist) is unaffected by the rate_song fix.
    """
    with app.app_context():
        aaliya = sharer_and_rater["aaliya"]
        kenji = sharer_and_rater["kenji"]
        song = sharer_and_rater["song"]

        playlist = Playlist(name="Friday Energy", created_by=kenji.id)
        db.session.add(playlist)
        db.session.commit()

        add_to_playlist(playlist.id, song.id, kenji.id)

        notifs = get_notifications(aaliya.id)
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_added_to_playlist"
