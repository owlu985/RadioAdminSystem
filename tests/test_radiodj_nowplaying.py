from types import SimpleNamespace

from flask import Flask, jsonify

from app.routes import api


def test_same_song_compares_normalized_artist_and_title():
    entry = SimpleNamespace(artist="The Artist", title="The Song")

    assert api._same_song({"artist": " the artist ", "title": "THE SONG"}, entry)
    assert not api._same_song({"artist": "The Artist", "title": "Another Song"}, entry)


def test_non_music_payload_keeps_cached_song(monkeypatch):
    cached = {"artist": "Artist", "title": "Song", "is_music": True}
    api._RADIODJ_NOWPLAYING_CACHE.update({
        "fetched_at": None,
        "payload": cached,
        "error_until": None,
        "last_logged_track_id": None,
        "last_pushed_track_id": None,
    })

    class FakeRadioDJClient:
        enabled = True

        def now_playing(self):
            return {
                "Artist": "Station Sponsor",
                "Title": "Underwriting Announcement",
                "TrackType": "Commercial",
            }

    monkeypatch.setattr(api, "RadioDJClient", FakeRadioDJClient)

    app = Flask(__name__)
    with app.app_context():
        assert api._get_cached_radiodj_nowplaying() == cached
    assert api._RADIODJ_NOWPLAYING_CACHE["payload"] == cached


def test_widget_uses_now_playing_feed_to_update_history(monkeypatch):
    app = Flask(__name__)
    observed = {}
    track = {"artist": "Artist", "title": "Song", "is_music": True}

    monkeypatch.setattr(api, "now_playing", lambda: jsonify({"status": "off_air"}))

    def fake_cached_nowplaying(**kwargs):
        observed.update(kwargs)
        return track

    monkeypatch.setattr(api, "_get_cached_radiodj_nowplaying", fake_cached_nowplaying)
    monkeypatch.setattr(api, "_cover_url_for_track", lambda *_args: None)

    with app.test_request_context():
        response = api.now_widget()

    assert observed == {"write_log": True}
    assert response.get_json()["track"] == {**track, "cover_url": None}
