"""6 个内置模板（验收 F19）。名字与说明来自 PRD §3.2，不在这里自由发挥。

存的是**用户在界面上搭出来那种形状**（未校验的原始 DSL），前端载入后可以直接改；
真正要用时一律先过 `dsl.validate()`。每条模板都在自检里跑一遍校验+人话生成。
"""

from typing import Any


def _ind(name: str, *args: int | float, mult: float | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"t": "ind", "name": name, "args": list(args)}
    if mult is not None:
        op["mult"] = mult
    return op


def _num(v: float) -> dict[str, Any]:
    return {"t": "num", "value": v}


def _cond(left: dict, cmp: str, right: dict) -> dict:
    return {"left": left, "cmp": cmp, "right": right}


def _group(logic: str, *items: dict) -> dict:
    return {"logic": logic, "items": list(items)}


TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "ma_cross", "name": "双均线金叉", "period": "daily", "level": 1,
        "note": "短期均线往上穿过长期均线就买，反过来跌破就卖。最经典的趋势跟随，规则一句话说得清。",
        "dsl": {
            "signals": {
                "buy": _group("and", _cond(_ind("MA", 5), "cross_above", _ind("MA", 20))),
                "sell": _group("or", _cond(_ind("MA", 5), "cross_below", _ind("MA", 20))),
            },
            "sizing": {"mode": "all_in"},
            "risk": {"stop_loss_pct": 8, "take_profit_pct": None,
                     "trailing_stop_pct": None, "max_hold_days": None},
        },
    },
    {
        "id": "macd_cross", "name": "MACD金叉", "period": "daily", "level": 1,
        "note": "MACD 快线上穿慢线买入，下穿卖出。和双均线类似，但用的是两条指数均线的差，反应更快。",
        "dsl": {
            "signals": {
                "buy": _group("and", _cond(_ind("MACD_DIF"), "cross_above", _ind("MACD_DEA"))),
                "sell": _group("or", _cond(_ind("MACD_DIF"), "cross_below", _ind("MACD_DEA"))),
            },
            "sizing": {"mode": "all_in"},
            "risk": {"stop_loss_pct": 10, "take_profit_pct": None,
                     "trailing_stop_pct": None, "max_hold_days": None},
        },
    },
    {
        "id": "rsi_rebound", "name": "RSI超卖反弹", "period": "daily", "level": 3,
        "note": "跌到超卖区、而且当天已经收出阳线才进，避免接飞刀。属于逆势打法，止损必须严格执行。",
        "dsl": {
            "signals": {
                "buy": _group(
                    "and",
                    _cond(_ind("RSI", 14), "<", _num(30)),
                    _cond(_ind("CLOSE"), ">", _ind("OPEN")),
                ),
                "sell": _group("or", _cond(_ind("RSI", 14), ">", _num(70))),
            },
            "sizing": {"mode": "all_in"},
            "risk": {"stop_loss_pct": 8, "take_profit_pct": None,
                     "trailing_stop_pct": None, "max_hold_days": 20},
        },
    },
    {
        "id": "boll_break", "name": "布林带突破", "period": "daily", "level": 3,
        "note": "收盘价顶到布林上轨之外、同时放量，认为是强势启动；跌回中轨就离场。震荡市里容易被假突破骗。",
        "dsl": {
            "signals": {
                "buy": _group(
                    "and",
                    _cond(_ind("CLOSE"), ">", _ind("BOLL_UP", 20, 2)),
                    _cond(_ind("VOLUME"), ">", _ind("VOL_MA", 5, mult=1.5)),
                ),
                "sell": _group("or", _cond(_ind("CLOSE"), "<", _ind("BOLL_MID", 20, 2))),
            },
            "sizing": {"mode": "all_in"},
            "risk": {"stop_loss_pct": 8, "take_profit_pct": None,
                     "trailing_stop_pct": None, "max_hold_days": None},
        },
    },
    {
        "id": "new_high_vol", "name": "放量创新高", "period": "daily", "level": 4,
        "note": "成交量放到近期两倍、并且突破前 20 日最高价才追；跌破支撑或跌回月线就走。波动大，仓位别重。",
        "dsl": {
            "signals": {
                "buy": _group(
                    "and",
                    _cond(_ind("HIGH"), ">", _ind("HHV", 20)),
                    _cond(_ind("VOLUME"), ">", _ind("VOL_MA", 5, mult=2)),
                ),
                "sell": _group(
                    "or",
                    _cond(_ind("LOW"), "<", _ind("LLV", 10)),
                    _cond(_ind("CLOSE"), "<", _ind("MA", 20)),
                ),
            },
            "sizing": {"mode": "all_in"},
            "risk": {"stop_loss_pct": 10, "take_profit_pct": None,
                     "trailing_stop_pct": None, "max_hold_days": None},
        },
    },
    {
        "id": "ma_stop", "name": "均线+止损", "period": "daily", "level": 1,
        "note": "只管进场：短均线金叉长均线买入，没有卖出条件，全靠止损 5%、移动止损 8%、拿满 60 天出场。",
        "dsl": {
            "signals": {
                "buy": _group("and", _cond(_ind("MA", 10), "cross_above", _ind("MA", 30))),
                "sell": None,
            },
            "sizing": {"mode": "all_in"},
            "risk": {"stop_loss_pct": 5, "take_profit_pct": None,
                     "trailing_stop_pct": 8, "max_hold_days": 60},
        },
    },
]


def by_id(template_id: str) -> dict[str, Any] | None:
    for t in TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


def public() -> list[dict[str, Any]]:
    """给前端卡片用：内置 6 个 + 用户存为模板的（由接口层合并）。"""
    return [
        {"id": t["id"], "name": t["name"], "note": t["note"], "period": t["period"],
         "level": t["level"], "builtin": True, "dsl": t["dsl"]}
        for t in TEMPLATES
    ]
