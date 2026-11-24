"""Indexed, station-local storage for sampled Icecast connections."""
import json
import sqlite3
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app


def _paths():
    legacy = current_app.config.get("ICECAST_ANALYTICS_FILE")
    legacy = Path(legacy) if legacy else Path(current_app.instance_path, "analytics", "icecast_listener_history.jsonl")
    return legacy, legacy.with_name(legacy.name + ".sqlite3")


def _import_history(db, path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as source:
        for line in source:
            try:
                row = json.loads(line)
                stamp = datetime.fromisoformat(row["ts"]).replace(tzinfo=timezone.utc)
                count = int(row["listeners"])
            except (ValueError, KeyError, TypeError):
                continue
            db.execute("INSERT OR IGNORE INTO sample VALUES (?,?,?,?,?,NULL)",
                       (int(stamp.timestamp()), count, row.get("state"), row.get("show"), row.get("classification")))


@contextmanager
def connection():
    legacy, path = _paths()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                    db.execute("""CREATE TABLE sample (
                        stamp INTEGER PRIMARY KEY, listeners INTEGER NOT NULL, state TEXT,
                        show_name TEXT, classification TEXT, detail TEXT)""")
                    db.execute("CREATE TABLE active (id TEXT PRIMARY KEY, started INTEGER)")
                    _import_history(db, legacy)
                    db.execute("PRAGMA user_version=1")
        yield db


def save_sample(count, context, clients=None, now=None):
    now = now or datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    minute = epoch - epoch % 60
    detail = None
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("SELECT MAX(stamp) FROM sample").fetchone()[0]
        if latest is not None and latest >= minute:
            return
        if clients is not None:
            previous = dict(db.execute("SELECT id, started FROM active"))
            active = {}
            new = 0
            ages = []
            for client in clients:
                age = client["seconds"]
                if age is not None:
                    ages.append(age)
                key = client["id"]
                if key:
                    started = epoch - age if age is not None else None
                    old = previous.get(key)
                    if key not in previous or (started is not None and old is not None and abs(started - old) > 5):
                        new += 1
                    active[key] = started
            db.execute("DELETE FROM active")
            db.executemany("INSERT INTO active VALUES (?,?)", active.items())
            detail = json.dumps({
                "clients": Counter(c["client"] for c in clients),
                "networks": Counter(c["network"] for c in clients),
                "first_seen": new, "age_seconds": sum(ages), "age_count": len(ages),
            }, separators=(",", ":"))
        db.execute("INSERT INTO sample VALUES (?,?,?,?,?,?)",
                   (minute, count, context["state"], context["show_name"], context["classification"], detail))
        days = int(current_app.config.get("ICECAST_ANALYTICS_RETENTION_DAYS", 365))
        if days > 0:
            db.execute("DELETE FROM sample WHERE stamp < ?", (minute - 86400 * days,))


def samples(start=None, end=None, *, detailed=False):
    columns = "stamp,listeners,state,show_name,classification" + (",detail" if detailed else "")
    with connection() as db:
        for row in db.execute(f"SELECT {columns} FROM sample WHERE stamp >= ? AND stamp < ? ORDER BY stamp",
                              (start if start is not None else 0, end if end is not None else 2**62)):
            stamp, count, state, show, classification = row[:5]
            item = {"ts": datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None).isoformat(),
                    "listeners": count, "state": state, "show": show, "classification": classification}
            if detailed:
                item["detail"] = json.loads(row[5]) if row[5] else None
            yield item
