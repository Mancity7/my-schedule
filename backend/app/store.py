"""缓存层：把"下载"变成"本地有就用，没有才取"，并且守住一只股票一个来源。

三条硬规则（都对应 PRD 验收）：
1. 同一 `(code, period, adjust)` 的K线只允许来自一个上游。要换源就整段重下覆盖，
   绝不"缺哪补哪"——新浪与腾讯的前复权序列历史段能差 56%，拼起来会伪造信号。
2. 前复权序列每发生一次除权就整体重新锚定，所以只补最新一天是错的。
   缓存只在 `cache_ttl_days` 内可信，过期后整段重下（新浪一次请求就返回全量，代价一样）。
3. 周线/月线一律由日线重采样派生，不调任何外部接口。
"""

from datetime import date, datetime

import pandas as pd

from .config import DB_PATH
from .datasource import fetch_daily, fetch_index_daily, fetch_trade_calendar
from .db import connect, get_setting, get_state, now, set_state
from .errors import integrity_error

PERIOD = "daily"
ADJUST = "qfq"
# 对外只有这三种写法（前端、数据库、接口同一套词），不再让用户猜 w 还是 weekly
PERIODS = ("daily", "weekly", "monthly")
RESAMPLE_ALIASES = {"WEEKLY": "W-FRI", "MONTHLY": "ME"}
# 周/月线各列的聚合口径。换手率按日相加（一周累计换手），涨跌幅用派生收盘重算。
RESAMPLE_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "amount": "sum",
    "turnover": "sum",
}
INSERT_SQL = (
    "INSERT OR REPLACE INTO kline"
    "(code, period, adjust, ts, open, high, low, close, volume, amount, turnover, pct_chg, source)"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _d(s: str | date) -> str:
    """任意写法的日子 -> YYYY-MM-DD，取前 10 位，容忍 '2026-01-01 00:00' 这种。"""
    return str(s).strip()[:10]


def today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _age_days(ts: str) -> float:
    try:
        then = datetime.strptime(_d(ts), "%Y-%m-%d")
    except ValueError:
        return 9e9
    return (datetime.now() - then).total_seconds() / 86400.0


# ------------------------------------------------------------------ 读与写


def _f(v):
    """数字列可能整列缺失（例如手工导入的CSV没有换手率），缺就存 NULL，不补 0。"""
    return None if v is None or pd.isna(v) else float(v)


def upsert_kline(df: pd.DataFrame, code: str, period: str = PERIOD, adjust: str = ADJUST) -> int:
    """写入K线。单事务 executemany，缺价行直接丢（没有价格的那根没法回测）。"""
    if df is None or not len(df):
        return 0
    rows = []
    for r in df.itertuples(index=False):
        if any(pd.isna(getattr(r, c, None)) for c in ("open", "high", "low", "close")):
            continue
        rows.append((
            code, period, adjust, _d(r.ts),
            float(r.open), float(r.high), float(r.low), float(r.close),
            _f(getattr(r, "volume", None)),
            _f(getattr(r, "amount", None)),
            _f(getattr(r, "turnover", None)),
            _f(getattr(r, "pct_chg", None)),
            str(getattr(r, "source", "") or ""),
        ))
    if not rows:
        return 0
    with connect() as conn:
        conn.executemany(INSERT_SQL, rows)
    return len(rows)


def load_kline(code: str, period: str = PERIOD, adjust: str = ADJUST,
               start: str = "", end: str = "") -> pd.DataFrame:
    """读出一段升序K线，索引为 ts，便于指标层直接对齐。"""
    sql = ("SELECT ts, open, high, low, close, volume, amount, turnover, pct_chg, source "
           "FROM kline WHERE code=? AND period=? AND adjust=?")
    args: list = [code, period, adjust]
    if start:
        sql += " AND ts>=?"
        args.append(_d(start))
    if end:
        sql += " AND ts<=?"
        args.append(_d(end))
    sql += " ORDER BY ts"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    if not rows:
        return pd.DataFrame(columns=[
            "ts", "open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg", "source"
        ]).set_index("ts")
    df = pd.DataFrame([dict(r) for r in rows])
    return df.drop_duplicates(subset=["ts"]).set_index("ts").sort_index()


def sources_of(code: str, period: str = PERIOD, adjust: str = ADJUST) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT source FROM kline WHERE code=? AND period=? AND adjust=?",
            (code, period, adjust),
        ).fetchall()
    return sorted({r["source"] or "" for r in rows})


