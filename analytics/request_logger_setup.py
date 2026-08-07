"""
Setup script for `panel serve --setup request_logger_setup.py`.

Runs once, before the Panel/Bokeh server starts accepting connections.
Hooks into Tornado's built-in `tornado.access` logger (which fires on every
completed HTTP request) to record basic usage stats:

  - timestamp, method, path, status, duration,
    user_agent, referer, accept_language                  -> SQLite
  - country / city / org / isp / domain (from ipwho.is)    -> SQLite AND Prometheus

The client IP is used only in-memory, to query ipwho.is (https://ipwho.is),
and is never written to disk, logged, or exposed as a metric label -- only
the resulting fields below are kept.

Tornado has no public per-request logging hook that's reachable from a
--setup script: Application.log_request() is the documented extension
point, but replacing it (via the `log_function` setting, or by
subclassing Application) both require access at Application(...)
construction time, which happens inside Panel/Bokeh well after this
script has already run. So tornado.web.Application.log_request is patched
directly instead (see _patch_log_request, called near the bottom of this
file, before the server starts accepting connections) -- that's the one
targeted monkeypatch in this file; everywhere else here avoids it (see
the admin_sessions note below). The patch always calls the original
log_request first and unmodified, so Tornado's own console access-log
line is untouched; it then independently reads method/path/status/
duration/ip/headers straight off the finished `handler` object and
enqueues them. Reading real attributes this way, rather than parsing them
back out of a formatted log string, means nothing here depends on how
any of these values happen to render as text.

Everything that can be slow (the ipwho.is HTTP call, the SQLite write) runs
on a single dedicated background thread, fed by a queue. The patched
log_request itself only reads a few attributes off the handler and does a
non-blocking queue.put -- it never runs on Tornado's event loop for longer
than a few microseconds, so a slow or unresponsive ipwho.is can no longer
stall the whole app for every user.
Successful lookups are cached in memory by IP, via functools.lru_cache, so
repeat visits don't re-spend ipwho.is's free-tier daily quota; a failed or
rate-limited call raises rather than returning a value, so lru_cache never
caches it -- it's simply retried the next time that IP shows up.

Pod memory/CPU history is handled separately, in resource_monitor.py
(imported below) -- see that file's own docstring for why it's kept fully
independent (own thread, own SQLite file) rather than folded in here.

Panel's built-in --admin panel's session records (state.session_info)
normally live only in memory and are lost on every pod restart. This
persists every session to a second SQLite table (admin_sessions, same file
as requests) and restores them on startup, using only Panel/Param's public
APIs -- state.param.watch(..., 'session_info') and a plain assignment to
state.session_info -- no monkeypatching needed. This version persists
every session as-is (no /stats-vs-/index filtering, since that's a
separate change you haven't adopted here either -- ask if you want it).

Why a table with incremental upserts, rather than periodically
pickling/dumping the whole state.session_info dict to a file: (1) safety
-- pickle can execute arbitrary code on load, which is unnecessary risk
for data that's just strings/floats/None; a table (or JSON) has no such
issue. (2) cost -- a periodic full dump gets more expensive over a long
uptime as history grows (admin mode disables Panel's own history trimming,
so this history is unbounded by default), whereas a per-event upsert
touches one row and stays cheap regardless of total history size. (3)
durability -- a fixed interval (say, every 60s) risks losing up to that
much recent state if the pod dies uncleanly between snapshots, whereas
writing as each lifecycle event happens (session created / rendered /
destroyed) keeps that window much smaller -- and there are normally only
2-4 such events per session, so this isn't a high write-rate design.
Writes still go through the same background worker thread as the request
log, so none of this runs synchronously on Tornado's event loop either.

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
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit

import requests
import tornado.web

# panel serve is a console-script entry point, which (unlike `python
# script.py`) does not add this script's own directory to sys.path -- so
# without this, `import resource_monitor` fails unless it happens to
# already be on the path some other way. __file__ is set correctly here
# by panel's --setup mechanism (it execs this file with its own path in
# the namespace), confirmed by testing.
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resource_monitor
from panel.io.state import state as pn_state

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
# Last-seen state per session, so the persistence watcher only writes rows
# that actually changed -- cheap regardless of how large the session
# history has grown. Only ever touched from the main/event-loop thread
# (Panel's param-watcher callbacks run there, not on our worker thread), so
# no lock is needed.
_last_known_sessions: dict = {}

# Only count the top-level page load, not static assets, the websocket
# connection, or the /stats and /admin pages -- avoids treating every
# asset fetch or heartbeat as a separate "visit".
_COUNTED_PATHS = {"", "/index"}


def _is_counted_path(raw_path: str) -> bool:
    """
    handler.request.uri is the raw request URI, which includes any query
    string (e.g. "/index?utm_source=...") -- comparing that directly against
    "/index" would silently miss it. This strips the query string and any
    trailing slash first, so "/", "/index", "/index/" and "/index?ref=x"
    all match, while "/static/...", "/index/ws", "/stats" and "/admin" don't.
    """
    path = urlsplit(raw_path).path.rstrip("/")
    return path in _COUNTED_PATHS


def _on_session_info_changed(event) -> None:
    """
    Fires whenever Panel updates state.session_info (session created,
    rendered, or destroyed -- see panel/io/state.py, all three call
    self.param.trigger('session_info') after mutating the dict in place).
    Diffs against _last_known_sessions so only genuinely new/changed
    sessions get enqueued -- cheap regardless of how large the overall
    history has grown. This version persists every session as-is; there's
    no /stats-vs-/index distinction here (see module docstring).
    """
    new_sessions = event.new.get("sessions", {})
    for session_id, session_data in new_sessions.items():
        if _last_known_sessions.get(session_id) != session_data:
            _enqueue_session_upsert(session_id, dict(session_data))
            _last_known_sessions[session_id] = dict(session_data)


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
            domain TEXT,
            user_agent TEXT,
            referer TEXT,
            accept_language TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_sessions (
            session_id TEXT PRIMARY KEY,
            launched REAL,
            started REAL,
            rendered REAL,
            ended REAL,
            user_agent TEXT
        )
        """
    )
    conn.commit()
    _prune_old_rows(conn)
    conn.close()


