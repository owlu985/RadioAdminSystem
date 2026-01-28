from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from flask import current_app

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
