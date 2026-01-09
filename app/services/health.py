from datetime import datetime
from typing import Any, Dict, Optional
import os
import sys
import resource

from app.logger import init_logger
from app.models import JobHealth, db

logger = init_logger()


def _touch_job(name: str) -> JobHealth:
    job = JobHealth.query.filter_by(name=name).first()
    if not job:
        job = JobHealth(name=name, failure_count=0, restart_count=0)
        db.session.add(job)
        db.session.commit()
    return job


def record_failure(job_name: str, reason: Optional[str] = None, restarted: bool = False) -> JobHealth:
    job = _touch_job(job_name)
    job.failure_count = (job.failure_count or 0) + 1
    job.last_failure_at = datetime.utcnow()
    if reason:
        job.last_failure_reason = reason[:250]
    if restarted:
        job.restart_count = (job.restart_count or 0) + 1
        job.last_restart_at = datetime.utcnow()
    db.session.commit()
    status = "(self-healed)" if restarted else ""
    logger.warning("Job %s failure %s%s", job_name, status, f": {reason}" if reason else "")
    return job


def record_restart(job_name: str) -> JobHealth:
    job = _touch_job(job_name)
    job.restart_count = (job.restart_count or 0) + 1
    job.last_restart_at = datetime.utcnow()
    db.session.commit()
    logger.info("Job %s restarted", job_name)
    return job


def get_health_snapshot():
    jobs = JobHealth.query.all()
    return {
        job.name: {
            "failures": job.failure_count or 0,
            "restarts": job.restart_count or 0,
            "last_failure": job.last_failure_at,
            "last_restart": job.last_restart_at,
            "reason": job.last_failure_reason,
        }
        for job in jobs
    }


def _get_rss_bytes() -> Optional[int]:
    statm_path = "/proc/self/statm"
    if os.path.exists(statm_path):
        try:
            with open(statm_path, "r", encoding="utf-8") as fh:
                parts = fh.read().split()
            if len(parts) >= 2:
                rss_pages = int(parts[1])
                return rss_pages * os.sysconf("SC_PAGE_SIZE")
        except Exception:
            pass
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    if sys.platform == "darwin":
        return int(usage)
    return int(usage * 1024)


def _collect_cache_stats() -> Dict[str, Any]:
    stats: Dict[str, Any] = {}
    try:
        from app.services import api_cache

        stats["api_cache_entries"] = api_cache.stats().get("entries")
    except Exception:
        stats["api_cache_entries"] = None
    try:
        from app.services import music_search

        cached = music_search._MUSIC_INDEX_CACHE.get("data")
        if isinstance(cached, dict):
            stats["music_index_files"] = len(cached.get("files", {}))
    except Exception:
        stats.setdefault("music_index_files", None)
    try:
        from app.services import media_library

        cached = media_library._MEDIA_INDEX_CACHE.get("data")
        if isinstance(cached, dict):
            stats["media_index_files"] = len(cached.get("files", {}))
    except Exception:
        stats.setdefault("media_index_files", None)
    return stats


def log_resource_usage(context: str = "periodic") -> None:
    rss_bytes = _get_rss_bytes()
    rss_mb = round(rss_bytes / (1024 * 1024), 2) if rss_bytes is not None else None
    cache_stats = _collect_cache_stats()
    logger.info("Resource usage (%s): rss_mb=%s cache=%s", context, rss_mb, cache_stats)
