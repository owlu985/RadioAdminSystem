from datetime import timedelta

def link_log_to_recording(log, recordings):
    for rec in recordings:
        delta = abs(log["start_time"] - rec["start_time"])
        if delta <= timedelta(minutes=10):
            return rec
    return None
