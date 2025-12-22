import csv
import os
from collections import defaultdict
from datetime import datetime
from .artist_utils import normalize_artist
from .reader import scan_logs

def generate_artist_report(log_dir, start_date=None, end_date=None):
    report = defaultdict(lambda: {
        "artist": "",
        "plays": 0,
        "dates": set()
    })

    logs = scan_logs(log_dir)

    for log in logs:
        log_date = log["start_time"].date()

        if start_date and log_date < start_date:
            continue
        if end_date and log_date > end_date:
            continue

        with open(log["path"], newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Type", "").lower() != "music":
                    continue

                artist_raw = row.get("Artist", "")
                artist = normalize_artist(artist_raw)
                if not artist:
                    continue

                entry = report[artist]
                entry["artist"] = artist.title()
                entry["plays"] += 1
                entry["dates"].add(log_date)

    # Convert dates to counts
    for artist in report.values():
        artist["days_played"] = len(artist["dates"])
        del artist["dates"]

    return list(report.values())
