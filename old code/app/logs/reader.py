import os
import re
from datetime import datetime

LOG_PATTERN = re.compile(
    r"(?P<first>[A-Z]+)_(?P<last>[A-Z]+)_"
    r"(?P<month>\d{2})_(?P<day>\d{2})_(?P<year>\d{4})_"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_LOG\.csv"
)

def scan_logs(log_dir):
    logs = []

    for fname in os.listdir(log_dir):
        match = LOG_PATTERN.match(fname)
        if not match:
            continue

        data = match.groupdict()
        start_time = datetime(
            int(data["year"]),
            int(data["month"]),
            int(data["day"]),
            int(data["hour"]),
            int(data["minute"]),
        )

        logs.append({
            "filename": fname,
            "dj_first": data["first"].title(),
            "dj_last": data["last"].title(),
            "start_time": start_time,
            "path": os.path.join(log_dir, fname)
        })

    return logs
