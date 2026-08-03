"""
Pod-level memory/CPU monitor, independent of both Panel's own --admin
memory/CPU charts (confirmed to be per-admin-session, in-memory only,
recreated empty on every /admin page load -- see panel/io/admin.py's
get_process_info(), which streams into a fresh Trend widget each time)
and of request_logger_setup.py's own worker thread/queue -- deliberately
kept on its own thread and its own SQLite file, writing synchronously, so
a backlog or failure in request logging can never delay or block a
resource sample, and vice versa.

Reads the container's own cgroup accounting files directly (v1 or v2,
auto-detected) rather than psutil, because psutil reports *this Python
process's* usage, not the cgroup/container-level usage that the kernel's
OOM killer and Kubernetes' CPU throttling actually act on -- the number
that matters for "why did the pod get killed" is the cgroup's, not the
process's. Falls back to psutil (process-level) only if no cgroup files
are found at all, e.g. when testing outside a container.

Every sample is one row in `resource_usage`, on the same PVC as
requests.db by default (a separate file, resource_usage.db, so a
concurrent write from request_logger_setup.py's worker thread can never
contend for the same SQLite file). A special 'started' row is written
once per process start, so a query/plot can immediately show the samples
leading up to any given restart -- e.g.:

    SELECT ts, memory_percent, cpu_percent FROM resource_usage
    WHERE ts BETWEEN datetime(?, '-10 minutes') AND ?
    -- where ? is the ts of the 'started' row you're investigating

Usable two ways:
  1. In-process (default): from another --setup script, or directly as
     panel serve's --setup, call start_monitoring() once.
  2. As a fully separate pod/sidecar in the same namespace: run this file
     directly (`python resource_monitor.py`). It only needs read access to
     its own container's /sys/fs/cgroup and a writable path for its
     database -- no dependency on Panel, the main app, or any Kubernetes
     API/metrics-server. Point it at a PVC mounted into that pod (e.g. the
     same one, if it's genuinely a sidecar sharing the pod, mounted
     read-write in both containers) via RESOURCE_DB.

     Note this only reports on *this pod's* own cgroup, so a separate pod
     would need to run as a sidecar in the *same pod spec* as BrimView (a
     second container sharing that pod's cgroup tree isn't automatic
     either -- containers in a pod get separate cgroups -- so genuinely
     reading BrimView's own usage from a different container requires
     either running in-process here, or switching to the Kubernetes
     Metrics API instead of these files, which needs metrics-server
     running in-cluster plus RBAC to read it). In-process is the simpler,
     dependency-free default for that reason; ask if you'd rather have the
     Metrics API version instead.

Configure via environment variables:
  RESOURCE_DB                 path to this module's own sqlite file
                                (default /data/resource_usage.db)
  RESOURCE_SAMPLE_SECONDS      how often to sample (default 10)
  REQUEST_LOG_RETENTION_DAYS  reused from request_logger_setup.py so
                                there's one retention knob for the whole
                                app (default 0 = disabled)
"""

import logging
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("RESOURCE_DB", "/data/resource_usage.db")
SAMPLE_SECONDS = int(os.environ.get("RESOURCE_SAMPLE_SECONDS", "10"))
RETENTION_DAYS = int(os.environ.get("REQUEST_LOG_RETENTION_DAYS", "0"))

log = logging.getLogger("resource_monitor")

_CGROUP_V2_ROOT = "/sys/fs/cgroup"
_CGROUP_V1_MEMORY = "/sys/fs/cgroup/memory"
_CGROUP_V1_CPU = "/sys/fs/cgroup/cpu"
_CGROUP_V1_CPUACCT = "/sys/fs/cgroup/cpuacct"

# Sentinel-ish threshold for cgroup v1's "unlimited" memory.limit_in_bytes,
# which is some huge page-aligned number close to but not exactly 2**63-1
# (observed 9223372036854771712 in testing) rather than one exact value.
_V1_UNLIMITED_MEMORY_THRESHOLD = 2**62


def _read_int(path: str):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _read_cgroup_v2() -> dict:
    root = _CGROUP_V2_ROOT
    stats: dict = {}

    stats["memory_bytes"] = _read_int(os.path.join(root, "memory.current"))

    try:
        with open(os.path.join(root, "memory.max")) as f:
            raw = f.read().strip()
        stats["memory_limit_bytes"] = None if raw == "max" else int(raw)
    except Exception:
        stats["memory_limit_bytes"] = None

    try:
        with open(os.path.join(root, "cpu.max")) as f:
            quota_str, period_str = f.read().split()
        period = int(period_str)
        stats["cpu_limit_cores"] = None if quota_str == "max" else int(quota_str) / period
    except Exception:
        stats["cpu_limit_cores"] = None

    try:
        with open(os.path.join(root, "cpu.stat")) as f:
            fields = dict(line.split() for line in f.read().splitlines() if line.strip())
        stats["cpu_usage_usec"] = int(fields.get("usage_usec", "") or 0) or None
        stats["cpu_nr_throttled"] = int(fields.get("nr_throttled", "") or 0)
        stats["cpu_throttled_usec"] = int(fields.get("throttled_usec", "") or 0)
    except Exception:
        stats["cpu_usage_usec"] = None
        stats["cpu_nr_throttled"] = None
        stats["cpu_throttled_usec"] = None

    return stats


