import os
import time
import json
import threading
import uuid
import requests
import re
from urllib.parse import quote
from typing import List, Dict
from datetime import datetime
from flask import current_app
from app.services.detection import analyze_audio
from app.services.music_search import _walk_music, _read_tags
from app.models import AuditRun, db

audit_jobs: Dict[str, Dict] = {}
FCC_WORDS = ["fuck", "shit", "piss", "cunt", "cock", "tit", "dick"]


def _min_rate_limit(rate_limit_s: float) -> float:
    return max(rate_limit_s, 3.1)


def _lyrics_check(title: str, artist: str, rate_limit_s: float) -> dict:
    rate_limit_s = _min_rate_limit(rate_limit_s)
    url = f"https://api.lyrics.ovh/v1/{quote(artist)}/{quote(title)}"
    try:
        resp = requests.get(url, timeout=10)
        time.sleep(rate_limit_s)
        if resp.status_code != 200:
            return {"flagged": False, "matches": [], "error": f"lyrics_status_{resp.status_code}"}
        payload = resp.json()
        lyrics = (payload.get("lyrics") or "").lower()
        if not lyrics:
            return {"flagged": False, "matches": [], "error": "no_lyrics"}
        matches = []
        for word in FCC_WORDS:
            if re.search(rf"\\b{re.escape(word)}\\b", lyrics):
                matches.append(word)
        return {"flagged": bool(matches), "matches": matches, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"flagged": False, "matches": [], "error": str(exc)}


def audit_recordings(folder: str | None = None) -> List[Dict]:
    """
    Analyze recordings using the existing silence/automation classifier.
    """
    folder = folder or current_app.config["OUTPUT_FOLDER"]
    results = []
    for base, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith((".mp3", ".wav", ".flac", ".aac", ".m4a")):
                path = os.path.join(base, f)
                analysis = analyze_audio(path, current_app.config)
                results.append({
                    "path": path,
                    "classification": analysis.classification,
                    "reason": analysis.reason,
                    "avg_db": analysis.avg_db,
                    "silence_ratio": analysis.silence_ratio,
                    "automation_ratio": analysis.automation_ratio,
                })
    return results


def _itunes_search(title: str, artist: str, rate_limit_s: float = 0.5):
    rate_limit_s = _min_rate_limit(rate_limit_s)
    params = {
        "term": f"{title} {artist}".strip(),
        "limit": 5,
        "entity": "song"
    }
    resp = requests.get("https://itunes.apple.com/search", params=params, timeout=10)
    time.sleep(rate_limit_s)
    resp.raise_for_status()
    return resp.json().get("results", [])


def _explicit_from_itunes(api_results: list[dict]) -> dict:
    explicit_found = False
    clean_available = False
    for item in api_results:
        explicitness = item.get("trackExplicitness") or item.get("collectionExplicitness")
        if explicitness == "explicit":
            explicit_found = True
        if explicitness == "notExplicit":
            clean_available = True
    return {"explicit": explicit_found, "clean_available": clean_available}


def audit_explicit_music(
    rate_limit_s: float = 0.5,
    max_files: int | None = None,
    lyrics_check: bool = False,
) -> List[Dict]:
    """
    Check songs for explicit flag and presence of a clean version via iTunes API.
    Processes all files under NAS_MUSIC_ROOT.
    """
    results = []
    music_files = list(_walk_music())
    if max_files:
        music_files = music_files[:max_files]
    for path in music_files:
        tags = _read_tags(path)
        title = tags.get("title") or ""
        artist = tags.get("artist") or ""
        if not title or not artist:
            continue
        try:
            api_results = _itunes_search(title, artist, rate_limit_s=rate_limit_s)
        except Exception as exc:  # noqa: BLE001
            results.append({
                "path": path,
                "title": title,
                "artist": artist,
                "error": str(exc),
            })
            continue

        explicit_result = _explicit_from_itunes(api_results)
        lyrics_result = None
        if lyrics_check:
            lyrics_result = _lyrics_check(title, artist, rate_limit_s=rate_limit_s)
        results.append({
            "path": path,
            "title": title,
            "artist": artist,
            "explicit": explicit_result["explicit"],
            "clean_available": explicit_result["clean_available"],
            "lyrics_flagged": lyrics_result["flagged"] if lyrics_result else False,
            "lyrics_matches": lyrics_result["matches"] if lyrics_result else [],
            "lyrics_error": lyrics_result["error"] if lyrics_result else None,
        })
    return results


