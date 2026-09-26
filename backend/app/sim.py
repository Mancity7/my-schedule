"""实时模拟盘：把回测内核改成"一天一天往前走"，状态入库、可断点续算。

三条设计决定，都是被现实逼出来的：

1. **不重写撮合，只换驱动方式。** 结算调用的是 `backtest.make_runner()` + `step_bar()`，
   和回测跑的是同一个类、同一份代码。PRD §5.4 要求「回测与模拟盘必须共用同一套撮合
   逻辑」——只要这里出现第二份成交/费用/涨跌停判断，两边的数字就再也不可比了。

2. **游标（settled_to）只在整批成功后推进，所有写库在一个事务里。** 这就是"重启幂等
   恢复"：容器结算到一半被杀，事务回滚、游标没动，下次重跑同一段得到完全一样的结果。
   反过来，如果先写成交再推游标，崩溃就会留下"钱扣了但游标说没结算过"的账。

3. **盘中不结算当天。** 今天还没收盘就结算，等于拿半天的K线当一整天用：收盘价是临时的，
   止损可能今天没触发但明天会触发，而游标一推进这天就永久定死了。所以 `settle_cutoff()`
   在 15:00 之前一律退回上一个交易日。

用户可能好几天才打开一次，所以补算必须能一次跨过多个交易日，并且正确处理跨天的
委托顺延（例如信号日跌停卖不出，委托要活到下一个交易日才成交）。
"""

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any

from . import dsl as dsl_mod
from . import metrics as metrics_mod
from . import store
from .backtest import BacktestResult, initial_state, make_runner
from .config import round_money
from .db import connect, dumps, get_fee_config, get_setting, loads, now
from .errors import AppError, input_error

log = logging.getLogger("app.sim")

# warmup 之外多要的余量：停牌、缺数据都会吃掉几根，宁可多取也别让指标算不出来
LOOKBACK_MARGIN = 10
# A股收盘时间。到点之后当天的K线才算定盘
CLOSE_AT = (15, 0)
# 后台定时结算的间隔（分钟）。用户一周只打开几次，剩下靠它兜底
SCHEDULE_MINUTES = 30
# 一根周线/月线分别对应多少个交易日，用来把暖机根数换算成要回溯的自然跨度
PERIOD_SPAN = {"daily": 1, "weekly": 5, "monthly": 21}
STATUS = ("running", "paused", "closed")

_stop = threading.Event()
_thread: threading.Thread | None = None
_tick = threading.Lock()          # 防止两轮定时结算叠在一起
_acct_locks: dict[int, threading.Lock] = {}
_acct_locks_guard = threading.Lock()


def _d(s: Any) -> str:
    return str(s or "").strip()[:10]


def _account_lock(aid: int) -> threading.Lock:
    """一个账户一把锁。

    页面打开会触发一次结算，后台定时器同时也会到点。两边要是并发结算同一个账户，
    会各自读到同一个游标、各算一遍、各插一份成交 —— 账直接翻倍。
    """
    with _acct_locks_guard:
        lk = _acct_locks.get(aid)
        if lk is None:
            lk = _acct_locks[aid] = threading.Lock()
        return lk


# ------------------------------------------------------------------ 时间口径


def settle_cutoff(when: datetime | None = None) -> str:
    """最后一个「已经定盘」的交易日。结算只允许推进到这一天为止。"""
    when = when or datetime.now()
    d = when.strftime("%Y-%m-%d")
    if not store.is_trading_day(d):
        return store.last_trading_day(d)
    if (when.hour, when.minute) < CLOSE_AT:
        yesterday = (when - timedelta(days=1)).strftime("%Y-%m-%d")
        return store.last_trading_day(yesterday)
    return d


def back_n_trading_days(d: str, n: int) -> str:
    """从 `d` 往前数 n 个交易日。日历没同步到位就按自然日粗略推，宁可多取。"""
    n = int(n)
    with connect() as conn:
        rows = conn.execute(
            "SELECT ts FROM trade_calendar WHERE ts<=? ORDER BY ts DESC LIMIT ?",
            (_d(d), n + 1),
        ).fetchall()
    if len(rows) > n:
        return rows[n]["ts"]
    back = datetime.strptime(_d(d), "%Y-%m-%d") - timedelta(days=n * 2 + 40)
    return back.strftime("%Y-%m-%d")


