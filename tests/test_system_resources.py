from app.services.system_resources import _read_linux_memory, get_memory_status


def test_read_linux_memory_uses_available_bytes(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n", encoding="utf-8")
    assert _read_linux_memory(meminfo) == (1_024_000, 256_000)


def test_memory_status_marks_high_usage(monkeypatch):
    monkeypatch.setattr("app.services.system_resources._read_linux_memory", lambda: (1000, 100))
    status = get_memory_status()
    assert status["percent"] == 90.0
    assert status["level"] == "danger"
    assert status["label"] == "High"
