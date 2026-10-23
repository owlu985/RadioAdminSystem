"""Evidence-based airing totals. File modification times are never airing dates."""
from datetime import date
import os
import re

from app.services.legacy_recordings import split_dj_names
from app.services.log_export import read_recording_metadata


def key(value):
    return " ".join((value or "").casefold().split())


def recording_family(path):
    return re.sub(r"_part\d+$", "", os.path.splitext(path)[0], flags=re.I)


def evidence_identity(path, metadata=None, fallback_show=""):
    metadata = read_recording_metadata(path) if metadata is None else metadata
    show = metadata.get("show_name") or fallback_show
    for field in ("airing_date", "recorded_date"):
        try:
            return key(show), date.fromisoformat(str(metadata[field])[:10])
        except (KeyError, ValueError, TypeError):
            pass
    match = re.search(r"(\d{2})[-_](\d{2})[-_](\d{2,4})_RAWDATA", os.path.basename(path), re.I)
    if match:
        month, day, year = map(int, match.groups())
        try:
            return key(show), date(year + 2000 if year < 100 else year, month, day)
        except ValueError:
            pass
    for field in ("show_date", "show_start"):
        try:
            return key(show), date.fromisoformat(str(metadata[field])[:10])
        except (KeyError, ValueError, TypeError):
            pass
    return None


def metadata_hosts(metadata, fallback=""):
    hosts = metadata.get("dj_names") or metadata.get("hosts") or metadata.get("dj") or fallback
    if isinstance(hosts, str):
        hosts = split_dj_names(hosts.replace(",", "&"))
    return {name.strip() for name in hosts if isinstance(name, str) and name.strip()}


def build_audit(root, recordings, week, **options):
    from app.services.airing_report import build_report
    return build_report(root, recordings, week, **options)
