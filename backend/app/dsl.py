"""DSL 校验 + 向量化求值。

这一层是整套系统的安全闸门（红线 1）：AI（V2 的 DeepSeek）只能产出这里定义的 JSON，
字段、指标名、运算符全部走白名单，由本文件的固定逻辑解释执行。
**引擎里没有、也不允许有 把字符串当代码跑的那类操作。**

三条判例：
- 未知指标 / 未知运算符 / 参数越界 / 多余字段 / 超过 2 层嵌套 → input_error（400），
  错误里带**具体字段路径**，前端能直接跳到那一格。
- 求值时任何一处操作数是 NaN（暖机不够、停牌缺数据），这一根标记为「数据不足」，
  **既不算满足也不算不满足**（红线 2）。三值逻辑：
      全部满足：有一个不满足就是不满足；没有不满足、但有无从判断的，就是无从判断。
      任一满足：有一个满足就是满足；没有满足、但有无从判断的，就是无从判断。
- 上穿/下破必须左右都是会变化的线。拿固定数字比"上穿"没有意义，直接拒绝。
"""

from typing import Any

import numpy as np
import pandas as pd

from . import indicators as ind
from .errors import input_error

MAX_DEPTH = 2

# 右值形态：另一条指标线 / 固定数字 / 指标×倍数（mult 挂在 ind 上）
CMP: dict[str, dict[str, Any]] = {
    ">": {"cn": "大于", "sign": ">", "line_only": False},
    "<": {"cn": "小于", "sign": "<", "line_only": False},
    ">=": {"cn": "大于等于", "sign": "≥", "line_only": False},
    "<=": {"cn": "小于等于", "sign": "≤", "line_only": False},
    "==": {"cn": "等于", "sign": "=", "line_only": False},
    "cross_above": {"cn": "上穿", "sign": "↑", "line_only": True},
    "cross_below": {"cn": "下破", "sign": "↓", "line_only": True},
}

LOGIC = {"and": "全部满足", "or": "任一满足"}

# V1 只做"全仓买入"。留位而不实现，是为了让仓位规则以后能加进来而不改结构。
SIZING_MODES = {"all_in": "买入时全仓"}

# 风控字段：这些依赖持仓成本，无法向量化成一条线，所以不进条件组
RISK_FIELDS: dict[str, dict[str, Any]] = {
    "stop_loss_pct": {"cn": "止损线", "min": 0.1, "max": 100, "unit": "%"},
    "take_profit_pct": {"cn": "止盈线", "min": 0.1, "max": 1000, "unit": "%"},
    "trailing_stop_pct": {"cn": "移动止损", "min": 0.1, "max": 100, "unit": "%"},
    "max_hold_days": {"cn": "最长持有天数", "min": 1, "max": 3650, "unit": "天", "integer": True},
}

TOP_KEYS = {"signals", "sizing", "risk"}
# "t" 是 validate() 规范化时补的类型标记；允许它出现，校验才是幂等的
# （库里存的就是规范化后的形状，读出来还要能再过一遍校验做人话预览）
GROUP_KEYS = {"logic", "items", "t"}
COND_KEYS = {"left", "cmp", "right", "not", "t"}
COND_REQUIRED = {"left", "cmp", "right"}
OPERAND_KEYS = {"ind": {"t", "name", "args", "mult"}, "num": {"t", "value"}}


def _fail(path: str, message: str) -> None:
    raise input_error("DSL_INVALID", message, field=path)


def _require_dict(x: Any, path: str) -> dict:
    if not isinstance(x, dict):
        _fail(path, f"{path} 应该是一个对象，现在给的是 {type(x).__name__}")
    return x


def _check_keys(obj: dict, allowed: set, path: str) -> None:
    for k in obj:
        if k not in allowed:
            _fail(f"{path}.{k}" if path else k,
                  f"{path or '策略'}里有不认识的字段「{k}」。允许的字段是："
                  f"{'、'.join(sorted(allowed))}")


# ------------------------------------------------------------------ 校验