# ------------------------------------------------------------------ 建账户


def create_accounts(strategy_id: int, codes: list[str], period: str = "daily",
                    start_date: str = "", initial_cash: float | None = None,
                    strategy_name: str = "") -> dict:
    """给一批股票各建一个独立模拟账户（每只独立资金、全仓进出，和回测同口径）。

    策略 DSL 与费率都在这一刻**拍快照**存进账户：之后改策略、改费率都不影响已有账户，
    否则账户中途换规则，前后两段盈亏就是两套逻辑拼出来的，没法解释也没法对照回测。
    """
    with connect() as conn:
        row = conn.execute("SELECT * FROM strategy WHERE id=?", (int(strategy_id),)).fetchone()
    if not row:
        raise input_error("STRATEGY_NOT_FOUND", f"找不到策略 {strategy_id}。", field="strategy_id")

    validated = dsl_mod.validate(loads(row["dsl"], {}))
    fee = get_fee_config()
    fee_snapshot = {k: fee[k] for k in ("commission_rate", "commission_min",
                                        "stamp_duty_rate", "transfer_fee_rate", "slippage")}
    if initial_cash is None:
        initial_cash = float(get_setting("initial_cash", float) or 100000)
    initial_cash = round_money(float(initial_cash))
    if initial_cash <= 0:
        raise input_error("BAD_CASH", "初始资金必须大于 0。", field="initial_cash")

    start = _d(start_date) or store.today()
    name = strategy_name or row["name"]
    stamp = now()
    fresh = dumps(initial_state(initial_cash))

    created, refused = [], []
    for code in codes:
        with connect() as conn:
            dup = conn.execute(
                "SELECT id FROM sim_account WHERE strategy_id=? AND code=? AND status='running'",
                (int(strategy_id), code),
            ).fetchone()
            stock = conn.execute("SELECT name FROM stock_basic WHERE code=?", (code,)).fetchone()
        if dup:
            refused.append({"code": code,
                            "reason": f"这只股票已经有一个在跑的模拟账户（#{dup['id']}）"})
            continue
        stock_name = stock["name"] if stock else code
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO sim_account(strategy_id, strategy_name, code, name, period, "
                "initial_cash, dsl, fee_snapshot, status, start_date, settled_to, state, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(strategy_id), name, code, stock_name, period, initial_cash,
                 dumps(validated), dumps(fee_snapshot), "running", start, None, fresh,
                 stamp, stamp),
            )
            created.append({"id": cur.lastrowid, "code": code, "name": stock_name})
    return {"created": created, "refused": refused, "start_date": start,
            "initial_cash": initial_cash, "strategy_name": name}


# ------------------------------------------------------------------ 结算


