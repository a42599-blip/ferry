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

CREATE TABLE IF NOT EXISTS ad_views (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,          -- 什麼時候看的
    subject      TEXT,                      -- 誰（dev:xxx 或 user:<member_id>）
    tier         TEXT,                      -- guest / free / monthly / lifetime
    member_id    TEXT,                      -- 會員 id（訪客為 NULL）
    member_email TEXT,                      -- 會員 email（訪客為 NULL）
    seconds      REAL,                      -- 實際看了幾秒
    min_seconds  INTEGER,                   -- 當時要求看幾秒（正常 15）
    granted      INTEGER,                   -- 這次給了幾次免費次數
    kind         TEXT,                      -- download / transfer（當時被擋的動作）
    used_before  INTEGER,                   -- 看之前「合併已用」
    used_after   INTEGER,                   -- 看之後「合併已用」
    country      TEXT,                      -- 哪個國家（Cloudflare cf-ipcountry）
    device_id    TEXT,
    platform     TEXT,                      -- 手機 / 電腦
    ad_network   TEXT                       -- 這筆是被哪一家廣告商看到的（adsterra／hilltopads／adsense…）
);
CREATE INDEX IF NOT EXISTS idx_adviews_ts ON ad_views(ts);
CREATE INDEX IF NOT EXISTS idx_adviews_tier ON ad_views(tier);
CREATE INDEX IF NOT EXISTS idx_adviews_country ON ad_views(country);

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
    expires_at  REAL,
    country     TEXT,                      -- 註冊時的地區（cf-ipcountry）
    plan_started_at REAL,                  -- 付費起始時間
    status      TEXT DEFAULT 'active',     -- active / deleted（軟刪除）
    deleted_at  REAL,
    last_login_at REAL,
    last_seen_at  REAL,                    -- 最後有動作的時間（工作階段判斷）
    login_count INTEGER DEFAULT 0,
    marketing_opt_in INTEGER DEFAULT 0     -- 行銷信同意（合規：預設不同意）
);

CREATE TABLE IF NOT EXISTS orders (
    id          TEXT PRIMARY KEY,
    member_id   TEXT,
    plan        TEXT NOT NULL,
    amount      REAL NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT 'TWD',
    fee         REAL NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL,
    paid_at     REAL,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

-- 前台公告（小羅 2026-09-27：前台要留一個公告區塊）
CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    title       TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    level       TEXT    DEFAULT 'info',   -- info / warn / critical
    active      INTEGER DEFAULT 1,        -- 1=顯示中 0=隱藏
    expires_at  REAL,                     -- 可設定自動下架時間
    emailed     INTEGER DEFAULT 0         -- 有沒有同步寄給會員
);
CREATE INDEX IF NOT EXISTS idx_ann_ts ON announcements(ts);

-- 方案變更歷史（小羅 2026-09-27：「要知道付費時間、用什麼費率、剩餘多少」）
CREATE TABLE IF NOT EXISTS plan_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id   TEXT    NOT NULL,
    email       TEXT,
    from_plan   TEXT,
    to_plan     TEXT    NOT NULL,
    amount      REAL    DEFAULT 0,
    currency    TEXT    DEFAULT 'TWD',
    at          REAL    NOT NULL,
    expires_at  REAL,                      -- 這次變更後的到期時間
    reason      TEXT,                      -- signup / first_pay / renew / upgrade / expire / refund / admin
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_planh_member ON plan_history(member_id);
CREATE INDEX IF NOT EXISTS idx_planh_at ON plan_history(at);

-- 撥款紀錄（小羅 2026-09-30：真實撥款在「金流商後台」操作，這裡只登記「已到帳」的紀錄）
CREATE TABLE IF NOT EXISTS payouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    amount      REAL    NOT NULL,
    fee         REAL    DEFAULT 0,
    currency    TEXT    DEFAULT 'TWD',
    method      TEXT,                       -- bank / paypal / stripe ...
    note        TEXT,
    status      TEXT    DEFAULT 'done',     -- done 已到帳 / pending 處理中 / cancelled 取消
    done_at     REAL,
    provider    TEXT,                       -- newebpay / ecpay / payoneer / ezpay（哪個平台撥的）
    account     TEXT,                       -- 匯入哪個帳戶（台新 / LINE Bank …）
    fx_rate     REAL,                       -- 外幣換算匯率（原幣 → 台幣）
    amount_twd  REAL                        -- 換算後的台幣金額
);
CREATE INDEX IF NOT EXISTS idx_payouts_ts ON payouts(ts);

