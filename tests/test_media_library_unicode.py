from flask import Flask
from app.services.library import media_library


def test_media_token_round_trip_preserves_surrogate_escaped_filename():
    path = "/srv/psa/Station \udcff ID.mp3"

    token = media_library.encode_media_token(path)

    assert media_library.decode_media_token(token) == path
    assert media_library.decode_media_token(token).encode("utf-8", "surrogateescape") == (
        b"/srv/psa/Station \xff ID.mp3"
    )


def test_media_token_round_trip_handles_non_filesystem_lone_surrogate():
    path = "/srv/psa/Bad \ud800 index entry.mp3"

    assert media_library.decode_media_token(media_library.encode_media_token(path)) == path


def test_list_media_replaces_invalid_display_surrogates_without_changing_spacing(monkeypatch):
    app = Flask(__name__)
    path = "/srv/psa/Station \udcff ID.mp3"
    monkeypatch.setattr(
        media_library,
        "get_media_index",
        lambda: {
            "files": {
                path: {
                    "path": path,
                    "name": "Station \udcff ID.mp3",
                    "category": "PSA/Station IDs",
                    "kind": "psa",
                }
            }
        },
    )
    monkeypatch.setattr(media_library, "load_media_meta", lambda unused_path: {})
    monkeypatch.setattr(media_library, "load_cue", lambda unused_path: None)
    monkeypatch.setattr(media_library, "get_asset_metadata", lambda unused_path, unused_kind: {})
    app.add_url_rule("/media/file/<path:token>", "main.media_file", lambda token: token)

    with app.test_request_context():
        payload = media_library.list_media(kind="psa", per_page=100)

    item = payload["items"][0]
    assert item["name"] == "Station ? ID.mp3"
    assert item["category"] == "PSA/Station IDs"
    assert media_library.decode_media_token(item["token"]) == path