def get_meta(code: str, period: str = PERIOD, adjust: str = ADJUST) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sync_meta WHERE code=? AND period=? AND adjust=?",
            (code, period, adjust),
        ).fetchone()
    return dict(row) if row else None


def set_meta(code: str, first_ts: str, last_ts: str, attempted_to: str = "",
             period: str = PERIOD, adjust: str = ADJUST) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sync_meta(code, period, adjust, first_ts, last_ts, attempted_to, updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(code, period, adjust) "
            "DO UPDATE SET first_ts=excluded.first_ts, last_ts=excluded.last_ts, "
            "attempted_to=excluded.attempted_to, updated_at=excluded.updated_at",
            (code, period, adjust, first_ts, last_ts, attempted_to, now()),
        )


# ------------------------------------------------------------------ 交易日历


def ensure_calendar(force: bool = False) -> dict:
    """交易日历一次取全（1990 至今年年底），之后一个月刷一次。"""
    with connect() as conn:
        row = conn.execute("SELECT MAX(ts) last_ts, MAX(updated_at) at FROM trade_calendar").fetchone()
    if not force and row["last_ts"] and row["last_ts"] >= today() and _age_days(row["at"] or "") <= 30:
        return {"days": count_trading_days("1990-12-19", today()), "downloaded": False,
                "until": row["last_ts"]}
    days = fetch_trade_calendar()
    stamp = now()
    with connect() as conn:
        conn.execute("DELETE FROM trade_calendar")
        conn.executemany("INSERT OR REPLACE INTO trade_calendar(ts, updated_at) VALUES(?,?)",
                         [(d, stamp) for d in days])
    return {"days": len(days), "downloaded": True, "until": days[-1]}


def count_trading_days(start: str, end: str) -> int:
    if not start or not end:
        return 0
    with connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM trade_calendar WHERE ts>=? AND ts<=?", (_d(start), _d(end))
        ).fetchone()[0])


def last_trading_day(d: str) -> str:
    """`d` 当天或之前最后一个交易日。日历为空时原样返回，宁可多下一次也别猜。"""
    d = _d(d)
    with connect() as conn:
        row = conn.execute("SELECT MAX(ts) t FROM trade_calendar WHERE ts<=?", (d,)).fetchone()
    return row["t"] or d


def is_trading_day(d: str) -> bool:
    d = _d(d)
    with connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM trade_calendar").fetchone()[0])
        if not total:
            return True     # 日历还没同步过，不下判断，别把正常流程挡住
        n = int(conn.execute("SELECT COUNT(*) FROM trade_calendar WHERE ts=?", (d,)).fetchone()[0])
    return n > 0


# ------------------------------------------------------------------ 增量判断与取数


def cache_state(code: str, start: str, end: str, period: str = PERIOD, adjust: str = ADJUST) -> dict:
    """这段区间是否已经缓存、缓存到哪天。首页的「✓缓存至09-24」用的就是它。"""
    meta = get_meta(code, period, adjust)
    srcs = [s for s in sources_of(code, period, adjust) if s]
    base = {"cached": False, "reason": "never_synced", "first_ts": None, "last_ts": None,
            "sources": srcs, "mixed": len(srcs) > 1}
    if not meta:
        return base
    # 周末、长假、以及当天收盘前都不算"缺尾"：只要上次已经请求到这个日期就够了，
    # 否则每次回测都会重下一遍（违反验收 C4），还白白增加被限流的风险。
    wanted_end = last_trading_day(min(_d(end) or today(), today()))
    stale_reason = None
    if _d(meta["first_ts"]) > _d(start):
        stale_reason = "missing_head"
    elif _d(meta["last_ts"]) < wanted_end and _d(meta.get("attempted_to") or "") < wanted_end:
        stale_reason = "missing_tail"
    elif _age_days(meta["updated_at"]) > _ttl_days():
        stale_reason = "expired"
    return {
        "cached": stale_reason is None,
        "reason": stale_reason,
        "first_ts": meta["first_ts"],
        "last_ts": meta["last_ts"],
        "sources": srcs,
        "mixed": len(srcs) > 1,
    }


def _ttl_days() -> float:
    try:
        return max(0.0, float(get_setting("cache_ttl_days")))
    except (TypeError, ValueError):
        return 1.0


