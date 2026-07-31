"""
Setup script for `panel serve --setup request_logger_setup.py`.

Runs once, before the Panel/Bokeh server starts accepting connections.
Hooks into Tornado's built-in `tornado.access` logger (which fires on every
completed HTTP request) to record basic usage stats:

  - timestamp, method, path, status, duration  -> SQLite (queryable history)
  - country / city (derived from IP via GeoIP) -> SQLite AND Prometheus

The client IP is used only in-memory, for the GeoIP lookup, and is never
written to disk, logged, or exposed as a metric label. Only the resulting
country/city are kept.

Requires:
  - `panel serve ... --use-xheaders` so the real client IP (from
    X-Forwarded-For / X-Real-Ip, set by your ingress) is used instead of
    the ingress pod's internal IP.
  - `pip install prometheus_client`

Configure via environment variables:
  REQUEST_LOG_DB              path to the sqlite file (default /data/requests.db)
  REQUEST_LOG_RETENTION_DAYS  delete rows older than this many days (default 180, 0 disables)
  METRICS_PORT                port for the Prometheus /metrics endpoint (default 9100)
"""

import ipaddress
import requests
import logging
import os
import random
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("REQUEST_LOG_DB", "/data/requests.db")
RETENTION_DAYS = int(os.environ.get("REQUEST_LOG_RETENTION_DAYS", "0"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9100"))

log = logging.getLogger("request_logger")
_lock = threading.Lock()
_requests_counter = None

# Tornado's default access-log line renders as e.g.:
#   "200 GET /brimview/ (203.0.113.5) 12.34ms"
_LOG_RE = re.compile(r"^(\d{3})\s+(\S+)\s+(.*)\s+\(([^)]+)\)\s+([\d.]+)ms$")


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

def _lookup_geo(ip: str):
    """Resolve an IP to a dictionary, which is guaranteed to 
    contain at least (country_name, country_iso_code, city).
    If the IP is private or the lookup fails, returns an empty dict.
    """
    if _is_private(ip):
        return {}
    try:
        resp = _fetch_ipwhois(ip)
        return resp
    except IpWhoisRateLimitError as e:
        log.warning(f"ipwho.is rate limit exceeded; skipping geo lookup for {ip}. {e}")
    except Exception as e:
        log.warning(f"Failed to lookup geo for {ip}: {e}")
    return {}


class GeoAccessLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            match = _LOG_RE.match(record.getMessage())
            if not match:
                return
            status, method, path, ip, duration_ms = match.groups()
            if not (path == '' or path == "/" or path == "/index"):
                # discard requests to subpaths to avoid double-counting
                return
            ip_lookup_dict = _lookup_geo(ip)  # ip discarded after this line

            country = ip_lookup_dict.get("country")
            country_code = ip_lookup_dict.get("country_code")
            city = ip_lookup_dict.get("city")

            country_label = country or "Unknown"
            city_label = city or "Unknown"

            connection_dict = ip_lookup_dict.get("connection", {})
            organization = connection_dict.get("org")
            isp = connection_dict.get("isp")
            domain = connection_dict.get("domain")

            if _requests_counter is not None:
                _requests_counter.labels(country=country_label, city=city_label).inc()

            ts = datetime.now(timezone.utc).isoformat()
            with _lock:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT INTO requests "
                    "(ts, method, path, status, duration_ms, country, country_code, city, organization, isp, domain) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, method, path, int(status), float(duration_ms), country, country_code, city, organization, isp, domain),
                )
                conn.commit()
                if random.random() < 0.001:  # ~1 in 1000 writes: cheap periodic prune
                    _prune_old_rows(conn)
                conn.close()
        except Exception:
            log.exception("failed to record request")


_init_db()
_init_prometheus()

access_logger = logging.getLogger("tornado.access")
access_logger.setLevel(logging.INFO)
access_logger.addHandler(GeoAccessLogHandler())
log.info("Request logging active -> %s (retention: %d days)", DB_PATH, RETENTION_DAYS)
