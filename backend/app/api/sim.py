"""实时模拟盘接口。

页面打开时的调用顺序是：**先 GET /accounts 把库里的现状画出来，再 POST /settle 补算**。
反过来做的话，取数一旦慢下来（或者断了网），用户会盯着一页空白等 —— 而库里的数据
虽然是几天前的，也远比空白有用。`stale` 字段就是让前端能标出"这是上周的数"。
"""

from fastapi import APIRouter, Body, Query

from .. import sim
from .. import store
from ..db import connect
from ..errors import input_error
from .common import check_codes, check_date, check_period

router = APIRouter()


@router.post("/sim/accounts")
def create(payload: dict = Body(...)) -> dict:
    """建模拟账户。一只股票一个独立账户，和回测同口径。

    建完立刻结算一次：如果今天已经收盘、数据也拿得到，用户当场就能看到第一行资产，
    而不是一个空账户等到明天。结算失败不影响建账户（错误记在账户的 last_error 上）。
    """
    strategy_id = payload.get("strategy_id")
    if not strategy_id:
        raise input_error("NO_STRATEGY", "没有指定策略。", field="strategy_id")
    try:
        strategy_id = int(strategy_id)
    except (TypeError, ValueError):
        raise input_error("BAD_STRATEGY", f"策略编号 {strategy_id} 不是整数。",
                          field="strategy_id") from None

    codes = check_codes(payload.get("codes", []))
    period = check_period(payload.get("period", "daily"))

    start = payload.get("start_date") or ""
    if start:
        start = check_date(start, "start_date")
        if start > store.today():
            raise input_error("FUTURE_START", "开始跟踪的日期不能晚于今天。", field="start_date")
    cash = payload.get("initial_cash")
    if cash is not None:
        try:
            cash = float(cash)
        except (TypeError, ValueError):
            raise input_error("BAD_CASH", f"初始资金 {cash} 不是数字。", field="initial_cash") from None

    out = sim.create_accounts(strategy_id, codes, period=period, start_date=start,
                              initial_cash=cash)
    if not out["created"]:
        reasons = "；".join(f"{r['code']}：{r['reason']}" for r in out["refused"])
        raise input_error("ALL_DUPLICATED", f"一个账户都没建成。{reasons}", field="codes")

    # 建完就结算一次，让页面立刻有东西可看。逐个来，失败的记在账户上，不整批报错。
    results = [sim.settle_account(a["id"], trigger="create") for a in out["created"]]
    return {**out, "settle": results}


@router.get("/sim/accounts")
def list_accounts(status: str = Query("", pattern="^(|running|paused|closed)$")) -> dict:
    """账户列表。不触发结算 —— 页面先拿这个把界面画出来。"""
    return {"items": sim.list_accounts(status), "cutoff": sim.settle_cutoff()}


@router.post("/sim/settle")
def settle_all(payload: dict | None = Body(default=None)) -> dict:
    """把所有在跑的账户补算到最后一个已定盘的交易日。

    用户可能一周才打开一次，这一次要把中间漏掉的交易日全部按顺序补上，
    包括跨天的委托顺延（信号日跌停卖不出 → 委托活到下一个交易日成交）。
    """
    upto = str((payload or {}).get("upto") or "")
    if upto:
        upto = check_date(upto, "date")
    return sim.settle_all(trigger="manual", upto=upto)


@router.post("/sim/accounts/{account_id}/settle")
def settle_one(account_id: int, payload: dict | None = Body(default=None)) -> dict:
    upto = str((payload or {}).get("upto") or "")
    if upto:
        upto = check_date(upto, "date")
    out = sim.settle_account(int(account_id), trigger="manual", upto=upto)
    return {"account_id": int(account_id), **out}


@router.get("/sim/accounts/{account_id}")
def get_account(account_id: int, limit: int = Query(200, ge=1, le=2000)) -> dict:
    return sim.get_account(int(account_id), limit=limit)


@router.get("/sim/accounts/{account_id}/metrics")
def get_metrics(account_id: int) -> dict:
    """绩效指标。走的是回测那同一套公式，两边的数字才可比。"""
    return sim.metrics_of(int(account_id))


@router.get("/sim/accounts/{account_id}/signals")
def get_signals(account_id: int, limit: int = Query(100, ge=1, le=2000),
                only_fired: bool = Query(False)) -> dict:
    """信号日志。**被拒绝的信号也在里面** —— 用户最想知道的往往就是"为什么没卖出去"。"""
    sim.load_account(int(account_id))
    sql = ("SELECT ts, buy, sell, insufficient, action, skip_reason, settled_at "
           "FROM sim_signal WHERE account_id=?")
    if only_fired:
        sql += " AND (buy OR sell)"
    sql += " ORDER BY ts DESC LIMIT ?"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, (int(account_id), int(limit))).fetchall()]
    return {"account_id": int(account_id), "items": rows}


@router.patch("/sim/accounts/{account_id}/status")
def change_status(account_id: int, payload: dict = Body(...)) -> dict:
    status = str(payload.get("status") or "")
    return sim.set_status(int(account_id), status)


@router.delete("/sim/accounts/{account_id}")
def delete_account(account_id: int) -> dict:
    return sim.delete_account(int(account_id))


@router.get("/sim/status")
def status() -> dict:
    """后台定时结算的状态。模拟盘页用它回答"我不一直开着容器，能结算上吗"。"""
    return sim.scheduler_state()
