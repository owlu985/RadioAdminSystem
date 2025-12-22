from dataclasses import dataclass
from pydub import AudioSegment
import numpy as np
import json
import os

@dataclass
class SilenceAnalysis:
    avg_db: float
    silence_ratio: float
    automation_ratio: float
    classification: str
    reason: str


def analyze_audio(file_path: str, config: dict) -> SilenceAnalysis:
    """
    Analyze audio levels to detect dead air, automation, or live DJ.
    Never raises exceptions.
    """

    try:
        audio = AudioSegment.from_file(file_path)
        samples = np.array(audio.get_array_of_samples())

        if samples.size == 0:
            return SilenceAnalysis(0, 1.0, 0, "dead_air", "empty_audio")

        # Convert to dBFS
        rms = np.sqrt(np.mean(samples.astype(float) ** 2))
        avg_db = 20 * np.log10(rms) if rms > 0 else -100

        # Windowed analysis
        chunk_ms = 500
        chunks = audio[::chunk_ms]

        silence_chunks = 0
        automation_chunks = 0

        for chunk in chunks:
            if chunk.dBFS <= config["silence_detection"]["dead_air_db"]:
                silence_chunks += 1
            elif (
                config["silence_detection"]["automation_min_db"]
                <= chunk.dBFS
                <= config["silence_detection"]["automation_max_db"]
            ):
                automation_chunks += 1

        total_chunks = max(len(chunks), 1)

        silence_ratio = silence_chunks / total_chunks
        automation_ratio = automation_chunks / total_chunks

        # Classification logic
        if silence_ratio > 0.6:
            classification = "dead_air"
            reason = "majority_silence"
        elif automation_ratio >= config["show_detection"]["automation_ratio_threshold"]:
            classification = "automation"
            reason = "consistent_compression"
        else:
            classification = "live_show"
            reason = "dynamic_levels"

        return SilenceAnalysis(
            avg_db=round(avg_db, 2),
            silence_ratio=round(silence_ratio, 3),
            automation_ratio=round(automation_ratio, 3),
            classification=classification,
            reason=reason
        )

    except Exception as e:
        return SilenceAnalysis(
            avg_db=0,
            silence_ratio=0,
            automation_ratio=0,
            classification="unknown",
            reason=str(e)
        )