def load_account(account_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sim_account WHERE id=?", (int(account_id),)).fetchone()
    if not row:
        raise input_error("SIM_NOT_FOUND", f"找不到模拟账户 {account_id}。", field="account_id")
    return dict(row)


def settle_account(account_id: int, trigger: str = "manual", upto: str = "",
                   max_bars: int = 0) -> dict:
    """把一个账户结算到 `upto`（默认最后一个已定盘的交易日）。

    返回 {status, bars, trades, from_ts, to_ts, message}。
    status: ok（结算了）/ idle（没有新K线）/ skipped（账户不在运行）/ error（失败，游标没动）。
    """
    aid = int(account_id)
    with _account_lock(aid):
        row = load_account(aid)
        if row["status"] != "running":
            return {"status": "skipped", "bars": 0, "trades": 0,
                    "message": f"账户已{row['status']}，不结算"}
        try:
            return _settle(aid, row, trigger, upto, max_bars)
        except AppError as exc:
            # 取不到数据、暖机不够这类问题要变成账户上的一条状态，而不是让整个页面 500 ——
            # 用户打开页面时是一次性结算所有账户的，一只股票出问题不该拖垮其他几只。
            return _fail(aid, trigger, exc.code, exc.message)


def _settle(aid: int, row: dict, trigger: str, upto: str, max_bars: int) -> dict:
    cutoff = _d(upto) or settle_cutoff()
    floor = _d(row["settled_to"])
    start = _d(row["start_date"])
    if cutoff < start:
        return {"status": "idle", "bars": 0, "trades": 0, "to_ts": cutoff,
                "message": f"还没到开始跟踪的日期（{start}）"}

    validated = dsl_mod.validate(loads(row["dsl"], {}))
    fee = loads(row["fee_snapshot"], {}) or get_fee_config()
    period = row["period"] or "daily"
    warmup = dsl_mod.warmup_bars(validated)

    # 取数窗口锚在"已经结算到哪"，往前多要 warmup 根，指标才算得出来。
    # 锚点跟着游标走，窗口就不会随时间无限变长。周/月线一根顶好几根日线，跨度要放大。
    span = (warmup + LOOKBACK_MARGIN) * PERIOD_SPAN.get(period, 1)
    lookback = back_n_trading_days(floor or start, span)
    df = store.get_kline(row["code"], period, lookback, cutoff, ensure=True)
    if df is None or not len(df):
        return _fail(aid, trigger, "NO_KLINE",
                     f"{row['code']} 在 {lookback}~{cutoff} 没有取到K线")

    runner = make_runner(row["code"], row["name"] or row["code"], validated, df,
                         fee, float(row["initial_cash"]), period)
    bars, ts_list, ev = runner.prepare(df)
    runner.restore(loads(row["state"], {}))

    # 要结算的那几根：在开始日期之后、且游标之前没处理过
    todo = [i for i, t in enumerate(ts_list) if t >= start and t > floor]
    if not todo:
        return {"status": "idle", "bars": 0, "trades": 0, "to_ts": cutoff,
                "message": f"没有新K线（已结算至 {floor or '未开始'}，数据至 {ts_list[-1]}）"}

    # fail-closed：暖机不够就别结算。硬着头皮跑下去，每一天都会被记成「数据不足·跳过」，
    # 账户永远不动 —— 而且看起来完全不像出错，这是最难查的一种静默失效。
    if todo[0] < warmup:
        return _fail(
            aid, trigger, "WARMUP_SHORT",
            f"历史数据不够：这套策略的指标需要至少 {warmup} 根K线暖机，"
            f"但 {ts_list[todo[0]]} 之前只有 {todo[0]} 根。",
        )

    if max_bars and len(todo) > int(max_bars):
        todo = todo[:int(max_bars)]

    for i in todo:
        runner.step_bar(i, bars, ts_list[i], ev)

    _persist(aid, trigger, runner, bars, todo, ts_list, floor)
    return {
        "status": "ok",
        "bars": len(todo),
        "trades": len(runner.trades),
        "from_ts": ts_list[todo[0]],
        "to_ts": ts_list[todo[-1]],
        "message": f"结算 {len(todo)} 个交易日，成交 {len(runner.trades)} 笔",
    }


def _persist(aid: int, trigger: str, runner, bars, todo: list[int], ts_list: list[str],
             floor: str) -> None:
    """把这一批的结果写进库。**一个事务**：要么全成，要么全不算。"""
    stamp = now()
    prev_equity = _last_equity(aid, floor)

    eq_rows, sig_rows, trade_rows = [], [], []
    # runner 是这次结算新建的，equity / signal_log 里就只有 todo 这几根，按位置一一对应
    for k, i in enumerate(todo):
        ts = ts_list[i]
        e = runner.equity[k]
        close = round_money(float(bars.iloc[i]["close"]))
        base = prev_equity if prev_equity is not None else runner.initial_cash
        day_pnl = round_money(e["equity"] - base)
        eq_rows.append((aid, ts, close, e["cash"], e["position"], e["equity"], day_pnl,
                        round_money(day_pnl / base * 100.0, 4) if base else 0.0))
        prev_equity = e["equity"]

    for s in runner.signal_log:
        sig_rows.append((aid, s["ts"], int(s["buy"]), int(s["sell"]), int(s["insufficient"]),
                         s.get("action"), s.get("skip_reason"), stamp))

    for t in runner.trades:
        trade_rows.append((aid, t["ts"], t.get("from_ts"), t["side"], t.get("kind") or "signal",
                           t["price"], t["shares"], t["amount"], t.get("pnl"),
                           dumps(t.get("fees") or {}), t.get("reason"), stamp))

    with connect() as conn:
        # 幂等兜底：游标之后的旧记录先清掉。正常一行都删不到（失败的批次已经回滚了），
        # 但万一有半截数据残留，重算会覆盖它而不是叠出第二份。
        # floor 为空说明这是第一次结算，此时表里本来就没有这个账户的行，不能拿 ts>''
        # 去删 —— 那会匹配到所有行。
        if floor:
            for table in ("sim_trade", "sim_equity", "sim_signal"):
                conn.execute(f"DELETE FROM {table} WHERE account_id=? AND ts>?", (aid, floor))
        conn.executemany(
            "INSERT OR REPLACE INTO sim_equity(account_id, ts, close, cash, position, equity, "
            "day_pnl, day_pnl_pct) VALUES(?,?,?,?,?,?,?,?)", eq_rows)
        conn.executemany(
            "INSERT OR REPLACE INTO sim_signal(account_id, ts, buy, sell, insufficient, "
            "action, skip_reason, settled_at) VALUES(?,?,?,?,?,?,?,?)", sig_rows)
        conn.executemany(
            "INSERT INTO sim_trade(account_id, ts, from_ts, side, kind, price, shares, amount, "
            "pnl, fees, reason, settled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", trade_rows)
        conn.execute(
            "UPDATE sim_account SET state=?, settled_to=?, last_error=NULL, updated_at=? "
            "WHERE id=?",
            (dumps(runner.snapshot()), ts_list[todo[-1]], stamp, aid),
        )
        conn.execute(
            "INSERT INTO sim_settlement(account_id, trigger, from_ts, to_ts, bars, trades, "
            "status, message, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (aid, trigger, ts_list[todo[0]], ts_list[todo[-1]], len(todo), len(trade_rows),
             "ok", "", stamp),
        )


