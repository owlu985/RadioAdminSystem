from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import current_app, Request

from app.logger import init_logger

logger = init_logger()


@dataclass
class RouteMetric:
    name: str
    duration_ms: float
    response_bytes: int
    status_code: int
    request_bytes: Optional[int] = None
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2),
            "response_bytes": self.response_bytes,
            "status_code": self.status_code,
            "request_bytes": self.request_bytes,
            "timestamp": self.timestamp,
        }


def start_route_timer() -> float:
    return time.perf_counter()


def _metrics_dir() -> Path:
    metrics_dir = Path(current_app.instance_path) / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    return metrics_dir


def record_route_metric(metric: RouteMetric) -> None:
    payload = metric.to_dict()
    metrics_path = _metrics_dir() / "route_metrics.jsonl"
    with metrics_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    logger.info(
        "Route metrics %s duration=%.2fms response_bytes=%s request_bytes=%s status=%s",
        metric.name,
        metric.duration_ms,
        metric.response_bytes,
        metric.request_bytes,
        metric.status_code,
    )


def finalize_route_metrics(
    name: str,
    start_time: float,
    response: Any,
    request: Request | None = None,
) -> Any:
    response_obj = current_app.make_response(response)
    duration_ms = (time.perf_counter() - start_time) * 1000
    response_bytes = len(response_obj.get_data() or b"")
    request_bytes = request.content_length if request else None
    metric = RouteMetric(
        name=name,
        duration_ms=duration_ms,
        response_bytes=response_bytes,
        status_code=response_obj.status_code,
        request_bytes=request_bytes,
        timestamp=datetime.utcnow().isoformat(),
    )
    record_route_metric(metric)
    return response_obj