def ensure_kline(code: str, start: str, end: str, period: str = PERIOD, adjust: str = ADJUST,
                 on_attempt=None, force: bool = False) -> dict:
    """保证 `[start, end]` 的日线在库里且来自同一上游。已缓存则一次网络都不碰。

    force=True 对应缓存列表里的[重新下载]：不管新鲜度，整段重下覆盖。
    """
    code = str(code).strip()
    if period != PERIOD:
        # 周/月线是日线派生出来的，库里只有日线。谁要是直接下载"周线"，
        # 就等于把日线当周线存了一份，回测会拿到错一半的K线（验收 C9 的反面）。
        raise integrity_error(
            "DERIVED_PERIOD",
            f"{period} 是由日线派生的，不需要也不能单独下载。",
            detail="请用 get_kline(code, period, ...)，它取日线后在内存里重采样。",
            field="period",
        )
    start, end = _d(start), _d(end) or today()
    end = min(end, today())          # 未来的日期没有K线，别把它记成"已经请求到"
    state = cache_state(code, start, end, period, adjust)
    if state["cached"] and not force:
        df = load_kline(code, period, adjust, start, end)
        return {
            "code": code, "downloaded": False, "rows": int(len(df)),
            "from_ts": state["first_ts"], "to_ts": state["last_ts"],
            "sources": state["sources"], "elapsed": 0.0,
        }

    meta = get_meta(code, period, adjust)
    # 要下载了才需要同样的"日历"：判断这次请求到的日期算不算最新交易日
    cal = ensure_calendar()
    # 重下的范围 = 缓存已有的整段 ∪ 本次请求区间。两头都不能收窄：
    # 少要一头就会把已缓存的那半删掉（删完再写只写了请求区间），用户的历史凭空变短；
    # 只从请求区间开始下则会把新旧两段留成两个上游，成了混源。
    from_ts = min(start, _d(meta["first_ts"])) if meta else start
    to_ts = max(end, _d(meta["last_ts"])) if meta else end
    t0 = datetime.now()
    df = fetch_daily(code, from_ts, to_ts, adjust=adjust, on_attempt=on_attempt)
    with connect() as conn:
        conn.execute("DELETE FROM kline WHERE code=? AND period=? AND adjust=?", (code, period, adjust))
    written = upsert_kline(df, code, period, adjust)
    # 报价缺全的根在 upsert 里已经丢了，所以这里以"库里实际存了什么"为准回报，
    # 别让接口说"缓存到 09-24"而下一轮回测又报缺数据。
    stored = [str(t)[:10] for t in load_kline(code, period, adjust).index]
    if not stored:
        raise integrity_error(
            "NO_USABLE_BAR", f"{code} 在 {from_ts}~{to_ts} 没有取到一根可用的K线", field=code,
            detail="上游返回的内容缺价格字段。可以换个上游重试，或稍后再试。",
        )
    set_meta(code, min(stored), max(stored), attempted_to=to_ts, period=period, adjust=adjust)
    elapsed = (datetime.now() - t0).total_seconds()
    # rows 一律指"请求区间内有多少根"，和走缓存那条分支同一个口径；
    # 整段写了多少根另说，免得前端两处显示的意思不一样。
    return {
        "code": code, "downloaded": True,
        "rows": int(sum(1 for t in stored if start <= t <= end)), "written": int(written),
        "from_ts": min(stored), "to_ts": max(stored), "attempted_to": to_ts,
        "calendar_until": cal.get("until"),
        "sources": [s for s in sources_of(code, period, adjust) if s],
        "elapsed": round(elapsed, 2),
    }


def assert_single_source(code: str, period: str = PERIOD, adjust: str = ADJUST) -> None:
    """回测前的完整性检查：混过源的数据一律拒绝（验收 F13）。"""
    srcs = [s for s in sources_of(code, period, adjust) if s]
    if len(srcs) > 1:
        raise integrity_error(
            "MIXED_SOURCE_KLINE",
            f"{code} 的K线来自 {'、'.join(srcs)} 两个数据源，不能用来回测。",
            detail="各源前复权算法不同，拼在一起会在拼接日凭空出现大幅涨跌。"
                   "请在缓存列表点[重新下载]整段覆盖，或删掉该股缓存后重取。",
            field=code,
        )


# ------------------------------------------------------------------ 派生周期


