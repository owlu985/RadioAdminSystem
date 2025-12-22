from dataclasses import dataclass
from datetime import datetime

@dataclass
class LogEntry:
    time: str
    entry_type: str  # music | psa | event
    title: str = ""
    artist: str = ""
    description: str = ""

@dataclass
class ShowLog:
    dj_first: str
    dj_last: str
    show_name: str
    start_time: datetime
    end_time: datetime
    entries: list[LogEntry]