def _read_cgroup_v1() -> dict:
    stats: dict = {}

    stats["memory_bytes"] = _read_int(os.path.join(_CGROUP_V1_MEMORY, "memory.usage_in_bytes"))
    limit = _read_int(os.path.join(_CGROUP_V1_MEMORY, "memory.limit_in_bytes"))
    stats["memory_limit_bytes"] = None if limit is None or limit >= _V1_UNLIMITED_MEMORY_THRESHOLD else limit

    quota = _read_int(os.path.join(_CGROUP_V1_CPU, "cpu.cfs_quota_us"))
    period = _read_int(os.path.join(_CGROUP_V1_CPU, "cpu.cfs_period_us"))
    if quota is not None and quota > 0 and period:
        stats["cpu_limit_cores"] = quota / period
    else:
        stats["cpu_limit_cores"] = None

    usage_ns = _read_int(os.path.join(_CGROUP_V1_CPUACCT, "cpuacct.usage"))
    stats["cpu_usage_usec"] = usage_ns // 1000 if usage_ns is not None else None

    try:
        with open(os.path.join(_CGROUP_V1_CPU, "cpu.stat")) as f:
            fields = dict(line.split() for line in f.read().splitlines() if line.strip())
        stats["cpu_nr_throttled"] = int(fields.get("nr_throttled", "0"))
        # v1 reports throttled_time in *nanoseconds*, unlike v2's microseconds.
        stats["cpu_throttled_usec"] = int(fields.get("throttled_time", "0")) // 1000
    except Exception:
        stats["cpu_nr_throttled"] = None
        stats["cpu_throttled_usec"] = None

    return stats


def _read_psutil_fallback() -> dict:
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        return {
            "memory_bytes": proc.memory_info().rss,
            "memory_limit_bytes": None,
            "cpu_usage_usec": None,
            "cpu_limit_cores": None,
            "cpu_nr_throttled": None,
            "cpu_throttled_usec": None,
        }
    except Exception:
        return {}


def _read_stats() -> dict:
    if os.path.exists(os.path.join(_CGROUP_V2_ROOT, "cpu.max")):
        stats = _read_cgroup_v2()
    elif os.path.isdir(_CGROUP_V1_MEMORY):
        stats = _read_cgroup_v1()
    else:
        log.warning("no cgroup v1 or v2 files found; falling back to psutil (process-level, not container-level)")
        stats = _read_psutil_fallback()
    return stats


def _init_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event TEXT NOT NULL DEFAULT 'sample',
            memory_bytes INTEGER,
            memory_limit_bytes INTEGER,
            memory_percent REAL,
            cpu_usage_usec INTEGER,
            cpu_percent REAL,
            cpu_limit_cores REAL,
            cpu_nr_throttled INTEGER,
            cpu_throttled_usec INTEGER
        )
        """
    )
    conn.commit()
    _prune_old_rows(conn)
    return conn


def _prune_old_rows(conn: sqlite3.Connection) -> None:
    if RETENTION_DAYS <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM resource_usage WHERE ts < ?", (cutoff,))
    conn.commit()


def _insert_row(event: str, stats: dict, cpu_percent, memory_percent) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO resource_usage "
            "(ts, event, memory_bytes, memory_limit_bytes, memory_percent, "
            "cpu_usage_usec, cpu_percent, cpu_limit_cores, cpu_nr_throttled, cpu_throttled_usec) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts, event, stats.get("memory_bytes"), stats.get("memory_limit_bytes"), memory_percent,
                stats.get("cpu_usage_usec"), cpu_percent, stats.get("cpu_limit_cores"),
                stats.get("cpu_nr_throttled"), stats.get("cpu_throttled_usec"),
            ),
        )
        conn.commit()
        if random.random() < 0.01:  # ~1 in 100 writes: cheap periodic prune
            _prune_old_rows(conn)
    finally:
        conn.close()


# (wall_clock_time, cpu_usage_usec) from the previous sample, needed to
# turn the cumulative cpu_usage_usec counter into a "% of one core over
# the last interval" rate. Only ever touched from _monitor_loop's own
# thread, so no lock is needed.
_prev_cpu_sample = None


def _sample_once(event: str = "sample") -> None:
    global _prev_cpu_sample
    stats = _read_stats()
    now = time.time()

    cpu_percent = None
    usage = stats.get("cpu_usage_usec")
    if usage is not None and _prev_cpu_sample is not None:
        prev_time, prev_usage = _prev_cpu_sample
        dt = now - prev_time
        if dt > 0:
            cpu_percent = ((usage - prev_usage) / 1_000_000) / dt * 100
    if usage is not None:
        _prev_cpu_sample = (now, usage)

    memory_percent = None
    mem = stats.get("memory_bytes")
    limit = stats.get("memory_limit_bytes")
    if mem is not None and limit:
        memory_percent = mem / limit * 100

    _insert_row(event, stats, cpu_percent, memory_percent)


def _monitor_loop() -> None:
    while True:
        try:
            _sample_once()
        except Exception:
            log.exception("resource sample failed")
        time.sleep(SAMPLE_SECONDS)


def start_monitoring() -> None:
    """Call once, early, before serving traffic (e.g. from a --setup script)."""
    conn = _init_db()
    conn.close()
    _sample_once(event="started")  # marks exactly when this process began
    threading.Thread(target=_monitor_loop, name="resource-monitor", daemon=True).start()
    log.info(
        "Resource monitoring active -> %s (every %ds, retention: %d days, 0 = disabled)",
        DB_PATH, SAMPLE_SECONDS, RETENTION_DAYS,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start_monitoring()
    while True:
        time.sleep(3600)
