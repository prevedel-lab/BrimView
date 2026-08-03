"""
Setup script for `panel serve --setup request_logger_setup.py`.

Runs once, before the Panel/Bokeh server starts accepting connections.
Hooks into Tornado's built-in `tornado.access` logger (which fires on every
completed HTTP request) to record basic usage stats:

  - timestamp, method, path, status, duration        -> SQLite
  - country / city / org / isp / domain (from ipwho.is) -> SQLite AND Prometheus

The client IP is used only in-memory, to query ipwho.is (https://ipwho.is),
and is never written to disk, logged, or exposed as a metric label -- only
the resulting fields below are kept.

Everything that can be slow (the ipwho.is HTTP call, the SQLite write) runs
on a single dedicated background thread, fed by a queue. `emit()` itself
only does a regex match and a non-blocking queue.put -- it never runs on
Tornado's event loop for longer than a few microseconds, so a slow or
unresponsive ipwho.is can no longer stall the whole app for every user.
Successful lookups are cached in memory by IP, via functools.lru_cache, so
repeat visits don't re-spend ipwho.is's free-tier daily quota; a failed or
rate-limited call raises rather than returning a value, so lru_cache never
caches it -- it's simply retried the next time that IP shows up.

Pod memory/CPU history is handled separately, in resource_monitor.py
(imported below) -- see that file's own docstring for why it's kept fully
independent (own thread, own SQLite file) rather than folded in here.

Requires:
    - `panel serve ... --use-xheaders` so the real client IP (from
        X-Forwarded-For / X-Real-Ip, set by your ingress) is used instead of
        the ingress pod's internal IP.
    - `pip install requests`
    - `pip install prometheus_client` only when ENABLE_PROMETHEUS is enabled

Configure via environment variables:
  REQUEST_LOG_DB              path to the sqlite file (default /data/requests.db)
  REQUEST_LOG_RETENTION_DAYS  delete rows older than this many days
                               (default 0 = disabled, i.e. rows are kept
                               forever unless you set this explicitly)
  ENABLE_PROMETHEUS           true/false toggle for Prometheus metrics
                               (default false -- matches Dockerfile.analytics's
                               own ARG ENABLE_PROMETHEUS=false default)
  METRICS_PORT                port for the Prometheus /metrics endpoint (default 9100)
"""

import ipaddress
import logging
import os
import queue
import random
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit

import requests

# panel serve is a console-script entry point, which (unlike `python
# script.py`) does not add this script's own directory to sys.path -- so
# without this, `import resource_monitor` fails unless it happens to
# already be on the path some other way. __file__ is set correctly here
# by panel's --setup mechanism (it execs this file with its own path in
# the namespace), confirmed by testing.
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resource_monitor