def _last_equity(aid: int, floor: str) -> float | None:
    """游标那天（含）的总资产，用来算新一天的「今日盈亏」。没有就返回 None。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT equity FROM sim_equity WHERE account_id=? AND ts<=? "
            "ORDER BY ts DESC LIMIT 1", (aid, floor or "9999-12-31"),
        ).fetchone()
    return float(row["equity"]) if row else None


def _fail(aid: int, trigger: str, code: str, message: str) -> dict:
    """结算失败：记下来，但**游标不动** —— 下次重跑还是从同一个地方开始。"""
    stamp = now()
    with connect() as conn:
        conn.execute("UPDATE sim_account SET last_error=?, updated_at=? WHERE id=?",
                     (message, stamp, aid))
        conn.execute(
            "INSERT INTO sim_settlement(account_id, trigger, from_ts, to_ts, bars, trades, "
            "status, message, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (aid, trigger, None, None, 0, 0, "error", f"{code}: {message}", stamp),
        )
    log.warning("模拟账户 %s 结算失败 %s: %s", aid, code, message)
    return {"status": "error", "bars": 0, "trades": 0, "error_code": code, "message": message}


def settle_all(trigger: str = "schedule", upto: str = "") -> dict:
    """结算所有在跑的账户。一个账户出错不影响其他账户。"""
    with connect() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM sim_account WHERE status='running' ORDER BY id").fetchall()]
    if not ids:
        return {"accounts": 0, "settled": 0, "results": []}
    if not _tick.acquire(blocking=False):
        # 上一轮定时结算还没跑完（可能在等网络）。跳过这一轮，别叠上去。
        return {"accounts": len(ids), "settled": 0, "results": [], "skipped": "busy"}
    try:
        results = []
        for aid in ids:
            try:
                r = settle_account(aid, trigger=trigger, upto=upto)
            except Exception as exc:  # noqa: BLE001 - 一个账户的意外不能拖垮整轮
                log.exception("模拟账户 %s 结算异常", aid)
                r = _fail(aid, trigger, "RUNTIME_ERROR", str(exc))
            r["account_id"] = aid
            results.append(r)
        return {"accounts": len(ids),
                "settled": sum(1 for r in results if r["status"] == "ok"),
                "results": results}
    finally:
        _tick.release()


# ------------------------------------------------------------------ 查询


def metrics_of(account_id: int) -> dict:
    """把库里的资产曲线和成交拼回 BacktestResult，交给回测那套指标函数算。

    不在这里另写一份夏普/回撤公式：模拟盘的指标必须和回测页的指标是同一个定义，
    否则"回测年化 20%、模拟盘年化 8%"这种对比就没有意义了。
    """
    aid = int(account_id)
    row = load_account(aid)
    with connect() as conn:
        eq = [dict(r) for r in conn.execute(
            "SELECT ts, equity, cash, position FROM sim_equity WHERE account_id=? ORDER BY ts",
            (aid,)).fetchall()]
        trades = [dict(r) for r in conn.execute(
            "SELECT ts, side, kind, price, shares, amount, pnl, reason, from_ts "
            "FROM sim_trade WHERE account_id=? ORDER BY ts, id", (aid,)).fetchall()]
    for t in trades:
        t["fees"] = {}
    fee = loads(row["fee_snapshot"], {}) or {}
    result = BacktestResult(
        code=row["code"], name=row["name"] or row["code"], period=row["period"],
        initial_cash=float(row["initial_cash"]), dsl=loads(row["dsl"], {}), fee=fee,
        trades=trades, equity=eq,
        final_equity=float(eq[-1]["equity"]) if eq else float(row["initial_cash"]),
    )
    m = metrics_mod.compute(result, None, fee)
    m["settled_to"] = row["settled_to"]
    m["bars_settled"] = len(eq)
    return m


def account_view(row: dict) -> dict:
    """列表页要的一行：账户 + 当前持仓 + 最新资产 + 今日盈亏 + 数据新鲜度。"""
    aid = int(row["id"])
    state = loads(row["state"], {}) or {}
    shares = int(state.get("shares") or 0)
    cost_price = float(state.get("cost_price") or 0.0)
    with connect() as conn:
        last = conn.execute(
            "SELECT * FROM sim_equity WHERE account_id=? ORDER BY ts DESC LIMIT 1", (aid,)
        ).fetchone()
        n_trades = int(conn.execute(
            "SELECT COUNT(*) FROM sim_trade WHERE account_id=?", (aid,)).fetchone()[0])
        n_signals = int(conn.execute(
            "SELECT COUNT(*) FROM sim_signal WHERE account_id=? AND (buy OR sell)", (aid,)
        ).fetchone()[0])
        last_settle = conn.execute(
            "SELECT trigger, status, created_at, message FROM sim_settlement "
            "WHERE account_id=? ORDER BY id DESC LIMIT 1", (aid,)).fetchone()

    initial = float(row["initial_cash"])
    equity = float(last["equity"]) if last else initial
    close = float(last["close"]) if last else 0.0
    cutoff = settle_cutoff()
    return {
        "id": aid,
        "strategy_id": row["strategy_id"],
        "strategy_name": row["strategy_name"],
        "code": row["code"],
        "name": row["name"],
        "period": row["period"],
        "status": row["status"],
        "initial_cash": initial,
        "start_date": row["start_date"],
        "settled_to": row["settled_to"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "equity": equity,
        "cash": float(state.get("cash") or 0.0),
        "position": float(last["position"]) if last else 0.0,
        "shares": shares,
        "cost_price": cost_price,
        "last_close": close,
        "holding": shares > 0,
        "hold_days": int(state.get("hold_days") or 0),
        "unrealized_pnl": round_money((close - cost_price) * shares) if shares and close else 0.0,
        "unrealized_pct": round_money((close / cost_price - 1.0) * 100.0, 2)
                         if shares and cost_price else 0.0,
        "total_return_pct": round_money((equity / initial - 1.0) * 100.0, 2) if initial else 0.0,
        "day_pnl": float(last["day_pnl"]) if last else 0.0,
        "day_pnl_pct": float(last["day_pnl_pct"]) if last else 0.0,
        "trade_count": n_trades,
        "signal_count": n_signals,
        "pending_order": state.get("pending"),
        # 数据跟上了没有。用户一周才打开一次，得让他一眼看出"这是上周的数"。
        "stale": bool(row["settled_to"]) and _d(row["settled_to"]) < cutoff,
        "cutoff": cutoff,
        "last_settle": dict(last_settle) if last_settle else None,
    }


def list_accounts(status: str = "") -> list[dict]:
    sql = "SELECT * FROM sim_account"
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY status, updated_at DESC, id DESC"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return [account_view(r) for r in rows]


def get_account(account_id: int, limit: int = 200) -> dict:
    aid = int(account_id)
    row = load_account(aid)
    view = account_view(row)
    with connect() as conn:
        view["equity_curve"] = [dict(r) for r in conn.execute(
            "SELECT ts, close, cash, position, equity, day_pnl, day_pnl_pct "
            "FROM sim_equity WHERE account_id=? ORDER BY ts", (aid,)).fetchall()]
        view["trades"] = [dict(r) for r in conn.execute(
            "SELECT id, ts, from_ts, side, kind, price, shares, amount, pnl, fees, reason "
            "FROM sim_trade WHERE account_id=? ORDER BY ts, id", (aid,)).fetchall()]
        view["signals"] = [dict(r) for r in conn.execute(
            "SELECT ts, buy, sell, insufficient, action, skip_reason FROM sim_signal "
            "WHERE account_id=? ORDER BY ts DESC LIMIT ?", (aid, int(limit))).fetchall()]
        view["settlements"] = [dict(r) for r in conn.execute(
            "SELECT id, trigger, from_ts, to_ts, bars, trades, status, message, created_at "
            "FROM sim_settlement WHERE account_id=? ORDER BY id DESC LIMIT 20", (aid,)).fetchall()]
    for t in view["trades"]:
        t["fees"] = loads(t.get("fees"), {})
    view["dsl"] = loads(row["dsl"], {})
    view["fee_snapshot"] = loads(row["fee_snapshot"], {})
    return view


def set_status(account_id: int, status: str) -> dict:
    """暂停 / 恢复 / 平仓。平仓只是停止结算，历史数据全部保留。"""
    if status not in STATUS:
        raise input_error("BAD_STATUS",
                          f"不认识的状态 {status}，可选：{'、'.join(STATUS)}。", field="status")
    row = load_account(account_id)
    if row["status"] == "closed" and status != "closed":
        raise input_error("ALREADY_CLOSED", "账户已经平仓，不能再改状态。", field="status")
    with _account_lock(int(account_id)):
        with connect() as conn:
            conn.execute("UPDATE sim_account SET status=?, updated_at=? WHERE id=?",
                         (status, now(), int(account_id)))
    return {"id": int(account_id), "status": status}


def delete_account(account_id: int) -> dict:
    """删掉账户和它的全部记录。策略、回测、行情缓存都不动。"""
    aid = int(account_id)
    load_account(aid)
    with _account_lock(aid):
        with connect() as conn:
            for table in ("sim_trade", "sim_equity", "sim_signal", "sim_settlement"):
                conn.execute(f"DELETE FROM {table} WHERE account_id=?", (aid,))
            conn.execute("DELETE FROM sim_account WHERE id=?", (aid,))
    with _acct_locks_guard:
        _acct_locks.pop(aid, None)
    return {"deleted": aid}


# ------------------------------------------------------------------ 后台定时结算


def start_scheduler() -> threading.Thread | None:
    """启动后台定时结算。自检里必须关掉（SIM_SCHEDULER=off），否则会偷偷联网。"""
    global _thread
    if os.environ.get("SIM_SCHEDULER", "on") == "off":
        return None
    if _thread is not None and _thread.is_alive():
        return _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="sim-scheduler", daemon=True)
    _thread.start()
    log.info("模拟盘定时结算已启动，每 %s 分钟检查一次", SCHEDULE_MINUTES)
    return _thread


def stop_scheduler() -> None:
    _stop.set()


def _loop() -> None:
    # 第一次也等满一个间隔再跑：启动瞬间就去联网取数，会和"用户打开页面"抢带宽
    while not _stop.wait(SCHEDULE_MINUTES * 60):
        try:
            out = settle_all(trigger="schedule")
            if out.get("settled"):
                log.info("定时结算完成：%s 个账户", out["settled"])
        except Exception:  # noqa: BLE001 - 定时器绝不能因为一次失败就停掉
            log.exception("定时结算异常")


def scheduler_state() -> dict:
    alive = _thread is not None and _thread.is_alive()
    with connect() as conn:
        row = conn.execute(
            "SELECT account_id, trigger, status, created_at FROM sim_settlement "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        n = int(conn.execute(
            "SELECT COUNT(*) FROM sim_account WHERE status='running'").fetchone()[0])
    return {
        "enabled": os.environ.get("SIM_SCHEDULER", "on") != "off",
        "running": alive,
        "interval_minutes": SCHEDULE_MINUTES,
        "close_at": "%02d:%02d" % CLOSE_AT,
        "cutoff": settle_cutoff(),
        "running_accounts": n,
        "last_settlement": dict(row) if row else None,
    }
