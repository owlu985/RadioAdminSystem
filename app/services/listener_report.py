from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from app.services.listener_store import samples


def report_window(period, anchor, zone):
    if period == "day":
        start, end = anchor, anchor + timedelta(days=1)
    elif period == "month":
        start = anchor.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    elif period in {"year", "ytd"}:
        start = date(anchor.year, 1, 1)
        end = date(anchor.year + 1, 1, 1) if period == "year" else anchor + timedelta(days=1)
    else:
        raise ValueError("Unknown report period")
    return [int(datetime.combine(day, time.min, zone).timestamp()) for day in (start, end)]


def _row():
    return {"samples": 0, "total": 0, "peak": 0}


def _observe(row, count):
    row["samples"] += 1
    row["total"] += count
    row["peak"] = max(row["peak"], count)


def listener_report(period, anchor, zone, selected=""):
    start, end = report_window(period, anchor, zone)
    groups = {key: defaultdict(_row) for key in ("Trend", "Time of day", "Programming", "Mode", "Client", "Network")}
    summary = _row()
    programs = set()
    detail_count = first_seen = age_seconds = age_count = 0
    for item in samples(start, end, detailed=True):
        mode = item["state"]
        mode_label = {"live_show": "Scheduled show", "automation": "Automation",
                      "dead_air": "Dead air"}.get(mode, "Unknown")
        program = item["show"] or mode_label
        programs.add(program)
        if selected and selected != program:
            continue
        local = datetime.fromisoformat(item["ts"]).replace(tzinfo=timezone.utc).astimezone(zone)
        hour = local.strftime("%I %p").lstrip("0")
        trend = local.strftime({"day": "%I %p %Z", "month": "%b %d", "year": "%b %Y", "ytd": "%b %Y"}[period]).lstrip("0")
        count = item["listeners"]
        _observe(summary, count)
        for group, label in (("Trend", trend), ("Time of day", hour), ("Programming", program), ("Mode", mode_label)):
            _observe(groups[group][label], count)
        detail = item["detail"]
        if detail is None:
            continue
        detail_count += 1
        first_seen += detail["first_seen"]
        age_seconds += detail["age_seconds"]
        age_count += detail["age_count"]
        for group, key in (("Client", "clients"), ("Network", "networks")):
            for label, value in detail[key].items():
                _observe(groups[group][label], value)

    hours = [(datetime(2000, 1, 1, h).strftime("%I %p").lstrip("0")) for h in range(24)]
    groups["Time of day"] = {h: groups["Time of day"][h] for h in hours if h in groups["Time of day"]}
    for group, rows in groups.items():
        for row in rows.values():
            if group in {"Client", "Network"}:
                row["samples"] = detail_count
            row["average"] = round(row["total"] / row["samples"], 2)
    return {"groups": groups, "programs": sorted(programs), "samples": summary["samples"],
            "average": round(summary["total"] / summary["samples"], 2) if summary["samples"] else None,
            "peak": summary["peak"] if summary["samples"] else None, "detailed": detail_count,
            "first_seen": first_seen if detail_count else None,
            "mean_age": round(age_seconds / age_count / 60, 1) if age_count else None}
