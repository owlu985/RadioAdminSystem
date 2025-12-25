from datetime import datetime
from typing import Optional

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
