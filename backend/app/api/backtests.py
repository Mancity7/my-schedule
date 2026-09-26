"""回测接口：提交、查询、取消、溯源、对比、导出。

回测是耗时操作（多只股票 × 多年K线），所以提交后走后台线程，前端轮询进度。
取消用 threading.Event，线程在每根K线之前检查一次。

费用快照：每条回测记录都存当时的费率（fee_snapshot），之后改费率不追溯（验收 K3）。
"""

import json
import threading
import traceback
from typing import Any

from fastapi import APIRouter, Body, Query

from .. import dsl as dsl_mod
from .. import metrics as metrics_mod
from .. import preflight
from .. import store
from ..backtest import gap_report, run_one
from ..config import DEFAULT_SETTINGS
from ..db import connect, dumps, get_fee_config, get_settings, loads, now
from ..errors import input_error, integrity_error, program_error
from .common import check_codes, check_period, check_range, check_strategy_name

router = APIRouter()

JOBS: dict[int, dict] = {}
LOCK = threading.Lock()


# ------------------------------------------------------------------ 提交


@router.post("/backtests")
def submit(payload: dict = Body(...)) -> dict:
    """提交回测任务。后台线程跑，立刻返回 run_id。"""
    strategy_id = payload.get("strategy_id")
    name = payload.get("name", "")
    dsl = payload.get("dsl")
    codes = payload.get("codes", [])
    start = payload.get("start_date", "")
    end = payload.get("end_date", "")
    period = payload.get("period", "daily")
    initial_cash = payload.get("initial_cash")

    if not strategy_id:
        raise input_error("NO_STRATEGY", "没有指定策略。", field="strategy_id")
    if dsl is None:
        raise input_error("NO_DSL", "没有提供策略规则。", field="dsl")

    validated = dsl_mod.validate(dsl)
    codes = check_codes(codes)
    start, end = check_range(start, end)
    period = check_period(period)

    settings = get_settings()
    if initial_cash is None:
        initial_cash = float(settings.get("initial_cash", 100000))
    initial_cash = float(initial_cash)

    fee = get_fee_config()
    fee_snapshot = {k: fee[k] for k in ("commission_rate", "commission_min",
                                         "stamp_duty_rate", "transfer_fee_rate", "slippage")}

    preflight_results = preflight.check(codes, start, end, period, validated, settings)
    preflight_ok = all(r["ok"] for r in preflight_results)
    if not preflight_ok:
        fails = [r for r in preflight_results if not r["ok"]]
        fail_msgs = [r.get("message") or r.get("name", "?") for r in fails]
        raise integrity_error(
            "PREFLIGHT_FAILED",
            f"预检不通过（{len(fails)} 项）：{'；'.join(fail_msgs)}",
            detail=dumps(fails),
        )

    stamp = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO backtest_run(strategy_id, name, period, start_date, end_date, "
            "initial_cash, codes, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (int(strategy_id), name or f"回测 {stamp}", period, start, end,
             initial_cash, dumps(codes), "running", stamp),
        )
        run_id = cur.lastrowid
        for code in codes:
            conn.execute(
                "INSERT INTO backtest(run_id, code, status, created_at) VALUES(?,?,?,?)",
                (run_id, code, "pending", stamp),
            )

    cancel_event = threading.Event()
    JOBS[run_id] = {"status": "running", "cancel": cancel_event, "progress": {}}

    thread = threading.Thread(
        target=_run_worker,
        args=(run_id, codes, name, validated, start, end, period,
              initial_cash, fee_snapshot),
        daemon=True,
    )
    thread.start()

    return {"run_id": run_id, "status": "running", "codes": codes,
            "preflight": preflight_results}


def _run_worker(run_id: int, codes: list[str], name: str, validated: dict,
                start: str, end: str, period: str, initial_cash: float,
                fee_snapshot: dict) -> None:
    """后台线程：逐只股票跑回测，结果落库。"""
    job = JOBS.get(run_id)
    if not job:
        return

    try:
        for i, code in enumerate(codes):
            if job["cancel"].is_set():
                _mark_run(run_id, "cancelled")
                return

            job["progress"] = {"current": i + 1, "total": len(codes), "code": code}

            try:
                df = store.get_kline(code, period, start, end, ensure=True)
                if df is None or not len(df):
                    _mark_bt_error(run_id, code, "K线为空", "EMPTY_KLINE")
                    continue

                settings = get_settings()
                tolerance = float(settings.get("gap_tolerance_pct", 5))
                gap = gap_report(df, start, end, period, tolerance)
                if not gap["ok"]:
                    _mark_bt_error(run_id, code, gap["reason"], "GAP_EXCEEDED",
                                   extra={"gap": gap})
                    continue

                stock_name = _get_stock_name(code)
                result = run_one(code, stock_name, validated, df, fee_snapshot,
                                 initial_cash, period)

                bench_df = None
                shanghai_df = None
                try:
                    bench_code = settings.get("benchmark", "000300")
                    bench_df = store.get_benchmark(bench_code, start, end)
                except Exception:
                    pass
                try:
                    shanghai_df = store.get_benchmark("000001", start, end)
                except Exception:
                    pass

                first_close = float(df.iloc[0]["close"]) if len(df) else None
                last_close = float(df.iloc[-1]["close"]) if len(df) else None

                m = metrics_mod.compute(result, bench_df, fee_snapshot,
                                        shanghai_df=shanghai_df,
                                        stock_first_close=first_close,
                                        stock_last_close=last_close)
                result.final_equity = m["final_equity"]

                _store_bt_result(run_id, code, result, m, fee_snapshot)

            except Exception as exc:
                _mark_bt_error(run_id, code, str(exc), "RUNTIME_ERROR")

        _mark_run(run_id, "done")

    except Exception as exc:
        _mark_run(run_id, "error")
        traceback.print_exc()


def _get_stock_name(code: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT name FROM stock_basic WHERE code=?", (code,)).fetchone()
    return row["name"] if row else code


def _store_bt_result(run_id: int, code: str, result, m: dict, fee_snapshot: dict) -> None:
    payload = result.payload()
    with connect() as conn:
        conn.execute(
            "UPDATE backtest SET status=?, metrics=?, equity=?, trades=?, signals=?, "
            "fee_snapshot=?, quality=?, name=? WHERE run_id=? AND code=?",
            ("done", dumps(m), dumps(payload["equity"]), dumps(payload["trades"]),
             dumps(payload["signal_log"]), dumps(fee_snapshot), dumps(payload["quality"]),
             result.name, run_id, code),
        )


def _mark_bt_error(run_id: int, code: str, message: str, code_: str,
                   extra: dict | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE backtest SET status=?, error=?, error_code=? WHERE run_id=? AND code=?",
            ("error", message, code_, run_id, code),
        )


def _mark_run(run_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE backtest_run SET status=? WHERE id=?", (status, run_id))
    job = JOBS.get(run_id)
    if job:
        job["status"] = status


# ------------------------------------------------------------------ 查询


@router.get("/backtests/pending")
def pending_jobs() -> dict:
    """前端轮询进度。"""
    out = []
    for run_id, job in JOBS.items():
        if job["status"] == "running":
            out.append({"run_id": run_id, **job["progress"]})
    return {"pending": out}


@router.post("/backtests/{run_id}/cancel")
def cancel(run_id: int) -> dict:
    job = JOBS.get(int(run_id))
    if not job:
        raise input_error("JOB_NOT_FOUND", f"找不到 run_id={run_id} 的任务。")
    job["cancel"].set()
    return {"run_id": int(run_id), "status": "cancelling"}


@router.get("/backtests/runs")
def list_runs(limit: int = Query(50, ge=1, le=200)) -> dict:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM backtest_run ORDER BY created_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["codes"] = loads(d.get("codes"), [])
        bts = conn.execute(
            "SELECT id, code, name, status, error, error_code, metrics, created_at "
            "FROM backtest WHERE run_id=?", (d["id"],)
        ).fetchall()
        d["backtests"] = [_bt_summary(dict(b)) for b in bts]
        out.append(d)
    return {"items": out}


def _bt_summary(row: dict) -> dict:
    m = loads(row.get("metrics"), {})
    return {
        "id": row["id"], "code": row["code"], "name": row.get("name", ""),
        "status": row["status"], "error": row.get("error"),
        "error_code": row.get("error_code"),
        "total_return": m.get("total_return"),
        "annual_return": m.get("annual_return"),
        "max_drawdown": m.get("max_drawdown"),
        "sharpe": m.get("sharpe"),
        "total_trades": m.get("total_trades"),
        "win_rate": m.get("win_rate"),
        "final_equity": m.get("final_equity"),
        "created_at": row["created_at"],
    }


@router.get("/backtests/runs/{run_id}")
def get_run(run_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM backtest_run WHERE id=?", (int(run_id),)).fetchone()
        if not row:
            raise input_error("RUN_NOT_FOUND", f"找不到 run_id={run_id}。")
        d = dict(row)
        d["codes"] = loads(d.get("codes"), [])
        bts = conn.execute("SELECT * FROM backtest WHERE run_id=?", (run_id,)).fetchall()
        d["backtests"] = []
        for b in bts:
            bd = dict(b)
            bd["metrics"] = loads(bd.get("metrics"), {})
            bd["equity"] = loads(bd.get("equity"), [])
            bd["trades"] = loads(bd.get("trades"), [])
            bd["signals"] = loads(bd.get("signals"), [])
            bd["fee_snapshot"] = loads(bd.get("fee_snapshot"), {})
            bd["quality"] = loads(bd.get("quality"), {})
            d["backtests"].append(bd)
    return d


@router.get("/backtests/trace")
def get_trace(run_id: int = Query(...), code: str = Query(...),
              ts: str = Query("")) -> dict:
    """单根K线的溯源数据。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT signals FROM backtest WHERE run_id=? AND code=?", (int(run_id), code)
        ).fetchone()
    if not row:
        raise input_error("BT_NOT_FOUND", f"找不到 run_id={run_id} code={code} 的回测。")
    signals = loads(row["signals"], [])
    if ts:
        entry = next((s for s in signals if s.get("ts") == ts), None)
        return {"ts": ts, "signal": entry}
    return {"signals": signals}


@router.post("/backtests/compare")
def compare(run_ids: list[int] = Body(..., embed=True)) -> dict:
    """横向对比多次回测。"""
    items = []
    for rid in run_ids:
        with connect() as conn:
            row = conn.execute("SELECT * FROM backtest_run WHERE id=?", (int(rid),)).fetchone()
            if not row:
                continue
            d = dict(row)
            d["codes"] = loads(d.get("codes"), [])
            bts = conn.execute("SELECT * FROM backtest WHERE run_id=?", (rid,)).fetchall()
            d["backtests"] = []
            for b in bts:
                bd = dict(b)
                bd["metrics"] = loads(bd.get("metrics"), {})
                d["backtests"].append(bd)
            items.append(d)
    return {"items": items}


@router.get("/backtests/export")
def export_csv(run_id: int = Query(...), code: str = Query("")) -> dict:
    """导出回测结果为 CSV 格式（返回结构化数据，前端负责下载）。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM backtest_run WHERE id=?", (int(run_id),)).fetchone()
        if not row:
            raise input_error("RUN_NOT_FOUND", f"找不到 run_id={run_id}。")
        d = dict(row)
        d["codes"] = loads(d.get("codes"), [])
        bts = conn.execute("SELECT * FROM backtest WHERE run_id=?", (run_id,)).fetchall()
        if code:
            bts = [b for b in bts if b["code"] == code]
        d["backtests"] = []
        for b in bts:
            bd = dict(b)
            bd["metrics"] = loads(bd.get("metrics"), {})
            bd["trades"] = loads(bd.get("trades"), [])
            bd["equity"] = loads(bd.get("equity"), [])
            d["backtests"].append(bd)
    return d


@router.delete("/backtests/{bt_id}")
def delete_backtest(bt_id: int) -> dict:
    """删除单条回测记录。"""
    with connect() as conn:
        conn.execute("DELETE FROM backtest WHERE id=?", (int(bt_id),))
    return {"deleted": int(bt_id)}