def _validate_operand(obj: Any, path: str, side: str) -> dict:
    obj = _require_dict(obj, path)
    t = obj.get("t")
    if t not in ("ind", "num"):
        _fail(f"{path}.t", f"{path} 的操作数形态只认 ind（指标）或 num（固定数字），给的是 {t!r}")
    _check_keys(obj, OPERAND_KEYS[t], path)
    if t == "num":
        v = obj.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            _fail(f"{path}.value", f"{path}.value 要填数字，你填的是 {v!r}")
        if abs(float(v)) > 1e12:
            _fail(f"{path}.value", f"{path}.value 太大（{v}），这个数在A股行情里不会出现")
        return {"t": "num", "value": float(v)}

    name = obj.get("name")
    if not isinstance(name, str) or name not in ind.INDICATORS:
        _fail(f"{path}.name",
              f"指标 {name!r} 不在白名单里（{side}）。"
              f"只能用：{'、'.join(ind.names())}")
    args = obj.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list):
        _fail(f"{path}.args", f"{path}.args 要写成数组，比如 [20]")
    spec = ind.spec(name)
    err = ind.arg_error(name, args)
    if err:
        _fail(f"{path}.args", f"{side}{err}")
    out: dict[str, Any] = {"t": "ind", "name": name, "args": [
        int(a) if p["integer"] else float(a) for p, a in zip(spec["params"], args)]}
    mult = obj.get("mult")
    if mult is not None:
        if isinstance(mult, bool) or not isinstance(mult, (int, float)):
            _fail(f"{path}.mult", f"{path}.mult 要填数字倍数，你填的是 {mult!r}")
        if not 0 < float(mult) <= 100:
            _fail(f"{path}.mult", f"{path}.mult 要在 0~100 之间（比如 1.5 表示 1.5 倍），"
                                  f"你填的是 {mult}")
        out["mult"] = float(mult)
    return out


def _validate_condition(obj: Any, path: str) -> dict:
    obj = _require_dict(obj, path)
    _check_keys(obj, COND_KEYS, path)
    for k in COND_REQUIRED:
        if k not in obj:
            _fail(f"{path}.{k}", f"{path} 里少了 {k}（一条条件要有左边、比较符、右边）")
    left = _validate_operand(obj["left"], f"{path}.left", "左边的")
    right = _validate_operand(obj["right"], f"{path}.right", "右边的")
    cmp_ = obj["cmp"]
    if cmp_ not in CMP:
        allowed = "、".join(f"{k}（{v['cn']}）" for k, v in CMP.items())
        _fail(f"{path}.cmp", f"比较符 {cmp_!r} 不认识。只能用：{allowed}")
    if CMP[cmp_]["line_only"]:
        # 「收盘价 上穿 20」这种写法在数学上没有意义：常数线不会上下穿。
        if left["t"] != "ind" or right["t"] != "ind":
            _fail(f"{path}.cmp",
                  f"「{CMP[cmp_]['cn']}」需要左右都是会变化的线（指标），"
                  f"现在{'左边' if left['t'] != 'ind' else '右边'}是固定数字。"
                  f"想比大小请用 > / < / >= / <=")
    not_ = obj.get("not", False)
    if not isinstance(not_, bool):
        _fail(f"{path}.not", f"{path}.not 只能是 true 或 false")
    if obj.get("t") not in (None, "cond"):
        _fail(f"{path}.t", f"{path} 被标成了 {obj['t']!r}，但它有 left/cmp/right，是一条条件")
    return {"t": "cond", "left": left, "cmp": cmp_, "right": right, "not": not_}


def _validate_group(obj: Any, path: str, depth: int) -> dict:
    obj = _require_dict(obj, path)
    _check_keys(obj, GROUP_KEYS, path)
    if obj.get("t") not in (None, "group"):
        _fail(f"{path}.t", f"{path} 被标成了 {obj['t']!r}，但它里面有 logic/items，是条件组")
    logic = obj.get("logic", "and")
    if logic not in LOGIC:
        _fail(f"{path}.logic", f"{path}.logic 只能是 and（全部满足）或 or（任一满足），给的是 {logic!r}")
    items = obj.get("items")
    if not isinstance(items, list) or not items:
        _fail(f"{path}.items", f"{path}.items 至少要有一条条件")
    if depth > MAX_DEPTH:
        _fail(path, f"条件组最多只能嵌套 {MAX_DEPTH} 层，这里已经有 {depth} 层了。"
                    f"再深容易过拟合，也不好理解——请拆成两条规则分开跑")
    out = []
    for i, item in enumerate(items):
        _require_dict(item, f"{path}.items[{i}]")
        kind = set(item)
        if "logic" in kind or "items" in kind:
            out.append(_validate_group(item, f"{path}.items[{i}]", depth + 1))
        else:
            out.append(_validate_condition(item, f"{path}.items[{i}]"))
    return {"t": "group", "logic": logic, "items": out}