def resample(df: pd.DataFrame, freq: str = "weekly") -> pd.DataFrame:
    """日线 -> 周线/月线。只吃内存里的日线，绝不再调接口（验收 C9）。

    输入索引为 ts（YYYY-MM-DD）的日线；输出同结构，涨跌幅按派生收盘重算。
    """
    if str(freq).strip().upper() not in RESAMPLE_ALIASES:
        raise integrity_error("BAD_PERIOD",
                              f"不支持的周期 {freq}，可选：{'、'.join(PERIODS)}", field=str(freq))
    if df is None or not len(df):
        return df if df is not None else pd.DataFrame()
    freq = str(freq).strip().upper()
    d = df.copy()
    d.index = pd.to_datetime(d.index.astype(str))
    d.index.name = "bar_ts"        # 固定名字，reset_index 之后才知道要改回 ts
    d = d.sort_index()
    rule = RESAMPLE_ALIASES[freq]
    cols = [c for c in RESAMPLE_AGG if c in d.columns]
    out = d[cols].resample(rule).agg(RESAMPLE_AGG)
    if "source" in d.columns:
        # 派生周期里 source 只用于展示；正常一条日线序列本来就只有一个来源
        out["source"] = d["source"].resample(rule).agg(lambda s: "/".join(sorted(set(s.dropna()))))
    out = out.dropna(subset=["close"]).reset_index()
    out["ts"] = pd.to_datetime(out["bar_ts"]).dt.strftime("%Y-%m-%d")
    out = out.drop(columns=["bar_ts"])
    out["pct_chg"] = pd.to_numeric(out["close"], errors="coerce").pct_change() * 100.0
    keep = [c for c in ["ts", "open", "high", "low", "close", "volume", "amount",
                        "turnover", "pct_chg", "source"] if c in out.columns]
    return out[keep]


def get_kline(code: str, period: str, start: str, end: str, adjust: str = ADJUST,
              ensure: bool = True, on_attempt=None) -> pd.DataFrame:
    """回测与首页统一入口：daily 直取，周/月线由日线派生。"""
    if period == PERIOD:
        if ensure:
            ensure_kline(code, start, end, period, adjust, on_attempt=on_attempt)
        return load_kline(code, period, adjust, start, end)
    daily = get_kline(code, PERIOD, start, end, adjust, ensure=ensure, on_attempt=on_attempt)
    return resample(daily, period)


# ------------------------------------------------------------------ 基准指数


def get_benchmark(code: str | None = None, start: str = "", end: str = "",
                  force: bool = False) -> pd.DataFrame:
    """基准指数收盘价序列，落进 index_kline，之后不再依赖网络。"""
    code = (code or "").strip() or (get_setting("benchmark") or "000300")
    start, end = _d(start), _d(end) or today()
    state = get_state("benchmark_synced") or {}
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            "FROM index_kline WHERE code=?", (code,)
        ).fetchone()
    n = int(row["n"] or 0)
    # 新鲜度只能看"上次什么时候抓的"（存在 app_state 里），
    # 看数据日期会把一月份抓的基准当成过期，每次回测都白跑一趟网络。
    covered = (
        n > 0 and not force
        and state.get("code") == code
        and (row["first_ts"] or "9999") <= (start or "1990-12-19")
        and (row["last_ts"] or "") >= min(end or today(), today())
        and _age_days(state.get("_at") or "") <= _ttl_days()
    )
    if not covered:
        df = fetch_index_daily(code, start, end)
        with connect() as conn:
            conn.execute("DELETE FROM index_kline WHERE code=?", (code,))
        rows = [
            (code, _d(r.ts),
             None if pd.isna(r.open) else float(r.open),
             None if pd.isna(r.high) else float(r.high),
             None if pd.isna(r.low) else float(r.low),
             float(r.close),
             None if pd.isna(getattr(r, "volume", float("nan"))) else float(r.volume),
             None if pd.isna(getattr(r, "amount", float("nan"))) else float(r.amount))
            for r in df.itertuples(index=False) if not pd.isna(r.close)
        ]
        if not rows:
            raise integrity_error("EMPTY_BENCHMARK", f"基准 {code} 取回来是空的", field=code)
        with connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO index_kline(code, ts, open, high, low, close, volume, amount)"
                " VALUES(?,?,?,?,?,?,?,?)", rows,
            )
        set_state("benchmark_synced", {"code": code, "rows": len(rows), "last_ts": rows[-1][1]})
    return load_benchmark(code, start, end)


