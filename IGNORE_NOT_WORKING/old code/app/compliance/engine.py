from recorder.silence import analyze_audio

def evaluate_show(log, recording, config):
    flags = []

    if not recording:
        flags.append({
            "flag": "no_recording",
            "confidence": "high",
            "reason": "no_matching_audio_file"
        })
        return flags

    analysis = analyze_audio(recording["path"], config)

    if analysis.classification == "automation":
        flags.append({
            "flag": "possible_missed_show",
            "confidence": "high",
            "reason": f"automation_ratio={analysis.automation_ratio}"
        })

    if analysis.classification == "dead_air":
        flags.append({
            "flag": "dead_air_detected",
            "confidence": "high",
            "reason": f"silence_ratio={analysis.silence_ratio}"
        })

    return flags