DB_PATH = os.environ.get("REQUEST_LOG_DB", "/data/requests.db")
RETENTION_DAYS = int(os.environ.get("REQUEST_LOG_RETENTION_DAYS", "0"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENABLE_PROMETHEUS = _env_bool("ENABLE_PROMETHEUS", False)
_metrics_port_raw = os.environ.get("METRICS_PORT", "9100")
METRICS_PORT = int(_metrics_port_raw)

log = logging.getLogger("request_logger")
_requests_counter = None

# Tornado's default access-log line renders as e.g.:
#   "200 GET /brimview/ (203.0.113.5) 12.34ms"
_LOG_RE = re.compile(r"^(\d{3})\s+(\S+)\s+(.*)\s+\(([^)]+)\)\s+([\d.]+)ms$")

# Only count the top-level page load, not static assets, the websocket
# connection, or the /stats and /admin pages -- avoids treating every
# asset fetch or heartbeat as a separate "visit".
_COUNTED_PATHS = {"", "/index"}


def _is_counted_path(raw_path: str) -> bool:
    """
    Tornado's logged path is the raw request URI, which includes any query
    string (e.g. "/index?utm_source=...") -- comparing that directly against
    "/index" would silently miss it. This strips the query string and any
    trailing slash first, so "/", "/index", "/index/" and "/index?ref=x"
    all match, while "/static/...", "/index/ws", "/stats" and "/admin" don't.
    """
    path = urlsplit(raw_path).path.rstrip("/")
    return path in _COUNTED_PATHS


def _init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            method TEXT,
            path TEXT,
            status INTEGER,
            duration_ms REAL,
            country TEXT,
            country_code TEXT,
            city TEXT,
            organization TEXT,
            isp TEXT,
            domain TEXT
        )
        """
    )
    conn.commit()
    _prune_old_rows(conn)
    conn.close()


def _prune_old_rows(conn: sqlite3.Connection) -> None:
    if RETENTION_DAYS <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM requests WHERE ts < ?", (cutoff,))
    conn.commit()


def _init_prometheus() -> None:
    global _requests_counter
    if not ENABLE_PROMETHEUS:
        _requests_counter = None
        log.info("Prometheus metrics disabled by ENABLE_PROMETHEUS")
        return

    try:
        from prometheus_client import Counter, start_http_server

        start_http_server(METRICS_PORT)
        _requests_counter = Counter(
            "brimview_requests_total",
            "Total requests, by approximate visitor location",
            ["country", "city"],
        )
        log.info("Prometheus metrics exposed on :%d/metrics", METRICS_PORT)
    except Exception as exc:
        _requests_counter = None
        log.warning("Prometheus metrics disabled (%s)", exc)


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


class IpWhoisRateLimitError(RuntimeError):
    pass


@lru_cache(maxsize=4096)
def _fetch_ipwhois(ip: str) -> dict:
    resp = requests.get(
        f"https://ipwho.is/{ip}",
        params={"output": "json"},
        timeout=5,
    )

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        message = "ipwho.is rate limit exceeded"
        if retry_after:
            message += f"; retry after {retry_after}s"
        raise IpWhoisRateLimitError(message)

    resp.raise_for_status()

    data = resp.json()
    if not isinstance(data, dict):
        raise TypeError("ipwho.is returned a non-object JSON response")

    if data.get("success") is False:
        raise ValueError(data.get("message", "ipwho.is request failed"))

    return data


def _lookup_geo(ip: str) -> dict:
    """
    Resolve an IP to ipwho.is's response dict (country, country_code, city,
    connection.{org,isp,domain}, ...). Runs on the background worker
    thread, never on Tornado's event loop.

    _fetch_ipwhois is wrapped in functools.lru_cache: successful calls are
    cached by IP (bounded to 4096 entries, LRU-evicted) for the life of the
    process, so repeat visits don't re-spend ipwho.is's daily quota.
    lru_cache only caches a call that actually returns -- since
    _fetch_ipwhois raises on failure/rate-limit, those calls are never
    cached and will simply be retried next time that IP shows up. Returns
    {} for private IPs or failed lookups.
    """
    if _is_private(ip):
        return {}
    try:
        return _fetch_ipwhois(ip)
    except IpWhoisRateLimitError as e:
        log.warning(f"ipwho.is rate limit exceeded; skipping geo lookup for {ip}. {e}")
    except Exception as e:
        log.warning(f"Failed to lookup geo for {ip}: {e}")
    return {}


# Queue feeding the background worker thread. Bounded so that if ipwho.is
# is slow and the worker falls behind, we drop new entries (with a log
# warning) instead of growing memory without limit -- this is best-effort
# analytics, not something that should ever apply backpressure to real
# requests.
_work_queue: "queue.Queue" = queue.Queue(maxsize=1000)


def _process_queued_request(
    ts: str, method: str, path: str, status: int, duration_ms: float, ip: str
) -> None:
    ip_lookup_dict = _lookup_geo(ip)  # ip discarded after this line; never stored

    country = ip_lookup_dict.get("country")
    country_code = ip_lookup_dict.get("country_code")
    city = ip_lookup_dict.get("city")

    country_label = country or "Unknown"
    city_label = city or "Unknown"

    connection_dict = ip_lookup_dict.get("connection") or {}
    organization = connection_dict.get("org")
    isp = connection_dict.get("isp")
    domain = connection_dict.get("domain")

    if _requests_counter is not None:
        _requests_counter.labels(country=country_label, city=city_label).inc()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO requests "
            "(ts, method, path, status, duration_ms, country, country_code, city, organization, isp, domain) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, method, path, status, duration_ms, country, country_code, city, organization, isp, domain),
        )
        conn.commit()
        if random.random() < 0.001:  # ~1 in 1000 writes: cheap periodic prune
            _prune_old_rows(conn)
    finally:
        conn.close()


def _worker_loop() -> None:
    while True:
        ts, method, path, status, duration_ms, ip = _work_queue.get()
        try:
            _process_queued_request(ts, method, path, status, duration_ms, ip)
        except Exception:
            log.exception("failed to record request")
        finally:
            _work_queue.task_done()


def _start_worker() -> None:
    threading.Thread(target=_worker_loop, name="request-logger-worker", daemon=True).start()


class GeoAccessLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Everything in here must be fast and non-blocking: this runs
        # directly on Tornado's event loop for every single request.
        try:
            match = _LOG_RE.match(record.getMessage())
            if not match:
                return
            status, method, path, ip, duration_ms = match.groups()
            if not _is_counted_path(path):
                return
            ts = datetime.now(timezone.utc).isoformat()
            try:
                _work_queue.put_nowait((ts, method, path, int(status), float(duration_ms), ip))
            except queue.Full:
                log.warning("request logging queue is full; dropping this entry")
        except Exception:
            log.exception("failed to enqueue request for logging")


_init_db()
_init_prometheus()
_start_worker()
resource_monitor.start_monitoring()

access_logger = logging.getLogger("tornado.access")
access_logger.setLevel(logging.INFO)
access_logger.addHandler(GeoAccessLogHandler())
log.info("Request logging active -> %s (retention: %d days, 0 = disabled)", DB_PATH, RETENTION_DAYS)