def _run_job(app, job_id: str, action: str, params: dict):
    audit_run = AuditRun(
        action=action,
        status="running",
        params_json=json.dumps(params, ensure_ascii=False),
    )
    try:
        with app.app_context():
            db.session.add(audit_run)
            db.session.commit()
            audit_jobs[job_id]["audit_run_id"] = audit_run.id
            audit_jobs[job_id]["status"] = "running"
            if action == "recordings":
                folder = params.get("folder")
                files = [
                    os.path.join(base, f)
                    for base, _, fs in os.walk(folder or app.config["OUTPUT_FOLDER"])
                    for f in fs
                    if f.lower().endswith((".mp3", ".wav", ".flac", ".aac", ".m4a"))
                ]
                audit_jobs[job_id]["total"] = len(files)
                results = []
                for idx, path in enumerate(files):
                    res = analyze_audio(path, app.config)
                    results.append({
                        "path": path,
                        "classification": res.classification,
                        "reason": res.reason,
                        "avg_db": res.avg_db,
                        "silence_ratio": res.silence_ratio,
                        "automation_ratio": res.automation_ratio,
                    })
                    audit_jobs[job_id]["progress"] = idx + 1
                audit_jobs[job_id]["results"] = results
            elif action == "explicit":
                rate = params.get("rate", 0.5)
                max_files = params.get("max_files")
                lyrics_check = params.get("lyrics_check", False)
                music_files = list(_walk_music())
                if max_files:
                    music_files = music_files[:max_files]
                audit_jobs[job_id]["total"] = len(music_files)
                results = []
                for idx, path in enumerate(music_files):
                    tags = _read_tags(path)
                    title = tags.get("title") or ""
                    artist = tags.get("artist") or ""
                    if not title or not artist:
                        audit_jobs[job_id]["progress"] = idx + 1
                        continue
                    try:
                        api_results = _itunes_search(title, artist, rate_limit_s=rate)
                    except Exception as exc:  # noqa: BLE001
                        results.append({
                            "path": path,
                            "title": title,
                            "artist": artist,
                            "error": str(exc),
                        })
                        audit_jobs[job_id]["progress"] = idx + 1
                        continue
                    explicit_result = _explicit_from_itunes(api_results)
                    lyrics_result = None
                    if lyrics_check:
                        lyrics_result = _lyrics_check(title, artist, rate_limit_s=rate)
                    results.append({
                        "path": path,
                        "title": title,
                        "artist": artist,
                        "explicit": explicit_result["explicit"],
                        "clean_available": explicit_result["clean_available"],
                        "lyrics_flagged": lyrics_result["flagged"] if lyrics_result else False,
                        "lyrics_matches": lyrics_result["matches"] if lyrics_result else [],
                        "lyrics_error": lyrics_result["error"] if lyrics_result else None,
                    })
                    audit_jobs[job_id]["progress"] = idx + 1
                audit_jobs[job_id]["results"] = results
            audit_jobs[job_id]["status"] = "completed"
            audit_run.status = "completed"
            audit_run.results_json = json.dumps(audit_jobs[job_id]["results"], ensure_ascii=False)
            audit_run.completed_at = datetime.utcnow()
            db.session.commit()
    except Exception as exc:  # noqa: BLE001
        audit_jobs[job_id]["status"] = "error"
        audit_jobs[job_id]["error"] = str(exc)
        if audit_run:
            with app.app_context():
                audit_run.status = "error"
                audit_run.results_json = json.dumps({"error": str(exc)}, ensure_ascii=False)
                audit_run.completed_at = datetime.utcnow()
                db.session.commit()


def start_audit_job(action: str, params: dict) -> str:
    job_id = str(uuid.uuid4())
    audit_jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "total": 0,
        "results": None,
        "audit_run_id": None,
    }
    # capture the real Flask app for background work
    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_job, args=(app, job_id, action, params), daemon=True)
    thread.start()
    return job_id


def get_audit_status(job_id: str) -> Dict:
    return audit_jobs.get(job_id, {"status": "unknown"})


def list_audit_runs(limit: int = 20) -> List[Dict]:
    runs = AuditRun.query.order_by(AuditRun.created_at.desc()).limit(limit).all()
    payload = []
    for run in runs:
        payload.append({
            "id": run.id,
            "action": run.action,
            "status": run.status,
            "created_at": run.created_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        })
    return payload


def get_audit_run(run_id: int) -> Dict | None:
    run = AuditRun.query.get(run_id)
    if not run:
        return None
    results = None
    if run.results_json:
        try:
            results = json.loads(run.results_json)
        except json.JSONDecodeError:
            results = run.results_json
    params = None
    if run.params_json:
        try:
            params = json.loads(run.params_json)
        except json.JSONDecodeError:
            params = run.params_json
    return {
        "id": run.id,
        "action": run.action,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "params": params,
        "results": results,
    }
