from unittest.mock import Mock, call, patch

from flask import Flask

from app.services.radiodj_client import RadioDJClient


def _client(tmp_path):
    app = Flask(__name__)
    app.config.update(
        RADIODJ_API_BASE_URL="http://radio.test:8080/",
        RADIODJ_API_PASSWORD="secret",
        RADIODJ_IMPORT_FOLDER=str(tmp_path / "imports"),
        NAS_ROOT=str(tmp_path / "nas"),
    )
    return app, app.app_context()


def _response(*, text="", json_payload=None):
    response = Mock(text=text)
    response.json.return_value = json_payload
    return response


def test_v14_command_status_and_playlist_endpoints(tmp_path):
    app, context = _client(tmp_path)
    with context, patch("app.services.radiodj_client.requests.get") as get:
        get.side_effect = [
            _response(text="OK"),
            _response(text="<State><AutoDJ>True</AutoDJ></State>"),
            _response(text="<Playlist><Item><Title>One</Title></Item></Playlist>"),
            _response(text="<Item><Title>One</Title></Item>"),
        ]
        client = RadioDJClient()

        assert client.set_item("LoadTrackToTop", "42") == "OK"
        assert client.status() == {"autodj": "True"}
        assert client.playlist() == [{"title": "One"}]
        assert client.playlist_item(0) == {"title": "One"}

    assert get.call_args_list == [
        call("http://radio.test:8080/RDJCommand", params={"auth": "secret", "command": "LoadTrackToTop", "arg": "42"}, timeout=10),
        call("http://radio.test:8080/RDJState", params={"auth": "secret"}, timeout=6),
        call("http://radio.test:8080/RDJp", params={"auth": "secret"}, timeout=10),
        call("http://radio.test:8080/RDJp", params={"auth": "secret", "arg": 0}, timeout=10),
    ]


def test_now_playing_uses_xml_endpoint_first(tmp_path):
    app, context = _client(tmp_path)
    response = _response(
        text="<NowPlaying><Artist>Artist (P)</Artist><Title>Song</Title><Duration>123.5</Duration></NowPlaying>"
    )
    with context, patch("app.services.radiodj_client.requests.get", return_value=response) as get:
        assert RadioDJClient().now_playing() == {
            "artist": "Artist", "title": "Song", "duration": 123.5
        }

    get.assert_called_once_with(
        "http://radio.test:8080/RDJnp", params={"auth": "secret"}, timeout=6
    )


def test_now_playing_falls_back_to_json_current_track(tmp_path):
    app, context = _client(tmp_path)
    invalid_xml = _response(text="not xml")
    json_response = _response(json_payload={
        "CurrentTrack": {"Artist": "Artist (P)", "Title": "Song", "Duration": "123.5"},
        "Playlist": [{"Title": "Next"}],
    })
    with context, patch("app.services.radiodj_client.requests.get", side_effect=[invalid_xml, json_response]) as get:
        assert RadioDJClient().now_playing() == {
            "Artist": "Artist", "Title": "Song", "Duration": 123.5
        }

    assert get.call_args_list[1] == call(
        "http://radio.test:8080/RDJnpjson", params={"auth": "secret"}, timeout=6
    )