def load_benchmark(code: str, start: str = "", end: str = "") -> pd.DataFrame:
    sql = "SELECT ts, close FROM index_kline WHERE code=?"
    args: list = [code]
    if start:
        sql += " AND ts>=?"
        args.append(_d(start))
    if end:
        sql += " AND ts<=?"
        args.append(_d(end))
    sql += " ORDER BY ts"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return pd.DataFrame([dict(r) for r in rows]).set_index("ts") if rows else pd.DataFrame(
        columns=["ts", "close"]).set_index("ts")


# ------------------------------------------------------------------ 估值快照


def get_valuation(code: str, force: bool = False) -> dict:
    """最近一期 PE/PB，只给个股信息卡展示，不进回测计算（验收 F12）。

    百度给的是稀疏序列（数据日期可能是一周前），所以新鲜度按**抓取时间**算，
    存在 app_state 里而不是 valuation 表——那张表留给 V2 的真正逐日序列。
    """
    from .datasource import fetch_valuation_latest

    code = str(code).strip()
    key = f"valuation:{code}"
    cached = get_state(key)
    if cached and not force and _age_days(cached.get("_at") or "") <= _ttl_days():
        return cached
    raw = fetch_valuation_latest(code)
    snapshot = {
        "code": code,
        "pe_ttm": raw.get("pe_ttm"),
        "pe_ttm_date": raw.get("pe_ttm_date"),
        "pb": raw.get("pb"),
        "pb_date": raw.get("pb_date"),
        "available": raw.get("pe_ttm") is not None or raw.get("pb") is not None,
    }
    if snapshot["available"]:
        set_state(key, snapshot)
        return {**snapshot, "_at": now()}
    # 一个都没取到：不写缓存，也不报错——展示用数据缺了就空着
    return {**snapshot, "_at": None}


# ------------------------------------------------------------------ 缓存管理


def cache_stats() -> dict:
    """缓存统计（验收 K5）。混源股票数单独给，设置页要标红。"""
    with connect() as conn:
        def one(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0] or 0)

        stats = {
            "db_size_mb": round(DB_PATH.stat().st_size / 1048576.0, 2) if DB_PATH.exists() else 0.0,
            "stocks": one("SELECT COUNT(DISTINCT code) FROM kline"),
            "kline_rows": one("SELECT COUNT(*) FROM kline"),
            "index_rows": one("SELECT COUNT(*) FROM index_kline"),
            "stock_list": one("SELECT COUNT(*) FROM stock_basic"),
            "strategies": one("SELECT COUNT(*) FROM strategy"),
            "backtests": one("SELECT COUNT(*) FROM backtest"),
            "mixed_count": one(
                "SELECT COUNT(*) FROM (SELECT code FROM kline "
                "GROUP BY code, period, adjust HAVING COUNT(DISTINCT source) > 1)"
            ),
        }
    return stats


def list_cached() -> list[dict]:
    """已缓存股票列表（验收 K6）：每行显示代码/名称/周期/根数/最后日期/数据源，混源的带标记。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT k.code, k.period, k.adjust, COUNT(*) AS rows_, MIN(k.ts) AS first_ts, "
            "MAX(k.ts) AS last_ts, GROUP_CONCAT(DISTINCT k.source) AS sources, "
            "b.name AS name FROM kline k LEFT JOIN stock_basic b ON b.code = k.code "
            "GROUP BY k.code, k.period, k.adjust ORDER BY k.code"
        ).fetchall()
    out = []
    for r in rows:
        # GROUP_CONCAT 默认用逗号分隔，别和 resample 里的 "/" 搞混
        srcs = sorted({s for s in (r["sources"] or "").split(",") if s})
        out.append({
            **{k: r[k] for k in ("code", "period", "adjust", "rows_", "first_ts", "last_ts", "name")},
            "sources": srcs,
            "mixed": len(srcs) > 1,
        })
    return out


def drop_cache(code: str) -> int:
    """删掉一只股票的行情缓存。策略与回测记录不动（验收 K7）。"""
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM kline WHERE code=?", (code,)).fetchone()[0]
        conn.execute("DELETE FROM kline WHERE code=?", (code,))
        conn.execute("DELETE FROM sync_meta WHERE code=?", (code,))
    return n


def clear_market_cache() -> dict:
    """清空行情（验收 K8）：K线与指数走，策略、回测记录、设置都留。"""
    with connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
        conn.execute("DELETE FROM kline")
        conn.execute("DELETE FROM sync_meta")
        conn.execute("DELETE FROM index_kline")
    return {"kline_rows_removed": int(before)}
