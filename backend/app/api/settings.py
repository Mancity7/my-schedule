"""设置接口：即时保存（无保存按钮）、按组恢复默认、数据源连通性测试。

所有校验都是 fail-closed：不认识的键、超出区间的数值一律拒绝，
绝不"帮忙改成一个能跑的值"——参数是回测结论的一部分，猜错等于结果不可信。
"""

from fastapi import APIRouter, Body

from ..config import (
    DEFAULT_SETTINGS,
    SECRET_SETTINGS,
    SETTING_GROUPS,
    SETTING_RANGES,
    UPSTREAM_NAMES,
)
from ..datasource import LABELS, test_upstream
from ..db import connect, get_settings, set_settings
from ..db import get_state, set_state
from ..errors import input_error

router = APIRouter()

MASK = "***"


def _validate(key: str, raw) -> str:
    """返回规范化后的字符串值，或抛出 input_error。"""
    if key not in DEFAULT_SETTINGS:
        raise input_error("UNKNOWN_SETTING", f"不认识的设置项 {key}。", field=key)
    value = str(raw).strip()

    if key == "datasource_order":
        names = [n.strip() for n in value.split(",") if n.strip()]
        if not names:
            raise input_error("EMPTY_UPSTREAM_ORDER", "至少要保留一个数据源。", field=key)
        bad = [n for n in names if n not in UPSTREAM_NAMES]
        if bad:
            raise input_error("UNKNOWN_UPSTREAM",
                              f"不认识的数据源：{'、'.join(bad)}。可选：{'、'.join(UPSTREAM_NAMES)}",
                              field=key)
        if len(set(names)) != len(names):
            raise input_error("DUP_UPSTREAM", "数据源顺序里有重复项。", field=key)
        return ",".join(names)

    if key == "benchmark":
        if not value.isdigit() or len(value) != 6:
            raise input_error("BAD_BENCHMARK", "基准指数要填 6 位代码，例如 000300。", field=key)
        return value

    if key in SETTING_RANGES:
        try:
            num = float(value)
        except ValueError:
            raise input_error("BAD_NUMBER", f"「{value}」不是数字。", field=key) from None
        lo, hi = SETTING_RANGES[key]
        if not lo <= num <= hi:
            raise input_error("OUT_OF_RANGE",
                              f"这一项要填 {lo} 到 {hi} 之间的数，你填了 {value}。", field=key)
        return value

    return value


@router.get("/settings")
def read_settings() -> dict:
    s = get_settings()
    return {
        "values": {k: (MASK if k in SECRET_SETTINGS and v else v) for k, v in s.items()},
        "defaults": DEFAULT_SETTINGS,
        "ranges": {k: list(v) for k, v in SETTING_RANGES.items()},
        "groups": SETTING_GROUPS,
        "secret_set": {k: bool(s.get(k)) for k in SECRET_SETTINGS},
    }


@router.post("/settings")
def write_settings(items: dict = Body(...)) -> dict:
    if not isinstance(items, dict) or not items:
        raise input_error("EMPTY_SETTINGS", "没有要保存的内容。")
    cleaned = {}
    for k, v in items.items():
        if k in SECRET_SETTINGS and v == MASK:   # 前端回显的掩码，不覆盖真值
            continue
        cleaned[k] = _validate(k, v)
    if cleaned:
        set_settings(cleaned)
    saved = get_settings()
    return {
        "saved": sorted(cleaned),
        "values": {k: (MASK if k in SECRET_SETTINGS and v else v) for k, v in saved.items()},
        "secret_set": {k: bool(saved.get(k)) for k in SECRET_SETTINGS},
    }


@router.post("/settings/reset")
def reset_settings(group: str = Body(..., embed=True)) -> dict:
    """group 为空串 = 恢复全部默认；API Key 不在任何"全部重置"里顺手清掉，得单独清。"""
    group = str(group or "").strip()
    if not group:
        keys = [k for k in DEFAULT_SETTINGS if k not in SECRET_SETTINGS]
    elif group in SETTING_GROUPS:
        keys = SETTING_GROUPS[group]
    else:
        raise input_error("UNKNOWN_GROUP", f"没有这一组设置：{group}", field="group")
    set_settings({k: DEFAULT_SETTINGS[k] for k in keys})
    return {"reset": keys}


@router.get("/settings/keys")
def list_keys() -> dict:
    """给自检和设置页用的白名单键列表。"""
    return {"keys": sorted(DEFAULT_SETTINGS), "groups": SETTING_GROUPS}


@router.post("/datasource/test")
def run_datasource_test(name: str = Body("", embed=True)) -> dict:
    """设置页的 [测试]：真去取一小段数据，而不是只看端口通不通。"""
    names = [name] if name else list(UPSTREAM_NAMES)
    if name and name not in UPSTREAM_NAMES:
        raise input_error("UNKNOWN_UPSTREAM", f"不认识的数据源 {name}", field="name")
    results = [test_upstream(n) for n in names]
    state = get_state("datasource_test") or {}
    merged = {r["name"]: r for r in (state.get("results") or []) if r.get("name")}
    merged.update({r["name"]: r for r in results})
    payload = {"results": list(merged.values()), "order": _order()}
    set_state("datasource_test", payload)
    return payload


def _order() -> list[str]:
    with connect() as conn:
        row = conn.execute("SELECT v FROM settings WHERE k='datasource_order'").fetchone()
    raw = row["v"] if row else DEFAULT_SETTINGS["datasource_order"]
    return [n for n in raw.split(",") if n]


@router.get("/datasource/status")
def datasource_status() -> dict:
    """首页那个数据源小灯：读上次测试结果，绝不为了亮灯去联网。"""
    state = get_state("datasource_test") or {}
    results = {r.get("name"): r for r in state.get("results") or []}
    order = _order()
    usable = [n for n in order if results.get(n, {}).get("ok")]
    return {
        "order": order,
        "labels": LABELS,
        "results": list(results.values()),
        "ok": bool(usable),
        "ok_source": usable[0] if usable else None,
        "checked_at": state.get("_at"),
    }
