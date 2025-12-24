from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional
import time

import ffmpeg
import numpy as np
from pydub import AudioSegment
from flask import current_app
from datetime import datetime

from app.logger import init_logger
from app.models import StreamProbe, db, LogEntry
from app.services.show_run_service import get_or_create_active_run
from app.services.alerts import check_stream_up, process_probe_alerts
from app.services.health import record_failure
from app.utils import get_current_show

logger = init_logger()


@dataclass
class DetectionResult:
    avg_db: float
    silence_ratio: float
    automation_ratio: float
    classification: str
    reason: str


def analyze_audio(file_path: str, config: dict) -> DetectionResult:
    """
    Analyze audio levels to detect dead air, automation, or live DJ.
    Never raises exceptions; falls back to defaults on errors.
    """

    try:
        audio = AudioSegment.from_file(file_path)
        samples = np.array(audio.get_array_of_samples())

        if samples.size == 0:
            return DetectionResult(0, 1.0, 0, "dead_air", "empty_audio")

        rms = np.sqrt(np.mean(samples.astype(float) ** 2))
        avg_db = 20 * np.log10(rms) if rms > 0 else -100

        chunk_ms = config.get("SILENCE_CHUNK_MS", 500)
        chunks = audio[::chunk_ms]

        silence_chunks = 0
        automation_chunks = 0

        for chunk in chunks:
            if chunk.dBFS <= config["DEAD_AIR_DB"]:
                silence_chunks += 1
            elif config["AUTOMATION_MIN_DB"] <= chunk.dBFS <= config["AUTOMATION_MAX_DB"]:
                automation_chunks += 1

        total_chunks = max(len(chunks), 1)

        silence_ratio = silence_chunks / total_chunks
        automation_ratio = automation_chunks / total_chunks

        if silence_ratio > 0.6:
            classification = "dead_air"
            reason = "majority_silence"
        elif automation_ratio >= config["AUTOMATION_RATIO_THRESHOLD"]:
            classification = "automation"
            reason = "consistent_compression"
        else:
            classification = "live_show"
            reason = "dynamic_levels"

        return DetectionResult(
            avg_db=round(avg_db, 2),
            silence_ratio=round(silence_ratio, 3),
            automation_ratio=round(automation_ratio, 3),
            classification=classification,
            reason=reason,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error(f"Error analyzing audio: {exc}")
        return DetectionResult(0, 0, 0, "unknown", str(exc))


def _sample_path() -> Optional[Path]:
    cfg = current_app.config
    sample = cfg.get("TEST_SAMPLE_AUDIO")
    if cfg.get("TEST_MODE") and sample and Path(sample).exists():
        return Path(sample)
    return None


def probe_stream(stream_url: str) -> Optional[DetectionResult]:
    """
    Record a short sample from the stream and analyze it.
    Returns None if the probe fails.
    """
    config = current_app.config
    duration = int(config.get("STREAM_PROBE_SECONDS", 8))

    sample = _sample_path()
    if sample:
        logger.info("Using test sample audio for probe: %s", sample)
        return analyze_audio(str(sample), config)

    with NamedTemporaryFile(suffix=".mp3", delete=True) as tmp:
        try:
            stream_input = ffmpeg.input(
                stream_url,
                t=duration,
                reconnect=1,
                reconnect_streamed=1,
                reconnect_delay_max=2,
            )

            _, stderr = (
                stream_input
                .output(tmp.name, acodec="copy")
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )

            if stderr:
                detail = stderr.decode(errors="ignore").strip()
                if detail:
                    logger.debug("FFmpeg probe stderr: %s", detail)

        except ffmpeg.Error as exc:  # type: ignore[attr-defined]
            detail = ""
            try:
                detail = exc.stderr.decode(errors="ignore") if exc.stderr else ""
            except Exception:  # noqa: BLE001
                detail = str(exc)
            logger.error("FFmpeg probe error: %s", (detail or str(exc)).strip())
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error("FFmpeg probe unexpected error: %s", exc)
            return None

        return analyze_audio(tmp.name, config)


def probe_and_record():
    """
    Probe the configured stream, attach the result to the active show run
    (if any), and store it for API access.
    """
    stream_url = current_app.config["STREAM_URL"]
    stream_up = check_stream_up(stream_url)

    def _attempt_probe() -> Optional[DetectionResult]:
        return probe_stream(stream_url) if stream_up else DetectionResult(0, 1.0, 0, "stream_down", "unreachable")

    result = _attempt_probe()

    if result is None and current_app.config.get("SELF_HEAL_ENABLED", True):
        record_failure("stream_probe", reason="probe_failed", restarted=True)
        time.sleep(1)
        result = _attempt_probe()

    if result is None:
        record_failure("stream_probe", reason="probe_failed_final", restarted=False)
        process_probe_alerts(stream_up, None)
        return

    show = get_current_show()
    show_run = None
    if show:
        show_run = get_or_create_active_run(
            show_name=show.show_name or f"{show.host_first_name} {show.host_last_name}",
            dj_first_name=show.host_first_name,
            dj_last_name=show.host_last_name,
        )

    probe = StreamProbe(
        show_run_id=show_run.id if show_run else None,
        classification=result.classification,
        reason=result.reason,
        avg_db=result.avg_db,
        silence_ratio=result.silence_ratio,
        automation_ratio=result.automation_ratio,
        created_at=datetime.utcnow(),
    )
    db.session.add(probe)

    if show_run:
        show_run.classification = result.classification
        show_run.classification_reason = result.reason
        show_run.avg_db = result.avg_db
        show_run.silence_ratio = result.silence_ratio
        show_run.automation_ratio = result.automation_ratio
        if result.classification in {"automation", "dead_air"}:
            show_run.flagged_missed = True

        db.session.add(LogEntry(
            show_run_id=show_run.id,
            timestamp=datetime.utcnow(),
            message=f"Probe: {result.classification}",
            entry_type="probe",
            description=f"reason={result.reason}, avg_db={result.avg_db}, silence={result.silence_ratio}, automation={result.automation_ratio}"
        ))

    db.session.commit()

    process_probe_alerts(stream_up, result)

    logger.info(
        "Stream probe: %s (avg_db=%.2f, silence=%.2f, automation=%.2f)",
        result.classification,
        result.avg_db,
        result.silence_ratio,
        result.automation_ratio,
    )
