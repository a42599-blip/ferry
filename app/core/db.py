"""SQLite 資料層（**唯一**碰資料庫的地方）。

規格書第 10 章：後台要看數據，所以「匿名統計事件、設定、會員、訂單」要存；
但**檔案完全不存**（下載走 CDN、傳輸走裝置之間）。

⚠️ 所有 SQL 都集中在這裡，其他模組不准自己連 DB（規格書第 21-2 章）。

資料位置：環境變數 `DATA_DIR`（Railway 正式站請掛 Volume）；預設 `./data`。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_path: str = ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    kind        TEXT    NOT NULL,          -- page_view / resolve / download / transfer_*
    device_id   TEXT,
    platform    TEXT,
    result      TEXT,                      -- ok / fail
    latency_ms  INTEGER,
    quality     TEXT,
    size        INTEGER,
    mode        TEXT,
    error_code  TEXT,
    country     TEXT,
    referrer    TEXT,
    utm         TEXT,
    path        TEXT,
    os          TEXT,
    browser     TEXT,
    is_new      INTEGER,                   -- 1=新訪客 0=回訪
    meta        TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, ts);
CREATE INDEX IF NOT EXISTS idx_events_plat ON events(platform, ts);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    visits      INTEGER NOT NULL DEFAULT 0,
    country     TEXT,
    os          TEXT,
    browser     TEXT,
    source      TEXT
);

CREATE TABLE IF NOT EXISTS members (
    id          TEXT PRIMARY KEY,
    email       TEXT,
    plan        TEXT NOT NULL DEFAULT 'free',
    device_id   TEXT,
    tz          TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL
);

CREATE TABLE IF NOT EXISTS orders (
    id          TEXT PRIMARY KEY,
    member_id   TEXT,
    plan        TEXT NOT NULL,
    amount      REAL NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT 'USD',
    fee         REAL NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL,
    paid_at     REAL,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

CREATE TABLE IF NOT EXISTS quotas (
    kind       TEXT NOT NULL,              -- download / transfer
    subject    TEXT NOT NULL,              -- dev:xxx / user:xxx / ip:xxx
    date_key   TEXT NOT NULL,              -- 該時區的 YYYY-MM-DD
    count      INTEGER NOT NULL DEFAULT 0,
    tz         TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, subject, date_key)
);
"""


def data_dir() -> str:
    d = settings.data_dir or os.path.join(os.getcwd(), "data")
    os.makedirs(d, exist_ok=True)
    return d


def path() -> str:
    return _path


def connect() -> sqlite3.Connection:
    """取得（必要時建立）連線。單一連線 + 執行緒鎖（SQLite 夠用且簡單）。"""
    global _conn, _path
    with _lock:
        if _conn is not None:
            return _conn
        _path = os.path.join(data_dir(), "ferry.db")
        _conn = sqlite3.connect(_path, check_same_thread=False, timeout=15)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(SCHEMA)
        _conn.commit()
        return _conn


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with _lock:
        c = connect()
        c.execute(sql, tuple(params))
        c.commit()


def executemany(sql: str, seq: Iterable[Iterable[Any]]) -> None:
    with _lock:
        c = connect()
        c.executemany(sql, [tuple(x) for x in seq])
        c.commit()


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        c = connect()
        return c.execute(sql, tuple(params)).fetchall()


def one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
    r = one(sql, params)
    if r is None:
        return default
    v = r[0]
    return default if v is None else v


# ── 設定（key-value）──────────────────────────────────
def get_setting(key: str, default: Any = None) -> Any:
    r = one("SELECT value FROM settings WHERE key = ?", (key,))
    if r is None:
        return default
    try:
        return json.loads(r["value"])
    except Exception:  # noqa: BLE001
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), time.time()),
    )


def db_size_bytes() -> int:
    try:
        return os.path.getsize(_path) + os.path.getsize(_path + "-wal")
    except OSError:
        return 0