def validate(dsl: Any) -> dict:
    """校验并规范化整份 DSL。返回可直接求值的结构；任何不合法都抛 input_error。"""
    dsl = _require_dict(dsl, "策略")
    _check_keys(dsl, TOP_KEYS, "策略")
    signals = _require_dict(dsl.get("signals"), "signals") if "signals" in dsl else None
    if signals is None:
        _fail("signals", "策略里必须有 signals（买卖条件）")
    _check_keys(signals, {"buy", "sell"}, "signals")
    if "buy" not in signals:
        _fail("signals.buy", "至少要有 1 条买入条件")
    buy = _validate_group(signals["buy"], "signals.buy", 1)
    sell = None
    if signals.get("sell") is not None:
        sell = _validate_group(signals["sell"], "signals.sell", 1)

    sizing = _require_dict(dsl.get("sizing", {"mode": "all_in"}), "sizing")
    _check_keys(sizing, {"mode"}, "sizing")
    mode = sizing.get("mode", "all_in")
    if mode not in SIZING_MODES:
        _fail("sizing.mode",
              f"仓位模式 {mode!r} 还不支持。V1 只有 all_in（买入时全仓）")

    risk = _require_dict(dsl.get("risk", {}), "risk")
    _check_keys(risk, set(RISK_FIELDS), "risk")
    clean_risk: dict[str, float | None] = {}
    for k, spec in RISK_FIELDS.items():
        v = risk.get(k)
        if v is None:
            clean_risk[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            _fail(f"risk.{k}", f"{spec['cn']} 要填数字（不想要就留空），你填的是 {v!r}")
        v = float(v)
        if spec.get("integer") and v != int(v):
            _fail(f"risk.{k}", f"{spec['cn']} 要填整数天数，比如 30")
        if not spec["min"] <= v <= spec["max"]:
            _fail(f"risk.{k}",
                  f"{spec['cn']} 要在 {spec['min']:g}~{spec['max']:g} 之间，你填的是 {v:g}")
        clean_risk[k] = int(v) if spec.get("integer") else v

    return {"signals": {"buy": buy, "sell": sell},
            "sizing": {"mode": mode},
            "risk": clean_risk}


# ------------------------------------------------------------------ 求值

# 三值状态用数值表示，方便向量化后直接比大小
YES, NO, UNKNOWN = 1.0, 0.0, -1.0


class EvalResult:
    """一次求值的全部产出：买卖信号 + 数据不足标记 + 溯源用的中间量。

    `buy`/`sell` 只包含"确定满足"的K线；拿不准的那些只在 `insufficient` 里，
    回测引擎会把它们记成「数据不足·跳过」而不是「条件不成立」（验收 F17）。
    """

    def __init__(self, buy: pd.Series, sell: pd.Series, insufficient: pd.Series,
                 lines: pd.DataFrame, nodes: list[dict], warmup: int):
        self.buy = buy
        self.sell = sell
        self.insufficient = insufficient
        self.lines = lines          # 每条操作数线的逐根取值（供溯源/画图）
        self.nodes = nodes          # 求值顺序与结构，溯源按这个走
        self.warmup = warmup        # 这份 DSL 里最长的指标暖机根数

    def summary(self) -> dict:
        return {
            "bars": int(len(self.buy)),
            "buy_signals": int(self.buy.sum()),
            "sell_signals": int(self.sell.sum()),
            "insufficient": int(self.insufficient.sum()),
            "valid_bars": int(len(self.buy) - self.insufficient.sum()),
            "warmup": self.warmup,
        }


def _operand_key(op: dict) -> str:
    if op["t"] == "num":
        return f"num:{op['value']:g}"
    args = ",".join(f"{a:g}" for a in op.get("args", []))
    mult = op.get("mult")
    return f"{op['name']}({args})" + (f"*{mult:g}" if mult else "")


def _operand_series(op: dict, df: pd.DataFrame, cache: dict) -> pd.Series:
    key = _operand_key(op)
    if key not in cache:
        if op["t"] == "num":
            cache[key] = pd.Series(op["value"], index=df.index, dtype="float64")
        else:
            s = ind.compute(df, op["name"], op.get("args", []))
            mult = op.get("mult")
            cache[key] = (s * mult) if mult else s
    return cache[key]


def _compare(a: pd.Series, b: pd.Series, cmp_: str) -> pd.Series:
    """返回三值 Series：满足 YES、不满足 NO、有任何一侧缺数据 UNKNOWN。

    注意 pandas 的比较遇到 NaN 给的是 False，不是 NaN —— 所以「有没有数据」必须
    单独用 notna() 判一次。少了这一步，暖机段会被静默当成"条件不成立"（违反 F17）。
    """
    if cmp_ in ("cross_above", "cross_below"):
        prev_a, prev_b = a.shift(1), b.shift(1)
        ok = a.notna() & b.notna() & prev_a.notna() & prev_b.notna()
        # 上穿 = 前一根还在线上或线下，这一根站上去了。前一根没数据 → 整根无从判断
        if cmp_ == "cross_above":
            hit = (prev_a <= prev_b) & (a > b)
        else:
            hit = (prev_a >= prev_b) & (a < b)
        return pd.Series(np.where(ok, np.where(hit, YES, NO), UNKNOWN), index=a.index)

    func = {">": a.__gt__, "<": a.__lt__, ">=": a.__ge__, "<=": a.__le__, "==": a.__eq__}[cmp_]
    ok = a.notna() & b.notna()
    hit = func(b).astype(bool)
    return pd.Series(np.where(ok, np.where(hit, YES, NO), UNKNOWN), index=a.index)


def _combine(states: list[pd.Series], logic: str) -> pd.Series:
    """三值 AND / OR（向量化，逐根算）。"""
    m = pd.concat(states, axis=1)
    has_unknown = (m == UNKNOWN).any(axis=1)
    if logic == "and":
        # 有一根明确不满足 → 不满足；否则只要有无从判断的就是无从判断
        has_no = (m == NO).any(axis=1)
        return pd.Series(np.where(has_no, NO, np.where(has_unknown, UNKNOWN, YES)), index=m.index)
    has_yes = (m == YES).any(axis=1)
    return pd.Series(np.where(has_yes, YES, np.where(has_unknown, UNKNOWN, NO)), index=m.index)


def _eval_node(node: dict, df: pd.DataFrame, cache: dict, path: str, out: list) -> pd.Series:
    if node["t"] == "group":
        states = [_eval_node(ch, df, cache, f"{path}.items[{i}]", out)
                  for i, ch in enumerate(node["items"])]
        series = _combine(states, node["logic"])
        out.append({"kind": "group", "path": path, "logic": node["logic"],
                    "children": [f"{path}.items[{i}]" for i in range(len(states))],
                    "series": series})
        return series
    left = _operand_series(node["left"], df, cache)
    right = _operand_series(node["right"], df, cache)
    series = _compare(left, right, node["cmp"])
    if node.get("not"):
        # 取反：满足↔不满足，无从判断还是无从判断
        series = series.map({YES: NO, NO: YES, UNKNOWN: UNKNOWN})
    out.append({"kind": "cond", "path": path, "cmp": node["cmp"], "not": node.get("not", False),
                "left": _operand_key(node["left"]), "right": _operand_key(node["right"]),
                "series": series})
    return series


def warmup_bars(dsl: dict) -> int:
    """这份 DSL 需要多少根历史K线才能算出指标（取所有指标里最长的暖机）。

    实时模拟靠它决定取数窗口：只取要结算的那几根，MA60 这类长周期指标全是 NaN，
    每一天都会被记成「数据不足·跳过」，账户永远不动 —— 而且看起来不像出错。
    """
    n = 0
    for name, args in _iter_indicators(dsl):
        n = max(n, ind.warmup(name, args))
    return n


def evaluate(dsl: dict, df: pd.DataFrame) -> EvalResult:
    """把校验过的 DSL 在一段K线上跑出来。dsl 必须先过 validate()。"""
    signals = dsl.get("signals") if isinstance(dsl, dict) else None
    buy_node = signals.get("buy") if isinstance(signals, dict) else None
    if not isinstance(buy_node, dict) or buy_node.get("t") != "group":
        # validate() 会给每个节点补上 t；没有它说明这份 DSL 还没过白名单，
        # 拿出去求值就等于绕过安全闸门，宁可报 400 也不能 KeyError 变 500。
        raise input_error("DSL_NOT_VALIDATED", "求值前必须先过 validate()")
    if df is None or not len(df):
        empty = pd.Series(dtype="bool")
        return EvalResult(empty.rename("buy"), empty.rename("sell"), empty.rename("insufficient"),
                          pd.DataFrame(index=[] if df is None else df.index), [], 0)

    cache: dict[str, pd.Series] = {}
    warmup = warmup_bars(dsl)

    nodes: list[dict] = []
    buy_codes = _eval_node(dsl["signals"]["buy"], df, cache, "signals.buy", nodes)
    sell_node = dsl["signals"].get("sell")
    sell_codes = _eval_node(sell_node, df, cache, "signals.sell", nodes) if sell_node else None

    # 暖机之外的列也要进 lines：溯源那页要显示「当时 MA5 是多少」
    lines = pd.DataFrame(cache)
    unknown = buy_codes == UNKNOWN
    if sell_node is not None:
        unknown = unknown | (sell_codes == UNKNOWN)
    buy = buy_codes == YES
    sell = (sell_codes == YES) if sell_node is not None else pd.Series(False, index=df.index)
    return EvalResult(
        buy.rename("buy").astype(bool),
        sell.rename("sell").astype(bool),
        unknown.rename("insufficient").astype(bool),
        lines, nodes, warmup,
    )


def _iter_indicators(dsl: dict) -> list[tuple[str, list]]:
    """列出 DSL 用到的 (指标名, 参数)，用来算暖机长度。"""
    found: list[tuple[str, list]] = []

    def walk(node):
        if node["t"] == "group":
            for ch in node["items"]:
                walk(ch)
            return
        for side in ("left", "right"):
            op = node[side]
            if op["t"] == "ind":
                found.append((op["name"], op.get("args", [])))

    for side in ("buy", "sell"):
        node = dsl["signals"].get(side)
        if node:
            walk(node)
    return found


def describe_trigger(dsl: dict, side: str, nodes: list[dict], bar_idx: int) -> str:
    """在第 bar_idx 根K线上，哪（几）条条件触发了？返回一句人话。

    用于交易明细的「原因」列：「5日均线上穿20日均线（金叉），而且收盘价高于前日最高价」。
    """
    from . import explain as _explain

    sig = dsl.get("signals", {}).get(side)
    if not sig:
        return "信号触发"

    by_path = {n["path"]: n for n in nodes}

    def _walk(node, path):
        if node["t"] == "cond":
            n = by_path.get(path)
            if n is None:
                return None
            fired = bool(n["series"].iloc[bar_idx] == YES) if bar_idx < len(n["series"]) else False
            return (path, fired)
        if node["t"] == "group":
            child_results = []
            for i, ch in enumerate(node["items"]):
                r = _walk(ch, f"{path}.items[{i}]")
                if r is not None:
                    child_results.append(r)
            return (path, node["logic"], child_results)
        return None

    tree = _walk(sig, f"signals.{side}")
    if tree is None:
        return "信号触发"

    def _describe(node, path, result):
        if node["t"] == "cond":
            return _explain.say_condition(node)
        logic = node["logic"]
        parts = []
        for i, ch in enumerate(node["items"]):
            child_path = f"{path}.items[{i}]"
            child_result = None
            if isinstance(result, tuple) and len(result) >= 3:
                for cr in result[2]:
                    if cr[0] == child_path:
                        child_result = cr
                        break
            if ch["t"] == "cond":
                fired = child_result[1] if child_result else False
                if logic == "and" or (logic == "or" and fired):
                    parts.append(_describe(ch, child_path, child_result))
            elif ch["t"] == "group" and child_result:
                sub = _describe(ch, child_path, child_result)
                if sub:
                    parts.append(f"（{sub}）")
        joiner = "，而且" if logic == "and" else "，或者"
        return joiner.join(parts)

    desc = _describe(sig, f"signals.{side}", tree)
    return desc if desc else "信号触发"


def lines_of(dsl: dict, df: pd.DataFrame) -> dict[str, pd.Series]:
    """只算指标线、不求值。画图和溯源面板用，避免重复计算。"""
    cache: dict[str, pd.Series] = {}
    for side in ("buy", "sell"):
        node = dsl["signals"].get(side)
        if not node:
            continue

        def walk(n):
            if n["t"] == "group":
                for ch in n["items"]:
                    walk(ch)
                return
            for key in ("left", "right"):
                _operand_series(n[key], df, cache)
        walk(node)
    return cache
