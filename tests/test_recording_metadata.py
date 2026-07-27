from app.services.log_export import (
    read_recording_metadata,
    recording_metadata_path,
    write_recording_metadata,
)


def test_recording_metadata_uses_matching_json_sidecar(tmp_path):
    recording = tmp_path / "Morning_Show_07-27-26_RAWDATA.mp3"
    payload = {
        "schema_version": 1,
        "show_name": "Morning Show",
        "show_start": "2026-07-27T10:00:00",
        "show_end": "2026-07-27T12:00:00",
    }

    path = write_recording_metadata(str(recording), payload)

    assert path == str(tmp_path / "Morning_Show_07-27-26_RAWDATA.json")
    assert recording_metadata_path(str(recording)) == path
    assert read_recording_metadata(str(recording)) == payload


def test_invalid_recording_metadata_is_treated_as_missing(tmp_path):
    recording = tmp_path / "show.mp3"
    (tmp_path / "show.json").write_text("not json", encoding="utf-8")

    assert read_recording_metadata(str(recording)) == {}
