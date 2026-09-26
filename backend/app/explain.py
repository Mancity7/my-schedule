"""DSL → 人话。右侧「人话预览」和策略卡片上的规则摘要都出自这里（验收 D2 / F18）。

输入必须是 `dsl.validate()` 之后的结构：本文件只负责说人话，不负责判断合不合法。

一条硬要求：**输出里不能出现裸的英文指标代码**。用户看到的是「5日均线」而不是
「MA(5)」；MACD、RSI 这类盘面通用缩写允许保留，但必须同时带中文（自检会逐条检查）。
"""

from typing import Any

from . import indicators as ind

# 每个指标在句子里该怎么说。{n} 会用实际参数填进去。
SAY = {
    "OPEN": "当天开盘价", "HIGH": "当天最高价", "LOW": "当天最低价", "CLOSE": "当天收盘价",
    "VOLUME": "当天成交量", "AMOUNT": "当天成交额", "TURNOVER": "当天换手率",
    "PCT_CHG": "当天涨跌幅",
    "MA": "{n}日均线", "EMA": "{n}日指数均线", "VOL_MA": "{n}日平均成交量",
    "LIANGBI": "{n}日量比",
    "MACD_DIF": "MACD快线", "MACD_DEA": "MACD慢线", "MACD_HIST": "MACD柱",
    "RSI": "{n}日相对强弱RSI",
    "KDJ_K": "KDJ的K线({n}日)", "KDJ_D": "KDJ的D线({n}日)", "KDJ_J": "KDJ的J线({n}日)",
    "BOLL_MID": "{n}日布林中轨", "BOLL_UP": "{n}日布林上轨", "BOLL_LOW": "{n}日布林下轨",
    "HHV": "前{n}日的最高价", "LLV": "前{n}日的最低价",
}

SAY_NO_ARG = {
    "BOLL_MID": "布林中轨", "BOLL_UP": "布林上轨", "BOLL_LOW": "布林下轨",
    "MA": "均线", "EMA": "指数均线", "VOL_MA": "平均成交量", "LIANGBI": "量比", "RSI": "相对强弱RSI",
}

# 比较符接在句子里的读法：{L} 高于 {R}
SAY_CMP = {
    ">": "高于", "<": "低于", ">=": "不低于", "<=": "不高于", "==": "正好等于",
    "cross_above": "从下往上穿过", "cross_below": "从上方跌破",
}

JOIN = {"and": "，而且", "or": "，或者"}
LOGIC_CN = {"and": "同时满足", "or": "满足任意一条"}

RISK_SENTENCE = {
    "stop_loss_pct": "买入后亏到 {v}% 就卖出止损",
    "take_profit_pct": "赚够 {v}% 就获利卖出",
    "trailing_stop_pct": "从持有期间最高价回落到 {v}% 就卖出（移动止损）",
    "max_hold_days": "拿满 {v} 天不管赚亏都卖出",
}


def _g(v: float) -> str:
    """数字进句子的写法：5.0 → 5，1.5 → 1.5。"""
    f = float(v)
    return f"{f:g}"


def say_operand(op: dict) -> str:
    if op["t"] == "num":
        return f"{_g(op['value'])}"
    name = op["name"]
    args = list(op.get("args") or [])
    tpl = SAY.get(name, name)
    params = ind.spec(name)["params"]
    if args and params:
        text = tpl.format(**{p["name"]: _g(a) for p, a in zip(params, args)})
    elif "{n}" in tpl:
        text = SAY_NO_ARG.get(name, tpl.replace("{n}", ""))
    else:
        text = tpl
    if op.get("mult"):
        # 「5日平均成交量的1.5倍」而不是「×1.5」，用户不用在心里做乘法
        text = f"{text}的{_g(op['mult'])}倍"
    return text


def say_condition(node: dict) -> str:
    left, right = say_operand(node["left"]), say_operand(node["right"])
    cmp_ = node["cmp"]
    if cmp_ == "==":
        text = f"{left}和{right}正好相等"
    else:
        text = f"{left}{SAY_CMP[cmp_]}{right}"
    if node.get("not"):
        text = f"并不满足「{text}」"
    if cmp_ in ("cross_above", "cross_below") and not node.get("not"):
        # 金叉/死叉是用户听得懂的黑话，括注一次帮他建立联系
        text += "（也就是常说的金叉）" if cmp_ == "cross_above" else "（死叉）"
    return text


def _say_node(node: dict, top: bool = True) -> str:
    if node["t"] == "cond":
        return say_condition(node)
    parts = []
    for ch in node["items"]:
        text = _say_node(ch, top=False)
        # 子组内部自己先用「而且/或者」连起来，套进上层时加括号，避免"而且…或者…"读成一团
        if ch["t"] == "group" and len(ch["items"]) > 1:
            text = f"（{text}）"
        parts.append(text)
    return JOIN[node["logic"]].join(parts)


def say_group(node: dict | None) -> list[str]:
    """把一组条件按条目摊开，右侧面板一行一条。"""
    if not node:
        return []
    out = []
    for ch in node["items"]:
        out.append(_say_node(ch, top=False))
    return out


def risk_lines(risk: dict) -> list[str]:
    lines = []
    for key, tpl in RISK_SENTENCE.items():
        v = (risk or {}).get(key)
        if v is not None:
            lines.append(tpl.format(v=_g(v)))
    return lines


def describe(validated: dict) -> dict[str, Any]:
    """DSL → {buy_lines, sell_lines, risk_lines, summary}。"""
    signals = validated["signals"]
    buy = say_group(signals["buy"])
    sell = say_group(signals.get("sell"))
    risk = risk_lines(validated.get("risk"))
    head = f"买入：{len(buy)} 条条件（{LOGIC_CN[signals['buy']['logic']]}）—— "
    parts = []
    if buy:
        parts.append(head + JOIN[signals["buy"]["logic"]].join(buy))
    if sell:
        parts.append("卖出：共 " + str(len(sell)) + f" 条（{LOGIC_CN[signals['sell']['logic']]}）—— "
                     + JOIN[signals["sell"]["logic"]].join(sell))
    if risk:
        parts.append("风控：" + "；".join(risk))
    return {
        "buy_lines": buy,
        "sell_lines": sell,
        "risk_lines": risk,
        "summary": "。".join(parts) + "。" if parts else "还没有填写任何条件。",
    }
