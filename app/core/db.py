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
    url         TEXT,                      -- 使用者實際貼的網址（解析／下載）
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
    source      TEXT,
    member_id   TEXT,                      -- 若已登入，綁定的會員
    member_email TEXT
);

CREATE TABLE IF NOT EXISTS members (
    id          TEXT PRIMARY KEY,
    email       TEXT UNIQUE,
    password    TEXT,
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

-- 提現紀錄（後台把收入提出來時記錄；實際撥款由金流商處理）
CREATE TABLE IF NOT EXISTS payouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    amount      REAL    NOT NULL,
    fee         REAL    DEFAULT 0,
    currency    TEXT    DEFAULT 'USD',
    method      TEXT,                       -- bank / paypal / stripe ...
    note        TEXT,
    status      TEXT    DEFAULT 'pending',  -- pending / done / cancelled
    done_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_payouts_ts ON payouts(ts);

-- 使用者回報問題（任何人都能送，不限會員）
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    device_id   TEXT,
    message     TEXT    NOT NULL,
    contact     TEXT,
    platform    TEXT,                      -- 送出時正在看哪個平台（自動附帶）
    url         TEXT,                      -- 送出時輸入框的連結（方便重現）
    app_version TEXT,
    handled     INTEGER DEFAULT 0,         -- 0=未處理 1=已處理
    note        TEXT                       -- 後台處理備註
);
CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback(ts);

CREATE TABLE IF NOT EXISTS quotas (
    kind       TEXT NOT NULL,              -- download / transfer
    subject    TEXT NOT NULL,              -- dev:xxx / user:xxx / ip:xxx
    date_key   TEXT NOT NULL,              -- 該時區的 YYYY-MM-DD
    count      INTEGER NOT NULL DEFAULT 0,
    tz         TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, subject, date_key)
);

-- 配對過的裝置（規格書：配對過永久記住）
CREATE TABLE IF NOT EXISTS pairings (
    device_a   TEXT NOT NULL,
    device_b   TEXT NOT NULL,
    last_at    REAL NOT NULL,
    times      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (device_a, device_b)
);
"""


def data_dir() -> str:
    d = settings.data_dir or os.path.join(os.getcwd(), "data")
    os.makedirs(d, exist_ok=True)
    return d


def path() -> str:
    return _path


def _migrate(c: sqlite3.Connection) -> None:
    """輕量遷移：舊資料庫缺少的欄位就補上（不會動到已有的資料）。"""

    def cols(table: str) -> set[str]:
        try:
            return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            return set()

    wanted = {
        "members": {
            "password": "TEXT",
            "email": "TEXT",
            "tz": "TEXT",
            "expires_at": "REAL",
        },
        "events": {
            "is_new": "INTEGER",
            "meta": "TEXT",
            "country": "TEXT",
            "url": "TEXT",
            "referrer": "TEXT",
            "browser": "TEXT",
            "os": "TEXT",
            "device_id": "TEXT",
        },
        "devices": {
            "member_id": "TEXT",
            "member_email": "TEXT",
            "source": "TEXT",
        },
        "orders": {
            "fee": "REAL DEFAULT 0",
            "paid_at": "REAL",
            "note": "TEXT",
        },
    }
    for table, fields in wanted.items():
        have = cols(table)
        if not have:
            continue
        for name, decl in fields.items():
            if name not in have:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                except sqlite3.Error:
                    pass
    # 已經有 email 的會員，把 email 設唯一（重複不影響）
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_members_email ON members(email)")
    except sqlite3.Error:
        pass
    c.commit()


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
        _migrate(_conn)
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
