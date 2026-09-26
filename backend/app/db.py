import json
import sqlite3
from datetime import datetime
from typing import Any

from .config import DB_PATH, DEFAULT_SETTINGS, SCHEMA_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# 老库里已存在的表要加列时走这里。`CREATE TABLE IF NOT EXISTS` 不会给已有表补列，
# 所以升级版本时必须在这里登记，否则用户的数据文件会缺列直接报错。
COLUMN_MIGRATIONS = [
    ("sync_meta", "attempted_to", "TEXT"),
    ("backtest_run", "params", "TEXT"),
    ("backtest", "name", "TEXT"),
    ("backtest", "error_code", "TEXT"),
    ("backtest", "fee_snapshot", "TEXT"),
    ("backtest", "trace", "TEXT"),
    ("backtest", "quality", "TEXT"),
]


def migrate() -> list[str]:
    applied = []
    with connect() as conn:
        for table, col, decl in COLUMN_MIGRATIONS:
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if table in _tables(conn) and col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                applied.append(f"{table}.{col}")
    return applied


def _tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(k, v) VALUES(?, ?)", (k, v)
            )
    migrate()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_settings() -> dict[str, str]:
    out = dict(DEFAULT_SETTINGS)
    with connect() as conn:
        for row in conn.execute("SELECT k, v FROM settings"):
            out[row["k"]] = row["v"]
    return out


def get_setting(key: str, cast: Any = str):
    raw = get_settings().get(key, DEFAULT_SETTINGS.get(key, ""))
    if raw is None or raw == "":
        return None
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return DEFAULT_SETTINGS.get(key)


def get_fee_config() -> dict[str, float]:
    """撮合费用参数，回测与实时模拟必须共用这一份。"""
    s = get_settings()

    def f(key: str) -> float:
        try:
            return float(s.get(key, DEFAULT_SETTINGS[key]))
        except (TypeError, ValueError):
            return float(DEFAULT_SETTINGS[key])

    return {
        "commission_rate": f("commission_rate"),
        "commission_min": f("commission_min"),
        "stamp_duty_rate": f("stamp_duty_rate"),
        "transfer_fee_rate": f("transfer_fee_rate"),
        "slippage": f("slippage"),
        "risk_free_rate": f("risk_free_rate"),
        "annual_days": f("annual_days"),
    }


def set_settings(items: dict[str, str]) -> None:
    with connect() as conn:
        for k, v in items.items():
            if k in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings(k, v) VALUES(?, ?) "
                    "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                    (k, str(v)),
                )


def set_state(key: str, value: Any) -> None:
    """程序自己写的运行状态（区别于用户设置），用户不改。"""
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_state(k, v, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = excluded.updated_at",
            (key, dumps(value), now()),
        )


def get_state(key: str, fallback: Any = None) -> Any:
    """返回 set_state 存的对象，附带它写入的时间，便于判断新鲜度。"""
    with connect() as conn:
        row = conn.execute("SELECT v, updated_at FROM app_state WHERE k = ?", (key,)).fetchone()
    if row is None:
        return fallback
    value = loads(row["v"], fallback)
    if isinstance(value, dict):
        value.setdefault("_at", row["updated_at"])
    return value


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(text: str | None, fallback: Any = None) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback
