"""策略增删改查（验收 F20）。

一条原则：**DSL 先过白名单校验才允许进库**。库里没有脏数据，回测时才不必再猜
"这条老策略是不是哪里写错了"。人话预览（/strategies/explain）走同一套校验，
所以前端 300ms 刷出来的那句话和最后真跑出来的是同一个意思。
"""

from typing import Any

from fastapi import APIRouter, Body

from .. import dsl as dsl_mod
from .. import explain
from ..db import connect, dumps, loads, now
from ..errors import input_error
from .common import check_note, check_period, check_strategy_name

router = APIRouter()

SOURCE_KINDS = ("manual", "template", "nl")
ROW_FIELDS = ("id", "name", "note", "period", "source", "nl_text", "created_at", "updated_at")


def _shape(row: dict) -> dict:
    d = loads(row["dsl"], {})
    out = {k: row[k] for k in ROW_FIELDS}
    out["dsl"] = d
    out["explain"] = explain.describe(dsl_mod.validate(d))
    return out


def _insert(name: str, note: str, period: str, dsl_json: str, source: str,
            nl_text: str | None = None) -> dict:
    stamp = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO strategy(name, note, period, dsl, source, nl_text, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (name, note, period, dsl_json, source, nl_text, stamp, stamp),
        )
        # 返回值和 GET 同一个形状（带人话预览、dsl 已解析），前端存完不用再去查一遍
        return _shape(_row(conn, cur.lastrowid))


def _row(conn, sid: int) -> dict:
    row = conn.execute("SELECT * FROM strategy WHERE id=?", (int(sid),)).fetchone()
    if row is None:
        raise input_error("STRATEGY_NOT_FOUND",
                          f"找不到 id={sid} 这条策略，可能已经被删掉了。", field="id")
    return dict(row)


@router.get("/strategies")
def list_strategies() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM strategy ORDER BY updated_at DESC, id DESC").fetchall()
    return {"items": [_shape(dict(r)) for r in rows]}


@router.post("/strategies/explain")
def explain_only(dsl: Any = Body(..., embed=True)) -> dict:
    """前端条件构建器每次改动都调它。校验不通过就报错，不猜用户想写什么。"""
    return {"explain": explain.describe(dsl_mod.validate(dsl))}


@router.get("/strategies/{sid}")
def get_strategy(sid: int) -> dict:
    with connect() as conn:
        return _shape(_row(conn, sid))


@router.post("/strategies")
def create_strategy(name: str = Body(..., embed=True),
                    dsl: Any = Body(..., embed=True),
                    note: str = Body("", embed=True),
                    period: str = Body("daily", embed=True),
                    source: str = Body("manual", embed=True),
                    nl_text: str = Body("", embed=True)) -> dict:
    clean = dsl_mod.validate(dsl)                     # 先校验 DSL，再校验外壳：规则写错比名字写错更该先报
    if source not in SOURCE_KINDS:
        raise input_error("BAD_SOURCE", f"策略来源只能是 {'/'.join(SOURCE_KINDS)}。", field="source")
    return _insert(check_strategy_name(name), check_note(note), check_period(period),
                   dumps(clean), source, nl_text or None)


@router.put("/strategies/{sid}")
def update_strategy(sid: int, name: str = Body(..., embed=True),
                    dsl: Any = Body(..., embed=True),
                    note: str = Body("", embed=True),
                    period: str = Body("daily", embed=True)) -> dict:
    clean = dsl_mod.validate(dsl)
    with connect() as conn:
        _row(conn, sid)                         # 不存在就直接报错，不要静默改 0 行
        conn.execute(
            "UPDATE strategy SET name=?, note=?, period=?, dsl=?, updated_at=? WHERE id=?",
            (check_strategy_name(name), check_note(note), check_period(period),
             dumps(clean), now(), int(sid)),
        )
        return _shape(_row(conn, sid))


@router.post("/strategies/{sid}/duplicate")
def duplicate(sid: int) -> dict:
    """复制一份（验收 F20）：改参数前的后悔药。"""
    with connect() as conn:
        row = _row(conn, sid)
    return _insert(f"{row['name']} 副本", row["note"] or "", row["period"], row["dsl"],
                   row["source"], row["nl_text"])


@router.delete("/strategies/{sid}")
def delete(sid: int) -> dict:
    """删策略，并级联删掉它的回测记录（前端确认框里那句"N 条回测记录"就是这个数）。"""
    sid = int(sid)
    with connect() as conn:
        _row(conn, sid)
        runs = [r["id"] for r in conn.execute("SELECT id FROM backtest_run WHERE strategy_id=?",
                                             (sid,)).fetchall()]
        n_bt = 0
        if runs:
            marks = ",".join("?" * len(runs))
            n_bt = conn.execute(f"SELECT COUNT(*) c FROM backtest WHERE run_id IN ({marks})",
                                runs).fetchone()["c"]
            conn.execute(f"DELETE FROM backtest WHERE run_id IN ({marks})", runs)
            conn.execute("DELETE FROM backtest_run WHERE strategy_id=?", (sid,))
        conn.execute("DELETE FROM strategy WHERE id=?", (sid,))
    return {"deleted": sid, "runs_removed": len(runs), "backtests_removed": int(n_bt)}


@router.post("/strategies/{sid}/save-as-template")
def save_as_template(sid: int, name: str = Body("", embed=True)) -> dict:
    """把这条策略存成模板。存的是**校验过的 DSL**，载入时不可能带出错的东西进来。"""
    with connect() as conn:
        row = _row(conn, sid)
        tpl_name = check_strategy_name(name or row["name"])
        cur = conn.execute(
            "INSERT INTO template(name, note, period, dsl, from_strategy, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (tpl_name, row["note"] or "", row["period"], row["dsl"], sid, now()),
        )
    return {"id": f"u{cur.lastrowid}", "row_id": int(cur.lastrowid), "name": tpl_name,
            "builtin": False}
