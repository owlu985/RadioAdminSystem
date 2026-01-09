#!/usr/bin/env python3
import argparse
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    values_sorted = sorted(values)
    k = (len(values_sorted) - 1) * pct
    f = int(k)
    c = min(f + 1, len(values_sorted) - 1)
    if f == c:
        return values_sorted[f]
    d0 = values_sorted[f] * (c - k)
    d1 = values_sorted[c] * (k - f)
    return d0 + d1


def _read_rss_bytes(pid: int) -> Optional[int]:
    status_path = f"/proc/{pid}/status"
    try:
        with open(status_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return None


def _format_bytes(value: Optional[int]) -> str:
    if value is None:
        return "n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def _make_url(base_url: str, path: str, params: Dict[str, str]) -> str:
    base = base_url if base_url.endswith("/") else f"{base_url}/"
    url = urljoin(base, path.lstrip("/"))
    return f"{url}?{urlencode(params)}"


def _request(url: str, timeout: float) -> Tuple[float, int, str]:
    start = time.perf_counter()
    status = 0
    try:
        request = Request(url, headers={"User-Agent": "RadioAdminSystemLoadTest/1.0"})
        with urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
            elapsed = time.perf_counter() - start
            return elapsed, status, ""
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return elapsed, status, str(exc)


def _sample_memory(pid: int, interval: float, stop_event: threading.Event, samples: List[int]) -> None:
    while not stop_event.is_set():
        rss = _read_rss_bytes(pid)
        if rss is not None:
            samples.append(rss)
        stop_event.wait(interval)


def generate_synthetic_library(root: str, music_files: int, psa_files: int) -> None:
    music_root = os.path.join(root, "music")
    psa_root = os.path.join(root, "psa")
    for base in (music_root, psa_root):
        os.makedirs(base, exist_ok=True)

    for idx in range(music_files):
        bucket = os.path.join(music_root, f"genre_{idx % 5}")
        os.makedirs(bucket, exist_ok=True)
        path = os.path.join(bucket, f"track_{idx:04d}.mp3")
        if not os.path.exists(path):
            with open(path, "wb") as handle:
                handle.write(b"")

    for idx in range(psa_files):
        bucket = os.path.join(psa_root, f"category_{idx % 3}")
        os.makedirs(bucket, exist_ok=True)
        path = os.path.join(bucket, f"psa_{idx:04d}.mp3")
        if not os.path.exists(path):
            with open(path, "wb") as handle:
                handle.write(b"")


def run_load_test(
    base_url: str,
    total_requests: int,
    concurrency: int,
    pages: int,
    timeout: float,
    pid: Optional[int],
    memory_interval: float,
) -> None:
    urls = []
    for idx in range(total_requests):
        page = (idx % pages) + 1
        if idx % 2 == 0:
            params = {"q": "%", "page": str(page)}
            urls.append(_make_url(base_url, "/api/music/search", params))
        else:
            params = {"page": str(page)}
            urls.append(_make_url(base_url, "/api/psa/library", params))

    random.shuffle(urls)
    latency: List[float] = []
    failures: List[Tuple[str, str]] = []
    status_counts: Dict[int, int] = {}

    memory_samples: List[int] = []
    stop_event = threading.Event()
    sampler_thread = None
    if pid is not None:
        sampler_thread = threading.Thread(
            target=_sample_memory,
            args=(pid, memory_interval, stop_event, memory_samples),
            daemon=True,
        )
        sampler_thread.start()

    start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_request, url, timeout): url for url in urls}
        for future in as_completed(futures):
            elapsed, status, error = future.result()
            latency.append(elapsed)
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
            if error:
                failures.append((futures[future], error))
    total_time = time.perf_counter() - start_time

    stop_event.set()
    if sampler_thread:
        sampler_thread.join(timeout=memory_interval + 0.5)

    print("\nLoad test results")
    print("=================")
    print(f"Total requests: {total_requests}")
    print(f"Concurrency: {concurrency}")
    print(f"Elapsed time: {total_time:.2f}s")
    if latency:
        print(f"Average latency: {mean(latency):.3f}s")
        for label, pct in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
            value = _percentile(latency, pct)
            if value is not None:
                print(f"{label} latency: {value:.3f}s")
    if status_counts:
        print("Status codes:")
        for code in sorted(status_counts):
            print(f"  {code}: {status_counts[code]}")
    if failures:
        print(f"Failures: {len(failures)}")
        for url, error in failures[:5]:
            print(f"  {url} -> {error}")
        if len(failures) > 5:
            print("  ...")

    if memory_samples:
        avg_mem = int(mean(memory_samples))
        print("Memory (RSS):")
        print(f"  Min: {_format_bytes(min(memory_samples))}")
        print(f"  Avg: {_format_bytes(avg_mem)}")
        print(f"  Max: {_format_bytes(max(memory_samples))}")
    elif pid is not None:
        print("Memory (RSS): unavailable (pid not found or /proc not readable)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple load test for RadioAdminSystem APIs.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000", help="Base URL for the server.")
    parser.add_argument("--requests", type=int, default=100, help="Total number of requests to send.")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of concurrent workers.")
    parser.add_argument("--pages", type=int, default=5, help="Number of pages to cycle through.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds per request.")
    parser.add_argument("--pid", type=int, help="PID of the server process to sample RSS memory.")
    parser.add_argument("--memory-interval", type=float, default=1.0, help="Memory sampling interval in seconds.")
    parser.add_argument("--generate-library", action="store_true", help="Generate a synthetic NAS test library.")
    parser.add_argument("--nas-root", default="instance/nas_test", help="NAS root for synthetic library.")
    parser.add_argument("--music-files", type=int, default=200, help="Synthetic music file count.")
    parser.add_argument("--psa-files", type=int, default=50, help="Synthetic PSA file count.")
    args = parser.parse_args()

    if args.generate_library:
        generate_synthetic_library(args.nas_root, args.music_files, args.psa_files)

    run_load_test(
        base_url=args.base_url,
        total_requests=args.requests,
        concurrency=args.concurrency,
        pages=args.pages,
        timeout=args.timeout,
        pid=args.pid,
        memory_interval=args.memory_interval,
    )


if __name__ == "__main__":
    main()