-- 退款紀錄（小羅 2026-09-30：要能看「哪些已退、哪些沒退、哪些處理中」）
--   實際刷退由金流商（藍新等）處理，這裡追蹤狀態與金額
CREATE TABLE IF NOT EXISTS refunds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT,
    member_id   TEXT,
    email       TEXT,
    provider    TEXT,                       -- newebpay / ecpay / ...
    amount      REAL    NOT NULL DEFAULT 0,
    currency    TEXT    DEFAULT 'TWD',
    reason      TEXT,
    status      TEXT    DEFAULT 'applied',  -- applied 申請中 / processing 處理中 / done 已退款 / rejected 已拒絕
    apply_at    REAL    NOT NULL,
    done_at     REAL,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_refunds_status ON refunds(status);
CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_id);

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
    note        TEXT,                      -- 後台處理備註（內部）
    reply       TEXT,                      -- 回給客戶的訊息（客戶看得到）
    replied_at  REAL,
    action      TEXT                       -- 處理了什麼（例：補下載 3 次）
);
CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback(ts);

-- 回覆紀錄（小羅 2026-09-28：「不要只有一次機會，我可以重複回覆這個訊息」）
--   一筆回報可以有很多次回覆，每次都留著，不會覆蓋前一次。
CREATE TABLE IF NOT EXISTS feedback_replies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_id INTEGER NOT NULL,
    ts          REAL    NOT NULL,
    reply       TEXT    NOT NULL,
    action      TEXT
);
CREATE INDEX IF NOT EXISTS idx_fbrep_fid ON feedback_replies(feedback_id);

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

-- 忘記密碼重設碼（小羅 2026-09-29：前台登入頁要有「忘記密碼」且要真的有效）
CREATE TABLE IF NOT EXISTS password_resets (
    token      TEXT PRIMARY KEY,           -- 隨機碼（連結裡的那串）
    member_id  TEXT NOT NULL,
    email      TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,              -- 30 分鐘後失效
    used       INTEGER NOT NULL DEFAULT 0  -- 1=已用過（不能再用）
);
CREATE INDEX IF NOT EXISTS idx_pwreset_member ON password_resets(member_id);
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
            "nickname": "TEXT",            # 暱稱（小羅 2026-09-29：會員可自己改）
            "password": "TEXT",
            "email": "TEXT",
            "tz": "TEXT",
            "expires_at": "REAL",
            # 會員來自哪個地區（Cloudflare 的 cf-ipcountry；台灣＝TW）
            "country": "TEXT",
            # 付費起始時間（「什麼時間加入會員」）
            "plan_started_at": "REAL",
            # 帳號狀態：active / deleted（軟刪除，合規要求保留一段時間）
            "status": "TEXT",
            "deleted_at": "REAL",
            "last_login_at": "REAL",
            "last_seen_at": "REAL",       # 最後「有動作」的時間（判斷工作階段用）
            "login_count": "INTEGER",
            # 願不願意收行銷信（合規：預設不收，要他自己同意）
            "marketing_opt_in": "INTEGER",
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
        # 客戶回報（小羅 2026-09-27：要能直接回覆客戶＋記錄處理了什麼）
        "feedback": {
            "reply": "TEXT",
            "replied_at": "REAL",
            "action": "TEXT",
        },
        "orders": {
            "fee": "REAL DEFAULT 0",
            "paid_at": "REAL",
            "note": "TEXT",
            # 小羅 2026-09-30：對帳要用（哪個平台收的、退了多少）
            "provider": "TEXT",
            "refund_amount": "REAL DEFAULT 0",
            # 金流商的交易序號（藍新的 TradeNo／商店訂單編號）→ 互相核對用
            "provider_txn": "TEXT",
            # 付款方式（信用卡／ATM／超商／Apple Pay／LINE Pay／支付寶…）
            # 金流商回傳時寫入；後台可以「依付款方式」分開統計
            "pay_method": "TEXT",
        },
        "payouts": {
            "provider": "TEXT",
            "account": "TEXT",
            "fx_rate": "REAL",
            "amount_twd": "REAL",
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
    # 舊訂單沒有 provider 欄位 → 從 note（provider=xxx）補回來（對帳要用）
    try:
        c.execute("UPDATE orders SET provider = substr(note, 10, instr(note||',', ',') - 10)"
                  " WHERE (provider IS NULL OR provider='') AND note LIKE 'provider=%'")
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
        # 舊資料庫升級：新增欄位時要 ALTER（沒有的話會拋錯，吞掉即可）
        for _sql in ("ALTER TABLE ad_views ADD COLUMN ad_network TEXT",):
            try:
                _conn.execute(_sql)
            except Exception:  # noqa: BLE001
                pass
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