def _prune_old_rows(conn: sqlite3.Connection) -> None:
    if RETENTION_DAYS <= 0:
        return
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM requests WHERE ts < ?", (cutoff_iso,))
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).timestamp()
    conn.execute("DELETE FROM admin_sessions WHERE launched < ?", (cutoff_ts,))
    conn.commit()


def _load_persisted_sessions() -> None:
    """
    Restore state.session_info['sessions'] from admin_sessions, so the
    --admin panel's charts show history from before this pod started. Must
    run before the server starts accepting connections (i.e. from this
    --setup script) so it's in place before any real session, and before
    _on_session_info_changed is registered, so nothing races against it.

    Any restored row with ended IS NULL was, by definition, never cleanly
    closed -- that connection can't still be open after a pod restart, so
    it's marked ended here (both in memory and back in the DB) rather than
    left looking permanently "live".
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT session_id, launched, started, rendered, ended, user_agent FROM admin_sessions"
        ).fetchall()

        now = datetime.now(timezone.utc).timestamp()
        sessions = {}
        for session_id, launched, started, rendered, ended, user_agent in rows:
            if ended is None:
                # Can't still be open after a pod restart -- close it now.
                ended = now
                conn.execute("UPDATE admin_sessions SET ended = ? WHERE session_id = ?", (ended, session_id))
            data = {
                "launched": launched, "started": started, "rendered": rendered,
                "ended": ended, "user_agent": user_agent,
            }
            sessions[session_id] = data
            _last_known_sessions[session_id] = dict(data)
        conn.commit()
    finally:
        conn.close()

    if not sessions:
        return
    # Every restored session now has an 'ended' timestamp (see above), so
    # none of them are "live" -- that count only grows again as new,
    # actually-connected sessions come in after this point.
    pn_state.session_info = {"total": len(sessions), "live": 0, "sessions": sessions}
    log.info("Restored %d admin-panel session record(s) from %s", len(sessions), DB_PATH)


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


def _enqueue_session_upsert(session_id: str, session_data: dict) -> None:
    try:
        _work_queue.put_nowait(("session", session_id, session_data))
    except queue.Full:
        log.warning("request logging queue is full; dropping this admin-session update")


def _process_queued_request(
    ts: str, method: str, path: str, status: int, duration_ms: float, ip: str,
    user_agent: str, referer: str, accept_language: str,
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
            "(ts, method, path, status, duration_ms, country, country_code, city, organization, isp, domain, "
            "user_agent, referer, accept_language) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts, method, path, status, duration_ms, country, country_code,
                city, organization, isp, domain, user_agent, referer, accept_language,
            ),
        )
        conn.commit()
        if random.random() < 0.001:  # ~1 in 1000 writes: cheap periodic prune
            _prune_old_rows(conn)
    finally:
        conn.close()


def _process_session_upsert(session_id: str, data: dict) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO admin_sessions (session_id, launched, started, rendered, ended, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "started=excluded.started, rendered=excluded.rendered, "
            "ended=excluded.ended, user_agent=excluded.user_agent",
            (
                session_id, data.get("launched"), data.get("started"),
                data.get("rendered"), data.get("ended"), data.get("user_agent"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _worker_loop() -> None:
    while True:
        job = _work_queue.get()
        try:
            if job[0] == "request":
                _, ts, method, path, status, duration_ms, ip, user_agent, referer, accept_language = job
                _process_queued_request(
                    ts, method, path, status, duration_ms, ip, user_agent, referer, accept_language
                )
            elif job[0] == "session":
                _, session_id, data = job
                _process_session_upsert(session_id, data)
        except Exception:
            log.exception("failed to record %s", job[0] if job else "queued item")
        finally:
            _work_queue.task_done()


def _start_worker() -> None:
    threading.Thread(target=_worker_loop, name="request-logger-worker", daemon=True).start()


def _patch_log_request() -> None:
    """
    Application.log_request(self, handler) is Tornado's actual per-request
    logging hook (see its own docstring: "To change this behavior either
    subclass Application and override this method, or pass a function in
    the application settings dictionary as `log_function`"). Both of the
    documented ways to use it need the Application instance at
    construction time, which happens inside Panel/Bokeh well after this
    --setup script has already run, so neither is reachable from here.
    Patching the class method is the one remaining way in, and it has to
    happen exactly once, before any request is served, which module-level
    --setup execution guarantees, so there's no risk of double-patching.

    The original log_request always runs first, unmodified, so Tornado's
    own console access-log line (level chosen by status code, exact text)
    is completely untouched. Afterward, method/path/status/duration/ip and
    the three headers below are read directly off the finished `handler`
    object -- get_status() and request.request_time() are both already
    final by the time log_request runs. Reading real attributes this way,
    rather than parsing them back out of a formatted log string, means
    arbitrary characters in a header or a path can't misalign a field.

    Headers are taken as-is (defaulting to "" if absent -- e.g. some bots
    don't send a User-Agent).
    """
    original_log_request = tornado.web.Application.log_request

    def _log_request_and_enqueue(self, handler: tornado.web.RequestHandler) -> None:
        original_log_request(self, handler)
        try:
            path = handler.request.uri
            if not _is_counted_path(path):
                return
            headers = handler.request.headers
            ts = datetime.now(timezone.utc).isoformat()
            try:
                _work_queue.put_nowait((
                    "request",
                    ts,
                    handler.request.method,
                    path,
                    handler.get_status(),
                    1000.0 * handler.request.request_time(),
                    handler.request.remote_ip,
                    headers.get("User-Agent", ""),
                    headers.get("Referer", ""),
                    headers.get("Accept-Language", ""),
                ))
            except queue.Full:
                log.warning("request logging queue is full; dropping this entry")
        except Exception:
            log.exception("failed to enqueue request for logging")

    tornado.web.Application.log_request = _log_request_and_enqueue


_init_db()
_load_persisted_sessions()
_init_prometheus()
_patch_log_request()
_start_worker()
resource_monitor.start_monitoring()
pn_state.param.watch(_on_session_info_changed, "session_info")

# Tornado's tornado.access logger defaults to a level that can suppress
# its own console output depending on how the root logger is configured
# elsewhere in the image; this only affects that console line (still
# produced by the untouched original log_request above), not the enqueue
# above, which runs unconditionally.
access_logger = logging.getLogger("tornado.access")
access_logger.setLevel(logging.INFO)
log.info("Request logging active -> %s (retention: %d days, 0 = disabled)", DB_PATH, RETENTION_DAYS)
