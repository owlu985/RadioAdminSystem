import json
import wave

import app.services.legacy_recordings as legacy_recordings
from app.main_routes import _extract_recording_names
from app.services.legacy_recordings import (
    discover_legacy_recordings,
    ignore_legacy_recording,
    legacy_ignore_path,
    parse_legacy_filename,
    split_dj_names,
    write_legacy_sidecar,
)


def test_recognizes_common_legacy_filename_patterns(tmp_path):
    underscore = tmp_path / "FIRST_LAST_09_04_2015_RAWDATA.mp3"
    dashed = tmp_path / "first-last 9-4-09.mp3"

    first = parse_legacy_filename(str(underscore), str(tmp_path))
    second = parse_legacy_filename(str(dashed), str(tmp_path))

    assert (first.dj_names, first.recorded_date, first.recognized) == ("FIRST LAST", "2015-09-04", True)
    assert (second.dj_names, second.recorded_date, second.recognized) == ("first last", "2009-09-04", True)


def test_unrecognized_name_uses_parent_for_dj_but_leaves_unknown_fields_blank(tmp_path):
    folder = tmp_path / "Jane_Doe"
    folder.mkdir()
    recording = folder / "mystery tape.mp3"

    result = parse_legacy_filename(str(recording), str(tmp_path))

    assert result.dj_names == "Jane Doe"
    assert result.show_name == ""
    assert result.recorded_date == ""
    assert result.recognized is False


def test_sidecar_is_searchable_and_removes_recording_from_import_queue(tmp_path):
    recording = tmp_path / "Alex_Smith_01_02_2015_RAWDATA.mp3"
    recording.write_bytes(b"audio")
    assert len(discover_legacy_recordings(str(tmp_path))) == 1

    sidecar = write_legacy_sidecar(
        str(recording), period="Spring 2015", show_name="The Archive Hour",
        dj_names=split_dj_names("Alex Smith & Pat Jones"), recorded_date="2015-01-02",
    )

    payload = json.loads((tmp_path / "Alex_Smith_01_02_2015_RAWDATA.json").read_text())
    assert sidecar.endswith(".json")
    assert payload["show_name"] == "The Archive Hour"
    assert payload["dj_names"] == ["Alex Smith", "Pat Jones"]
    assert payload["compliance_applicable"] is False
    assert payload["listener_metrics_applicable"] is False
    assert discover_legacy_recordings(str(tmp_path)) == []


def test_sidecar_names_override_legacy_folder_and_filename_labels(tmp_path):
    recording = tmp_path / "Spring_2015" / "Alex_Smith" / "Old_Folder" / "Alex_01_02_15.mp3"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"audio")
    write_legacy_sidecar(
        str(recording), period="Spring 2015", show_name="The Archive Hour",
        dj_names=["Alex Smith", "Pat Jones"], recorded_date="2015-01-02",
    )

    names = _extract_recording_names(
        full=str(recording), base_root=str(tmp_path), period_folders={"Spring_2015"},
    )

    assert names == ("The Archive Hour", "Alex Smith & Pat Jones")


def test_ignored_non_show_file_is_removed_from_future_scans(tmp_path):
    recording = tmp_path / "class_project.mp3"
    recording.write_bytes(b"not actually an mp3")
    assert len(discover_legacy_recordings(str(tmp_path))) == 1

    marker = ignore_legacy_recording(str(recording))

    assert marker == legacy_ignore_path(str(recording))
    assert json.loads((tmp_path / "class_project.mp3.rams-ignore").read_text())["source_filename"] == recording.name
    assert recording.exists()
    assert discover_legacy_recordings(str(tmp_path)) == []


def test_scan_reports_audio_duration_and_modified_date(tmp_path):
    recording = tmp_path / "Alex_Smith_01_02_2015.wav"
    with wave.open(str(recording), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"\0\0" * 16_000)

    result = discover_legacy_recordings(str(tmp_path))[0]

    assert result.duration_seconds == 2
    assert result.duration_label == "0:02"
    assert result.modified_at.year >= 2015


def test_fast_scan_defers_audio_header_reads(tmp_path, monkeypatch):
    recording = tmp_path / "Alex_Smith_01_02_2015.mp3"
    recording.write_bytes(b"audio")
    monkeypatch.setattr(legacy_recordings, "_audio_duration", lambda path: (_ for _ in ()).throw(AssertionError("slow probe")))

    result = discover_legacy_recordings(str(tmp_path), include_duration=False)[0]

    assert result.duration_seconds is None
