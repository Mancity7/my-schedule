"""元数据接口：白名单、运算符、模板全部由后端下发。

红线 6：前端不允许硬编码指标表或运算符表 —— 它问这里要，改了设置刷新就生效。
"""

from fastapi import APIRouter

from .. import dsl as dsl_mod
from .. import indicators as ind
from .. import store
from .. import templates as tpl_mod
from ..db import connect, loads

router = APIRouter()

LINE_HINT = "上穿/下破需要左右都是会变化的线（比大小请用大于小于）"


@router.get("/indicators")
def indicators() -> dict:
    return {"items": ind.public_specs(), "count": len(ind.INDICATORS)}


@router.get("/operators")
def operators() -> dict:
    return {
        "compare": [
            {"code": code, "cn": spec["cn"], "symbol": spec["sign"],
             "line_only": spec["line_only"], "hint": LINE_HINT if spec["line_only"] else ""}
            for code, spec in dsl_mod.CMP.items()
        ],
        "logic": [{"code": code, "cn": cn} for code, cn in dsl_mod.LOGIC.items()],
        "operands": [
            {"t": "ind", "cn": "另一个指标", "keys": sorted(dsl_mod.OPERAND_KEYS["ind"])},
            {"t": "num", "cn": "固定数字", "keys": sorted(dsl_mod.OPERAND_KEYS["num"])},
        ],
        "max_depth": dsl_mod.MAX_DEPTH,
        "sizing_modes": [{"mode": m, "cn": cn} for m, cn in dsl_mod.SIZING_MODES.items()],
        "risk_fields": [
            {"key": k, "cn": v["cn"], "min": v["min"], "max": v["max"], "unit": v["unit"],
             "integer": bool(v.get("integer"))}
            for k, v in dsl_mod.RISK_FIELDS.items()
        ],
        "param_rule": {"min": ind.PARAM_MIN, "max": ind.PARAM_MAX, "hint": ind.PARAM_HINT},
        "periods": list(store.PERIODS),
    }


@router.get("/templates")
def templates() -> dict:
    """内置 6 个模板 + 用户[保存为模板]存下来的。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, note, period, dsl, created_at FROM template ORDER BY id"
        ).fetchall()

    items = tpl_mod.public()
    items += [
        {"id": f"u{r['id']}", "name": r["name"], "note": r["note"] or "", "period": r["period"],
         "level": None, "builtin": False, "row_id": r["id"], "created_at": r["created_at"],
         "dsl": loads(r["dsl"], {})}
        for r in rows
    ]
    return {"items": items}


@router.delete("/templates/{row_id}")
def drop_template(row_id: int) -> dict:
    """只能删自己存的模板，内置 6 个删不掉（前端也不会给它们显示删除按钮）。"""
    with connect() as conn:
        cur = conn.execute("DELETE FROM template WHERE id=?", (int(row_id),))
    return {"deleted": int(row_id), "removed": cur.rowcount > 0}
