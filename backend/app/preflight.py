"""回测前预检：在用户点"开始回测"之后、真正跑之前，快速检查 7 项硬条件。

任何一项不过 → 拒绝运行，返回具体原因。fail-closed（验收 G1/G2）。
预检只做轻量判断（不下载数据、不跑撮合），重活交给后台线程里的 gap_report。
"""

from typing import Any

from . import dsl as dsl_mod
from . import store
from .config import SETTING_RANGES
from .errors import integrity_error


def check(codes: list[str], start: str, end: str, period: str,
          validated: dict, settings: dict) -> list[dict]:
    """返回 7 项预检结果。每项 {name, ok, message}。

    预检项：
    1. 代码合法性（6 位数字、前缀合法）
    2. 日期区间合法性（start <= end，end <= 今天）
    3. 周期合法性（daily/weekly/monthly）
    4. DSL 合法性（已过 validate）
    5. 策略名称非空
    6. 初始资金在合理范围
    7. 数据源顺序合法
    """
    results = []

    # 1. 代码合法性
    for code in codes:
        ok = _valid_code(code)
        results.append({"name": "code_valid", "code": code, "ok": ok,
                        "message": "" if ok else f"{code} 不是合法的A股代码"})

    # 2. 日期区间
    ok = _valid_range(start, end)
    results.append({"name": "date_range", "ok": ok,
                    "message": "" if ok else f"日期区间不合法：{start} ~ {end}"})

    # 3. 周期
    ok = period in store.PERIODS
    results.append({"name": "period", "ok": ok,
                    "message": "" if ok else f"不支持的周期：{period}"})

    # 4. DSL
    try:
        dsl_mod.validate(validated)
        results.append({"name": "dsl_valid", "ok": True, "message": ""})
    except Exception as exc:
        results.append({"name": "dsl_valid", "ok": False, "message": str(exc)})

    # 5. 初始资金
    try:
        cash = float(settings.get("initial_cash", 100000))
        lo, hi = SETTING_RANGES["initial_cash"]
        ok = lo <= cash <= hi
        results.append({"name": "initial_cash", "ok": ok,
                        "message": "" if ok else f"初始资金 {cash} 不在 {lo}~{hi} 范围"})
    except (TypeError, ValueError):
        results.append({"name": "initial_cash", "ok": False, "message": "初始资金不是数字"})

    # 6. 数据源顺序
    ds_order = settings.get("datasource_order", "sina,tencent,eastmoney")
    ok = _valid_datasource_order(ds_order)
    results.append({"name": "datasource_order", "ok": ok,
                    "message": "" if ok else f"数据源顺序不合法：{ds_order}"})

    # 7. 缓存状态（轻量检查：是否有混源）
    for code in codes:
        try:
            store.assert_single_source(code, period)
            results.append({"name": "single_source", "code": code, "ok": True, "message": ""})
        except Exception as exc:
            results.append({"name": "single_source", "code": code, "ok": False,
                            "message": str(exc)})

    return results


def _valid_code(code: str) -> bool:
    c = str(code).strip()
    if len(c) != 6 or not c.isdigit():
        return False
    return c.startswith(("0", "3", "6", "4", "8", "9"))


def _valid_range(start: str, end: str) -> bool:
    if not start or not end:
        return False
    try:
        from datetime import date
        s = date.fromisoformat(str(start)[:10])
        e = date.fromisoformat(str(end)[:10])
        today = date.today()
        return s <= e and e <= today
    except ValueError:
        return False


def _valid_datasource_order(order: str) -> bool:
    from .config import UPSTREAM_NAMES
    parts = [p.strip() for p in str(order).split(",") if p.strip()]
    return all(p in UPSTREAM_NAMES for p in parts) and len(parts) == len(set(parts))
