"""阶段 3 自检：指标白名单、DSL 校验（安全闸门）、向量化求值、人话预览、策略接口。

对应 PRD 验收 D6/D7/D11/D12/D13、F14-F20、G4。全部离线：指标与求值用合成K线，
不碰行情接口；只有走 `/api/...` 的断言会经过 FastAPI（TestClient，不落正式库）。

为什么这一阶段的断言要写这么细：这套 DSL 是 V2 接 AI 的入口。AI 只能产出这里的
JSON，所以"非法输入一定进不来、NaN 一定不会被当成条件不成立"必须逐条钉死，
将来任何人改动这两处都会立刻红。
"""

import re

import pandas as pd

from .. import datasource, dsl as dsl_mod, explain, indicators as ind, store, templates as tpl
from ..errors import KIND_INPUT, KIND_INTEGRITY
from ..api.common import check_period
from .harness import check, client, eq, fresh_db, near, raises_error, true

CLIENT = None

CN_LOW, CN_HIGH = chr(0x4E00), chr(0x9FFF)


def has_cn(text) -> bool:
    """句子里有没有汉字。人话预览的底线：不能整句都是英文。"""
    return any(CN_LOW <= c <= CN_HIGH for c in str(text))


def api():
    global CLIENT
    if CLIENT is None:
        CLIENT = client()
    return CLIENT


# ------------------------------------------------------------------ 合成K线

ALL_COLS = ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"]


def ts_of(n, first="2025-01-02"):
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(first, periods=n, freq="D")]


def frame(closes, **overrides):
    """按收盘价造一份列齐全的K线；overrides 可以逐列覆盖（例如 high=[.., nan, ..]）。"""
    n = len(closes)
    base = {
        "open": [c * 0.99 for c in closes],
        "high": [c * 1.02 for c in closes],
        "low": [c * 0.98 for c in closes],
        "close": list(closes),
        "volume": [100.0 + i for i in range(n)],
        "amount": [1000.0 + i for i in range(n)],
        "turnover": [0.5] * n,
        "pct_chg": [float("nan")] + [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, n)],
    }
    base.update({k: list(v) for k, v in overrides.items()})
    return pd.DataFrame(base, index=ts_of(n))


def rising(n=200, start=10.0, step=0.05):
    """一路小涨，用来检查指标本身能不能算出来。"""
    return frame([round(start + i * step, 4) for i in range(n)])


def flat_then(closes_flat, rest):
    return frame(list(closes_flat) + list(rest))


# ------------------------------------------------------------------ DSL 构造


def I(name, *args, mult=None):
    op = {"t": "ind", "name": name, "args": list(args)}
    if mult is not None:
        op["mult"] = mult
    return op


def N(v):
    return {"t": "num", "value": v}


def C(left, cmp_, right, **kw):
    d = {"left": left, "cmp": cmp_, "right": right}
    d.update(kw)
    return d


def G(*items, logic="and"):
    return {"logic": logic, "items": list(items)}


def dsl_of(buy, sell=None, risk=None):
    return {"signals": {"buy": buy, "sell": sell}, "risk": risk or {}}


def run(dsl, df):
    return dsl_mod.evaluate(dsl_mod.validate(dsl), df)


def bad_http(payload, label, code_part="DSL_INVALID"):
    """POST /api/strategies/explain，断言它被 400 拒绝并回来看错误码与字段。"""
    resp = api().post("/api/strategies/explain", json=payload)
    eq(resp.status_code, 400, f"{label} · HTTP 状态")
    err = resp.json()["error"]
    eq(err["kind"], KIND_INPUT, f"{label} · 错误分类")
    true(code_part.lower() in err["code"].lower(),
         f"{label} · 错误码应含 {code_part}，实际 {err['code']}")
    return err


# ================================================================== 指标库


@check("3")
def t01_whitelist_is_exactly_the_24_prd_codes():
    """白名单就是能力边界，多一个少一个都要先改 PRD 再改代码。"""
    expected = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "AMOUNT", "TURNOVER", "PCT_CHG",
                "MA", "EMA", "VOL_MA", "LIANGBI", "MACD_DIF", "MACD_DEA", "MACD_HIST",
                "RSI", "KDJ_K", "KDJ_D", "KDJ_J", "HHV", "LLV",
                "BOLL_MID", "BOLL_UP", "BOLL_LOW"]
    eq(ind.names(), expected, "白名单指标（PRD §5.1 同一份、同一顺序）")
    eq(len(ind.INDICATORS), 24, "F14：24 个白名单指标")
    eq(len(ind.public_specs()), 24, "下发给前端的条数")
    canon = set(datasource.CANONICAL) - {"ts"}
    for name, spec in ind.INDICATORS.items():
        true(spec["cn"] and has_cn(spec["cn"]), f"{name} 要有中文名")
        true(set(spec["needs"]) <= canon, f"{name} 依赖了规范列之外的列：{spec['needs']}")
        true(spec["unit"], f"{name} 要标单位")
        true(spec["cat"], f"{name} 要分组（前端下拉按组显示）")
        for key in ("what", "example", "why"):
            true(has_cn(spec["term"][key]), f"{name} 的三段式解释缺「{key}」")
        for p in spec["params"]:
            true(p["min"] > 0, f"{p['name']} 参数下限要大于 0")
            true(p["max"] <= ind.PARAM_MAX, f"{name} 参数范围超出白名单总上限")


@check("3")
def t02_warmup_leaves_nan_never_zero():
    """暖机段留 NaN：填 0 会让「收盘价>MA20」在头 20 根凭空成立（F17 / G4 的根）。"""
    df = frame([1, 2, 3, 4, 5])
    ma3 = ind.compute(df, "MA", [3])
    eq([bool(pd.isna(v)) for v in ma3[:2]], [True, True], "MA(3) 前两根还没有三天数据")
    eq(list(ma3[2:]), [2.0, 3.0, 4.0], "MA(3) = 最近三天收盘平均")
    true(bool(pd.isna(ma3.iloc[1])), "是 NaN，不是 0")
    eq(ind.warmup("MA", [3]), 3, "MA(3) 要 3 根")
    eq(ind.warmup("MA"), 20, "缺参数时按默认值算暖机（只用于预检）")


@check("3")
def t03_every_indicator_reaches_value_within_its_declared_warmup():
    """每条指标线：真实出值的根数必须 <= 自己声明的暖机根数（预检按声明值放行）。"""
    df = rising(200)
    for name in ind.names():
        args = [p["default"] for p in ind.spec(name)["params"]]
        s = ind.compute(df, name, args)
        eq(len(s), len(df), f"{name} 必须与K线等长（不能变短）")
        need = ind.warmup(name, args)
        if need == 0:
            # 原始行情这一列本来就有的缺值（例如第一根没有涨跌幅）不算指标的锅，
            # 但指标自己绝不能再造缺值出来。
            source_missing = max(int(df[c].isna().sum()) for c in ind.spec(name)["needs"])
            eq(int(s.isna().sum()), source_missing, f"{name} 暖机为 0，却自己造了缺值")
            continue
        valid = s.dropna()
        true(len(valid) > 0, f"{name} 在 200 根上完全算不出值")
        bars_needed = int(df.index.get_loc(valid.index[0])) + 1
        true(bars_needed <= need,
             f"{name} 声明暖机 {need} 根，却在第 {bars_needed} 根就出值（会漏标数据不足）")


@check("3")
def t04_macd_hist_is_twice_dif_minus_dea():
    """国内软件的 MACD 柱 = 2×(DIF-DEA)，用国外口径会和盘面对不上。"""
    df = rising(120)
    dif = ind.compute(df, "MACD_DIF", [])
    dea = ind.compute(df, "MACD_DEA", [])
    hist = ind.compute(df, "MACD_HIST", [])
    for i in (40, 80, 119):
        near(hist.iloc[i], 2 * (dif.iloc[i] - dea.iloc[i]), 1e-9, f"第 {i} 根的柱")
    near(dif.iloc[119] - dea.iloc[119], abs(dif.iloc[119] - dea.iloc[119]), 1e-9,
         "一直上涨时 DIF 在 DEA 之上（柱为正）")
    eq([ind.warmup(n) for n in ("MACD_DIF", "MACD_DEA", "MACD_HIST")], [60, 120, 120],
       "递推类指标按收敛程度声明暖机")


@check("3")
def t05_rsi_seed_uses_n_real_changes():
    """Wilder 种子必须用满 n 个真实涨跌幅，且不能比数据本身更早出值。"""
    df = frame([10, 11, 10.5])
    rsi2 = ind.compute(df, "RSI", [2])
    # 涨均 = (1+0)/2 = 0.5，跌均 = (0+0.5)/2 = 0.25 → 100*0.5/0.75
    near(rsi2.iloc[2], 100 * 0.5 / 0.75, 1e-9, "RSI(2) 第三根手算")
    eq([bool(pd.isna(v)) for v in rsi2[:2]], [True, True], "两根K线只有一个涨跌幅，算不了 RSI(2)")
    eq(ind.warmup("RSI", [14]), 15, "RSI(14) 要 15 根（14 个涨跌幅）")
    eq(bool(pd.isna(ind.compute(frame(list(range(1, 15))), "RSI", [14]).iloc[13])), True,
       "只有 14 根时第 14 根仍然是 NaN，不能提前给值")
    near(ind.compute(rising(30), "RSI", [3]).iloc[-1], 100.0, 1e-9, "只涨不跌 → 100")
    near(ind.compute(frame([20 - i for i in range(10)]), "RSI", [3]).iloc[-1], 0.0, 1e-9,
         "只跌不涨 → 0")
    flat = frame([10.0] * 20)
    true(bool(pd.isna(ind.compute(flat, "RSI", [14]).iloc[-1])),
         "横盘到一点波动都没有时 RSI 是算不出来的：标数据不足，不许当成 0 或 100")


@check("3")
def t06_kdj_one_word_board_is_50():
    """一字板（最高=最低）RSV 无定义，国内软件记 50；留 NaN 会把正常交易日当数据不足。"""
    flat = frame([10.0] * 15, open=[10.0] * 15, high=[10.0] * 15, low=[10.0] * 15)
    for name, value in (("KDJ_K", 50.0), ("KDJ_D", 50.0), ("KDJ_J", 50.0)):
        s = ind.compute(flat, name, [9])
        near(s.iloc[12], value, 1e-9, f"一字板的 {name}")


@check("3")
def t07_kdj_recursion_hand_check():
    """K、D 都是从 50 起算的 1/3 递推，必须和手算一致。"""
    n = 6
    df = frame([10.0] * n, high=[10.0] * n, low=[0.0] * n)
    k = ind.compute(df, "KDJ_K", [3])
    d = ind.compute(df, "KDJ_D", [3])
    j = ind.compute(df, "KDJ_J", [3])
    rsv = 100.0
    k2 = 50 * (2 / 3) + rsv / 3                       # 第三根：K 由 50 平滑一次
    d2 = 50 * (2 / 3) + k2 / 3
    k3 = k2 * (2 / 3) + rsv / 3
    near(k.iloc[2], k2, 1e-9, "K 第三根")
    near(k.iloc[3], k3, 1e-9, "K 第四根")
    near(d.iloc[2], d2, 1e-9, "D 第三根（比 K 慢一拍）")
    near(j.iloc[2], 3 * k2 - 2 * d2, 1e-9, "J = 3K-2D")


@check("3")
def t08_boll_uses_population_std_and_second_arg():
    import math

    df = frame([1, 2, 3])
    mid = ind.compute(df, "BOLL_MID", [3, 2])
    up = ind.compute(df, "BOLL_UP", [3, 2])
    low = ind.compute(df, "BOLL_LOW", [3, 2])
    sd = math.sqrt(((1 - 2) ** 2 + 0 + (3 - 2) ** 2) / 3)   # 总体标准差 ddof=0
    near(mid.iloc[2], 2.0, 1e-9, "中轨 = MA(3)")
    near(up.iloc[2], 2 + 2 * sd, 1e-9, "上轨 = 中轨 + 2 倍总体标准差")
    near(low.iloc[2], 2 - 2 * sd, 1e-9, "下轨对称")
    near(ind.compute(df, "BOLL_UP", [3, 1]).iloc[2], 2 + sd, 1e-9, "第二个参数是带宽倍数")
    eq(ind.arg_error("BOLL_UP", [3, 2]), "", "两个参数都齐才放行")


@check("3")
def t09_boll_args_are_two_not_one():
    """布林带要两个参数（天数+倍数），少给、越界都得拒。"""
    true(ind.arg_error("BOLL_UP", [20]), "少给一个参数却通过了")
    true("2 个参数" in ind.arg_error("BOLL_UP", [20]), "错误要说清楚差几个参数")
    true("之间" in ind.arg_error("BOLL_UP", [20, 0.2]), "倍数 0.2 要按范围拒绝")
    eq(ind.arg_error("BOLL_UP", [20, 5]), "", "上限内放行")
    eq(ind.compute(frame([1, 2, 3]), "BOLL_MID", [3, 2]).iloc[2],
       ind.compute(frame([1, 2, 3]), "MA", [3]).iloc[2], "BOLL_MID(n) 与 MA(n) 同值")


@check("3")
def t10_liangbi_and_hhv_llv_exclude_today():
    """量比、前高、前低都必须不含当天：含当天就等于自己和自己比。"""
    df = frame([10, 10, 10, 10], volume=[100.0, 100.0, 100.0, 300.0])
    lb = ind.compute(df, "LIANGBI", [3])
    near(lb.iloc[3], 3.0, 1e-9, "第 4 根量比 = 300/前 3 日均量 100（分母不含当天）")
    eq(bool(pd.isna(lb.iloc[2])), True, "第 3 根凑不出「前 3 日」")
    eq(ind.warmup("LIANGBI", [3]), 4, "量比(n) 要 n+1 根")

    h = [10.0, 12.0, 11.0, 20.0]
    l = [8.0, 9.0, 7.0, 6.0]
    df2 = frame([9, 11, 10, 19], high=h, low=l)
    near(ind.compute(df2, "HHV", [2]).iloc[2], 12.0, 1e-9, "前 2 日最高 = max(10,12)")
    near(ind.compute(df2, "HHV", [2]).iloc[3], 12.0, 1e-9, "第 4 根的前高不含自己")
    near(ind.compute(df2, "LLV", [2]).iloc[3], 7.0, 1e-9, "LLV 取的是最低而不是最高")
    near(ind.compute(df2, "LLV", [2]).iloc[2], 8.0, 1e-9, "前 2 日最低 = min(8,9)")


@check("3")
def t11_bad_indicator_args_fail_closed():
    """参数写错是用户填错，不是「帮他改成能跑的值」。"""
    df = rising(30)
    raises_error(lambda: ind.compute(df, "FOO", []), KIND_INTEGRITY, "UNKNOWN_INDICATOR",
                 "白名单外的指标名")
    raises_error(lambda: ind.compute(df, "MA", [0]), KIND_INTEGRITY, "INDICATOR_ARG",
                 "天数 0 要拒绝")
    raises_error(lambda: ind.compute(df, "MA", [2.5]), KIND_INTEGRITY, "INDICATOR_ARG",
                 "小数天数要拒绝")
    raises_error(lambda: ind.compute(df, "RSI", [999]), KIND_INTEGRITY, "INDICATOR_ARG",
                 "RSI 天数上限 100")
    true("整数" in ind.arg_error("MA", [2.5]), "提示要说清是整数问题")
    true("之间" in ind.arg_error("MA", [999]), "提示要给出范围")
    true("参数" in ind.arg_error("VOL_MA", []), "缺参数也要说清")


@check("3")
def t12_missing_column_is_integrity_not_silent_nan():
    """上游没给某一列是数据问题，必须 422 说清楚，而不是算出一条全空的线。"""
    df = rising(20).drop(columns=["high"])
    raises_error(lambda: ind.compute(df, "HIGH", []), KIND_INTEGRITY, "COLUMN_MISSING",
                 "缺列要按完整性错误拒绝")
    raises_error(lambda: ind.compute(pd.DataFrame(), "MA", [5]), KIND_INTEGRITY,
                 "COLUMN_MISSING", "空表也不能蒙混过关")


# ================================================================== DSL 校验


@check("3")
def t13_d11_unknown_indicator_rejected_with_its_name():
    """D11：指标名 FOO → 400，错误里必须同时出现 FOO 和「不在白名单」。"""
    err = bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", I("FOO", 5))))}, "D11 未知指标")
    true("FOO" in err["message"], f"错误信息要点名 FOO：{err['message']}")
    true("不在白名单" in err["message"], f"要说清是没在白名单里：{err['message']}")
    eq(err["field"], "signals.buy.items[0].right.name", "字段路径要指到那一格，前端才能跳过去")
    true("MA" in err["message"], "顺手把能用的指标列出来，用户不必翻文档")
    raises_error(lambda: dsl_mod.validate(dsl_of(G(C(N(1), ">", I("NOPE"))))), KIND_INPUT,
                 "DSL_INVALID", "左边写错也一样拦")


@check("3")
def t14_d12_d13_code_injection_shapes_rejected():
    """D12/D13：运算符写成 __import__、多余字段叫 exec/eval，一律 400。"""
    err = bad_http({"dsl": dsl_of(G(C(I("CLOSE"), "__import__", N(1))))}, "D12 运算符注入")
    eq(err["field"], "signals.buy.items[0].cmp", "注入点正好是比较符那一格")
    true("__import__" in err["message"], "回显非法值，方便定位")
    err = bad_http({"dsl": {**dsl_of(G(C(I("CLOSE"), ">", N(1)))), "exec": {"x": 1}}},
                   "D13 顶层多余字段")
    true("exec" in err["message"], f"要点名多余字段：{err['message']}")
    err = bad_http({"dsl": dsl_of(G({**C(I("CLOSE"), ">", N(1)), "eval": "close>1"}))},
                   "D13 条件里塞 eval 字段")
    true("eval" in err["message"], "非法字段名回显")
    eq(sorted(dsl_mod.CMP),
       sorted([">", "<", ">=", "<=", "==", "cross_above", "cross_below"]),
       "比较符白名单就这 7 个，多一个少一个都要先改 PRD")
    # 校验只按名单比对字符串，不解释执行：把代码塞成指标名也只会当成一个不认识的词
    err = bad_http({"dsl": dsl_of(G(C(I("__import__('os')"), ">", N(1))))}, "D12 代码当指标名")
    true("不在白名单" in err["message"], f"只比对名单不执行：{err['message']}")
    true("__import__" in err["message"], "非法值原样回显")


@check("3")
def t15_engine_has_no_way_to_run_a_string():
    """红线 1：AI 无执行权限。策略引擎里不允许出现把字符串当代码跑的原语。"""
    import pathlib

    files = ["indicators.py", "dsl.py", "explain.py", "templates.py",
             "api/strategies.py", "api/meta.py"]
    pattern = re.compile(r"\b(eval|exec|compile|getattr|setattr|globals|locals|vars|breakpoint"
                         r"|memoryview|input|open)\s*\(")
    for f in files:
        text = (pathlib.Path(__file__).resolve().parent.parent / f).read_text(encoding="utf-8")
        hits = [m.group(0) for m in pattern.finditer(text)]
        eq(hits, [], f"{f} 里出现了执行原语")
        true("__import__" not in text, f"{f} 里出现了 __import__")
        true("pickle" not in text and "subprocess" not in text,
             f"{f} 里出现了反序列化或外部执行程序的东西")


@check("3")
def t16_d6_cross_with_a_constant_rejected():
    """D6：常数线不会上下穿，写成「收盘价 上穿 20」是概念错误，直接拒绝。"""
    err = bad_http({"dsl": dsl_of(G(C(I("CLOSE"), "cross_above", N(20))))}, "D6 上穿配固定数字")
    true("上穿" in err["message"] and "线" in err["message"],
         f"要解释为什么不行：{err['message']}")
    err = bad_http({"dsl": dsl_of(G(C(N(20), "cross_below", I("CLOSE"))))}, "D6 下破配固定数字")
    eq(err["field"], "signals.buy.items[0].cmp", "错误定位到比较符")
    true("比大小" in err["message"], "要提示改用大于小于")
    true(dsl_mod.CMP["cross_above"]["line_only"] and not dsl_mod.CMP[">"]["line_only"],
         "约束是运算符的属性，不是散在各处的 if")


@check("3")
def t17_d7_nesting_depth_capped_at_two():
    """D7：条件组最多 2 层。第 3 层 → 400 并说明「最多 2 层」。"""
    cond = C(I("CLOSE"), ">", N(1))
    two = G(G(cond, cond), cond)
    eq(dsl_mod.validate(dsl_of(two))["signals"]["buy"]["items"][0]["t"], "group", "两层合法")
    three = G(G(G(cond), cond), cond)
    err = bad_http({"dsl": dsl_of(three)}, "D7 三层嵌套")
    true("最多只能嵌套 2 层" in err["message"], f"要说清上限：{err['message']}")
    true("3 层" in err["message"], "要报出实际层数")
    true("拆成两条规则" in err["message"], "给一个可执行的建议")
    err = bad_http({"dsl": dsl_of(G(G(G(G(cond)))))}, "D7 四层嵌套")
    true("最多只能嵌套 2 层" in err["message"], "四层也一样在第三层就被拦下")


@check("3")
def t18_every_other_input_mistake_is_named():
    """F16：未知运算符/逻辑/仓位模式/风控越界，都要给出具体字段和非法值。"""
    cond = C(I("CLOSE"), ">", N(1))
    err = bad_http({"dsl": {**dsl_of(G(cond)), "notes": "x"}}, "顶层多余字段", "DSL_INVALID")
    true("notes" in err["field"], f"字段路径要带上错的地方：{err['field']}")
    err = bad_http({"dsl": {"signals": {"buy": G(cond, logic="xor")}}}, "逻辑写 xor")
    true("xor" in err["message"] and "and" in err["message"], "列出可用逻辑")
    err = bad_http({"dsl": {"signals": {"buy": G(C(I("CLOSE"), "=>", N(1)))}}}, "比较符写 =>")
    true("=>" in err["message"], "回显非法比较符")
    err = bad_http({"dsl": {**dsl_of(G(cond)), "sizing": {"mode": "half"}}}, "仓位模式不支持")
    true("all_in" in err["message"], "说明 V1 只有全仓")
    err = bad_http({"dsl": dsl_of(G(cond), risk={"stop_loss_pct": 0.05})}, "止损 0.05 太小")
    true("0.1" in err["message"] and "100" in err["message"], f"给出允许范围：{err['message']}")
    eq(err["field"], "risk.stop_loss_pct", "定位到风控那一格")
    err = bad_http({"dsl": dsl_of(G(cond), risk={"max_hold_days": 12.5})}, "持有天数要整数")
    true("整数" in err["message"], "要说明得填整数")
    err = bad_http({"dsl": dsl_of(G(cond), risk={"stop_loss": 8})}, "风控字段名写错")
    true("stop_loss_pct" in err["message"], "把正确字段名告诉用户")
    err = bad_http({"dsl": {"signals": {"buy": {"logic": "and", "items": []}}}}, "空条件组")
    true("至少" in err["message"], "至少一条条件")
    err = bad_http({"dsl": {"signals": {"sell": None}}}, "买入条件整个缺失")
    true("买入" in err["message"], f"要说明缺的是买入条件：{err['message']}")
    err2 = bad_http({"dsl": {"signals": {"buy": {"items": [{"left": I("CLOSE"), "cmp": ">"}]}}}},
                    "条件缺右值")
    true("right" in err2["field"], f"缺哪个报哪个：{err2['field']}")
    err3 = bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", {"t": "str", "value": "x"})))},
                    "操作数形态写错")
    true("ind" in err3["message"] and "num" in err3["message"], "说明只有两种形态")


@check("3")
def t19_validate_is_idempotent_and_fills_defaults():
    """库里存的就是 validate() 的输出，读出来还要能再过一遍（人话预览、复制策略）。"""
    raw = tpl.by_id("ma_cross")["dsl"]
    once = dsl_mod.validate(raw)
    eq(dsl_mod.validate(once), once, "校验必须幂等")
    eq(dsl_mod.validate(tpl.by_id("ma_stop")["dsl"])["signals"]["sell"], None,
       "「均线+止损」没有卖出条件，是合法的")
    eq(once["sizing"], {"mode": "all_in"}, "缺 sizing 时补默认全仓")
    eq(sorted(once["risk"]), sorted(dsl_mod.RISK_FIELDS), "四个风控字段全部到位，没填的是 None")
    eq(once["signals"]["buy"]["items"][0]["left"]["args"], [5], "参数按声明保持整数")
    eq(once["signals"]["buy"]["items"][0]["t"], "cond", "补上类型标记")
    bol = dsl_mod.validate(dsl_of(G(C(I("BOLL_UP", 20, 2), ">", I("CLOSE")))))
    eq(bol["signals"]["buy"]["items"][0]["left"]["args"], [20, 2.0],
       "天数取整、倍数允许小数")
    eq(dsl_mod.validate(dsl_of(G(C(I("CLOSE"), ">", N(1), **{"not": True})),
                              risk={"max_hold_days": 30}))["risk"]["max_hold_days"], 30,
       "整数风控字段保持 int")


@check("3")
def t20_number_operand_rules():
    """固定数字这一侧也要有边界：文字、布尔、天量都拦下来。"""
    err = bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", N("12"))))}, "数字写成字符串")
    true("value" in err["field"], f"定位到 value：{err['field']}")
    bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", N(True))))}, "布尔当数字")
    bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", N(1e13))))}, "天量数字")
    eq(dsl_mod.validate(dsl_of(G(C(N(0), "<", I("CLOSE")))))["signals"]["buy"]["items"][0]
       ["left"]["value"], 0.0, "0 和「左边放数字」都合法")
    eq(dsl_mod.validate(dsl_of(G(C(I("VOLUME"), ">", I("VOL_MA", 5, mult=1.5)))))
       ["signals"]["buy"]["items"][0]["right"]["mult"], 1.5, "倍数挂在指标操作数上")
    bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", I("MA", 5, mult=0))))}, "倍数 0")
    bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", I("MA", 5, mult=-2))))}, "负倍数")
    bad_http({"dsl": dsl_of(G(C(I("CLOSE"), ">", I("MA", 5, mult=101))))}, "倍数过大")


# ================================================================== 求值


@check("3")
def t21_g4_warmup_longer_than_range_is_all_insufficient():
    """G4：区间只有 10 根却用 MA(20) → 全部「数据不足」，一个信号都不许有。"""
    df = rising(10)
    r = run(dsl_of(G(C(I("CLOSE"), ">", I("MA", 20))), risk={"stop_loss_pct": 8}), df)
    eq(int(r.insufficient.sum()), 10, "10 根全部数据不足")
    eq(int(r.buy.sum()), 0, "零买入信号")
    eq(int(r.sell.sum()), 0, "零卖出信号")
    eq(r.warmup, 20, "这份 DSL 需要 20 根暖机")
    eq(r.summary()["valid_bars"], 0, "有效根数 0")
    eq(r.summary()["bars"], 10, "总根数")


@check("3")
def t22_nan_is_never_silently_false():
    """F17：中间断一根（停牌）时，那几根必须是「数据不足」而不是「条件不成立」。"""
    df = frame([10 + i * 0.1 for i in range(30)])
    highs = list(df["high"])
    highs[10] = float("nan")
    df = df.assign(high=highs)
    r = run(dsl_of(G(C(I("HIGH"), ">", I("HHV", 5)))), df)
    # HHV(5) 的前高窗口含停牌那根 → 第 10 根往后共 5 根算不出；第 10 根本身也没最高价
    want = {0, 1, 2, 3, 4, 10, 11, 12, 13, 14, 15}
    got = {i for i, v in enumerate(r.insufficient) if v}
    eq(got, want, "前 5 根凑不满前高窗口；断货的一根会再连着拖 5 根")
    eq(int((r.buy & r.insufficient).sum()), 0, "数据不足的根绝不能同时是买入信号")
    true(5 not in got and 9 not in got, "第 6 根起前高已经算得出来，不能滥标")
    eq(int(r.buy.sum()), len(df) - len(want),
       "剩下的根都是有效判断：一路上涨时每根确实创了 5 日新高")
    eq(int((r.buy | r.insufficient).sum()), len(df),
       "每根要么给结论、要么标数据不足，没有第三种静默不吭声")


@check("3")
def t23_cross_above_fires_only_on_the_crossing_bar():
    """金叉只在该触发的那一根触发：多一根少一根都是回测未来的假信号。"""
    df = flat_then([10.0] * 20, [11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0])
    r = run(dsl_of(G(C(I("MA", 5), "cross_above", I("MA", 20)))), df)
    eq([ts for ts, v in zip(df.index, r.buy) if v], [df.index[20]],
       "第 21 根（MA5 首次上穿 MA20）触发，之后不再重复触发")
    eq({i for i, v in enumerate(r.insufficient) if v}, set(range(20)),
       "前 20 根数据不足：MA20 要 20 根，而上下穿还要再看前一根")
    eq(r.summary()["valid_bars"], 10, "有效 10 根")
    eq(r.summary()["buy_signals"], 1, "一次金叉")


@check("3")
def t24_cross_below_and_no_sell_group():
    df = flat_then([10.0] * 20, [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.5])
    r = run(dsl_of(G(C(I("CLOSE"), ">", N(1e6))),
                   sell=G(C(I("MA", 5), "cross_below", I("MA", 20)))), df)
    eq(int(r.buy.sum()), 0, "买入条件永远不成立")
    eq([i for i, v in enumerate(r.sell) if v], [20], "死叉正好在第 21 根")
    eq({i for i, v in enumerate(r.insufficient) if v}, set(range(20)),
       "卖出用到 MA20，它那 20 根暖机同样是数据不足（买卖两边都要看）")
    eq(int((r.sell & r.insufficient).sum()), 0, "数据不足的根也不能是卖出信号")
    only_buy = run(dsl_of(G(C(I("CLOSE"), "<", N(1e6)))), df)
    eq(int(only_buy.sell.sum()), 0, "没写卖出条件时不许凭空产生卖出信号")
    eq({i for i, v in enumerate(only_buy.insufficient) if v}, set(),
       "只用收盘价比数字，第 1 根就有值，不该标数据不足")


@check("3")
def t25_three_valued_and_or():
    """三值逻辑：AND 里一个否就否、一个未知就悬着；OR 里一个是就是。"""
    df = frame([1, 2, 3, 4, 5, 6, 7, 8])
    unknown_cond = C(I("MA", 20), ">", N(0))       # 8 根数据，MA20 全空
    sure_cond = C(I("CLOSE"), ">", N(5))           # 后 3 根成立
    o = run(dsl_of(G(sure_cond, unknown_cond, logic="or")), df)
    eq([i for i, v in enumerate(o.buy) if v], [5, 6, 7],
       "OR：确定的那条成立就够了，未知不能一票否决")
    eq({i for i, v in enumerate(o.insufficient) if v}, {0, 1, 2, 3, 4},
       "OR：剩下的那几根仍然悬着")
    a = run(dsl_of(G(sure_cond, unknown_cond, logic="and")), df)
    eq(int(a.buy.sum()), 0, "AND：有一条不成立就整体不成立")
    eq({i for i, v in enumerate(a.insufficient) if v}, {5, 6, 7},
       "AND：成立的那几根遇上一条未知，整体就是未知")
    both = run(dsl_of(G(sure_cond, C(I("CLOSE"), "<", N(10)), logic="and")), df)
    eq([i for i, v in enumerate(both.buy) if v], [5, 6, 7], "两条都确定时按普通 AND")


@check("3")
def t26_not_inverts_but_does_not_invent_data():
    df = frame([1, 2, 3, 4, 5, 6, 7, 8])
    r = run(dsl_of(G(C(I("CLOSE"), ">", N(5), **{"not": True}))), df)
    eq([i for i, v in enumerate(r.buy) if v], [0, 1, 2, 3, 4], "取反后前 5 根成立")
    eq(int(r.insufficient.sum()), 0, "取反不该制造未知")
    r2 = run(dsl_of(G(C(I("MA", 20), ">", N(0), **{"not": True}))), df)
    eq(int(r2.buy.sum()), 0, "未知的条件取反之后还是未知，不能变成成立")
    eq(int(r2.insufficient.sum()), 8, "8 根全部数据不足")


@check("3")
def t27_trace_inputs_lines_and_nodes():
    """溯源要用的中间量：每条操作数线 + 求值顺序，一次算完（阶段 4/7 不再重算）。"""
    df = rising(40)
    validated = dsl_mod.validate(dsl_of(G(
        C(I("MA", 5), ">", N(30)),
        C(I("VOLUME"), ">", I("VOL_MA", 5, mult=1.5)))))
    r = dsl_mod.evaluate(validated, df)
    eq(sorted(r.lines.columns), sorted(["MA(5)", "num:30", "VOLUME()", "VOL_MA(5)*1.5"]),
       "lines 里就是这四条线：每个操作数一条，画图和溯源共用")
    near(r.lines["MA(5)"].iloc[10], ind.compute(df, "MA", [5]).iloc[10], 1e-12, "均线线值")
    near(r.lines["VOL_MA(5)*1.5"].iloc[10],
         ind.compute(df, "VOL_MA", [5]).iloc[10] * 1.5, 1e-12, "倍数已经乘进线里")
    near(r.lines["num:30"].iloc[0], 30.0, 1e-12, "固定数字也是一条线")
    eq([n["path"] for n in r.nodes],
       ["signals.buy.items[0]", "signals.buy.items[1]", "signals.buy"],
       "节点按先子后父记录，前端能逐条展开")
    eq([n["kind"] for n in r.nodes], ["cond", "cond", "group"], "节点类型")
    eq(r.nodes[0]["cmp"], ">", "条件节点带上比较符")
    eq(r.nodes[-1]["children"], ["signals.buy.items[0]", "signals.buy.items[1]"],
       "组节点记下孩子，溯源按这个走")
    eq(sorted(dsl_mod.lines_of(validated, df)), sorted(r.lines.columns),
       "只算线不求值得到的列一致")


@check("3")
def t28_evaluate_refuses_unvalidated_dsl():
    """求值前必须过校验：这是本层的契约，违约要报 400 而不是崩 500。"""
    df = rising(30)
    raw = tpl.by_id("ma_cross")["dsl"]
    raises_error(lambda: dsl_mod.evaluate(raw, df), KIND_INPUT, "DSL_NOT_VALIDATED",
                 "未校验的原始 DSL 不许直接求值")
    raises_error(lambda: dsl_mod.evaluate({"signals": {}}, df), KIND_INPUT, "DSL_NOT_VALIDATED",
                 "缺 buy 更要拦")
    r = dsl_mod.evaluate(dsl_mod.validate(dsl_of(G(C(I("CLOSE"), ">", N(0))))), pd.DataFrame())
    eq(r.summary()["bars"], 0, "空K线跑出一片空结果是可以的（回测预检会先拦住）")
    eq(r.warmup, 0, "空表时暖机不作要求")


# ================================================================== 人话预览


@check("3")
def t29_d2_sentences_read_like_chinese():
    """D2/F18 的措辞：指标带中文、mult 说成「几倍」、风控说成一句人话。"""
    eq(explain.say_operand(I("MA", 5)), "5日均线", "均线")
    eq(explain.say_operand(I("VOL_MA", 5, mult=1.5)), "5日平均成交量的1.5倍", "倍数说人话")
    eq(explain.say_operand(N(30)), "30", "固定数字直接读")
    eq(explain.say_operand(I("RSI", 14)), "14日相对强弱RSI", "缩写要带中文")
    eq(explain.say_operand(I("BOLL_UP", 20, 2)), "20日布林上轨", "布林带")
    eq(explain.say_operand(I("HHV", 20)), "前20日的最高价", "前高")
    eq(explain.say_operand(I("CLOSE")), "当天收盘价", "无参数指标")
    eq(explain.say_condition(C(I("MA", 5), "cross_above", I("MA", 20))),
       "5日均线从下往上穿过20日均线（也就是常说的金叉）", "金叉")
    eq(explain.say_condition(C(I("RSI", 14), "<", N(30))), "14日相对强弱RSI低于30", "比数字")
    eq(explain.say_condition(C(I("PCT_CHG"), "==", N(0))), "当天涨跌幅和0正好相等", "等于的读法")
    eq(explain.say_condition(C(I("CLOSE"), ">", N(5), **{"not": True})),
       "并不满足「当天收盘价高于5」", "取反要说清楚否的是哪一句")
    eq(explain.risk_lines({"stop_loss_pct": 8, "take_profit_pct": None,
                           "trailing_stop_pct": 8.5, "max_hold_days": 20}),
       ["买入后亏到 8% 就卖出止损", "从持有期间最高价回落到 8.5% 就卖出（移动止损）",
        "拿满 20 天不管赚亏都卖出"], "风控四选三，整数不许写成 20.0")


@check("3")
def t30_f18_six_templates_read_as_plain_chinese():
    """F18/F19：PRD §3.2 的 6 个模板逐个 describe()，输出不能夹裸英文指标名。"""
    eq([t["id"] for t in tpl.TEMPLATES],
       ["ma_cross", "macd_cross", "rsi_rebound", "boll_break", "new_high_vol", "ma_stop"],
       "模板 id 固定，前端「载入模板」按它取")
    eq([t["name"] for t in tpl.TEMPLATES],
       ["双均线金叉", "MACD金叉", "RSI超卖反弹", "布林带突破", "放量创新高", "均线+止损"],
       "名字与 PRD §3.2 一致")
    bare = [c for c in ind.names() if "_" not in c and c != "RSI"]
    for t in tpl.TEMPLATES:
        true(has_cn(t["note"]) and len(t["note"]) > 15, f"{t['id']} 的一句话说明太短")
        true(t["period"] in store.PERIODS, f"{t['id']} 周期写法要合法")
        true(1 <= t["level"] <= 5, f"{t['id']} 难度点")
        d = dsl_mod.validate(t["dsl"])
        out = explain.describe(d)
        texts = out["buy_lines"] + out["sell_lines"] + out["risk_lines"] + [out["summary"]]
        for text in texts:
            true(has_cn(text), f"{t['id']} 有一句不是中文：{text}")
            for code in bare:
                pat = re.compile(rf"(?<![A-Za-z_]){code}(?![A-Za-z_])")
                eq([m.group(0) for m in pat.finditer(text)], [],
                   f"{t['id']} 的人话里出现了裸指标名 {code}")
            true(re.search(r"[A-Za-z_]\s*\(", text) is None,
                 f"{t['id']} 的人话里出现了公式写法：{text}")
        true("买入：" in out["summary"], f"{t['id']} 摘要要有买入")
        true(out["sell_lines"] or out["risk_lines"],
             f"{t['id']} 卖出条件与风控不能两头都空")
        if t["id"] == "ma_stop":
            eq(out["sell_lines"], [], "「均线+止损」确实没有卖出条件")
            eq(len(out["risk_lines"]), 3, "它的三条风控都在")
        if t["id"] == "new_high_vol":
            true(any("前20日的最高价" in line for line in out["buy_lines"]),
                 "放量创新高要能读出突破前高")
    eq(tpl.by_id("nope"), None, "不存在的模板返回 None，不要返回空壳")


# ================================================================== 接口


@check("3")
def t31_meta_endpoints_drive_the_frontend():
    """红线 6：下拉框内容全部来自后端，前端不硬编码指标表。"""
    fresh_db()
    resp = api().get("/api/indicators")
    eq(resp.status_code, 200, "GET /api/indicators")
    body = resp.json()
    eq(body["count"], 24, "F14 下发 24 个")
    eq(len(body["items"]), 24, "条目数")
    first = body["items"][0]
    eq(sorted(first), sorted(["code", "cn", "cat", "unit", "params", "needs", "term",
                             "warmup", "param_hint"]), "前端要用的字段都在")
    eq(sorted(first["term"]), ["example", "what", "why"], "三段式解释")
    by_code = {i["code"]: i for i in body["items"]}
    eq(by_code["MA"]["params"][0]["default"], 20, "默认参数下发（前端填格子的初值）")
    eq(by_code["RSI"]["params"][0]["max"], 100, "参数上限下发（前端就地拦住越界）")
    true("正整数" in by_code["MA"]["param_hint"], "参数提示")

    op = api().get("/api/operators").json()
    eq([c["code"] for c in op["compare"]], list(dsl_mod.CMP), "运算符白名单与 DSL 同一份")
    eq([c["code"] for c in op["logic"]], ["and", "or"], "逻辑运算符")
    eq(op["max_depth"], 2, "D7：嵌套上限")
    eq([c["code"] for c in op["compare"] if c["line_only"]],
       ["cross_above", "cross_below"], "上下穿标出来，前端好置灰固定数字")
    true(all(c["hint"] for c in op["compare"] if c["line_only"]), "被置灰时要给原因")
    eq(sorted(t["t"] for t in op["operands"]), ["ind", "num"], "操作数形态")
    eq(sorted(op["operands"][0]["keys"]), ["args", "mult", "name", "t"], "允许的字段名")
    eq(sorted(r["key"] for r in op["risk_fields"]), sorted(dsl_mod.RISK_FIELDS), "风控字段")
    eq(op["periods"], list(store.PERIODS), "周期写法与数据层同一套词")

    t = api().get("/api/templates").json()["items"]
    eq(len(t), 6, "F19：内置 6 个模板")
    true(all(i["builtin"] for i in t), "内置模板标记")
    eq([i["id"] for i in t], [x["id"] for x in tpl.TEMPLATES], "模板顺序与说明一致")


@check("3")
def t32_f20_strategy_crud_duplicate_and_template():
    """F20：新建/编辑/复制/删除/存为模板，存进去的一定是校验过的 DSL。"""
    fresh_db()
    raw = tpl.by_id("ma_cross")["dsl"]
    created = api().post("/api/strategies", json={"name": "我的双均线", "dsl": raw,
                                                 "note": "先跑着看", "period": "weekly"})
    eq(created.status_code, 200, "新建")
    row = created.json()
    sid = row["id"]
    eq(row["name"], "我的双均线", "名字")
    eq(row["period"], "weekly", "周期按用户写的存")
    eq(row["source"], "manual", "来源")
    eq(row["explain"]["buy_lines"],
       explain.describe(dsl_mod.validate(raw))["buy_lines"], "存进去的规则和预览说的是同一件事")
    eq(row["dsl"]["signals"]["buy"]["items"][0]["t"], "cond", "库里存规范化后的形状")
    eq(api().get("/api/strategies").json()["items"][0]["id"], sid, "列表能查到")
    eq(api().get(f"/api/strategies/{sid}").json()["name"], "我的双均线", "单查")

    upd = api().put(f"/api/strategies/{sid}",
                    json={"name": "改过的双均线", "period": "daily",
                          "dsl": dsl_of(G(C(I("MA", 10), "cross_above", I("MA", 60))))})
    eq(upd.json()["name"], "改过的双均线", "改名")
    eq(upd.json()["dsl"]["signals"]["buy"]["items"][0]["left"]["args"], [10], "改参数")
    eq(upd.json()["explain"]["buy_lines"],
       ["10日均线从下往上穿过60日均线（也就是常说的金叉）"], "预览跟着改")

    dup = api().post(f"/api/strategies/{sid}/duplicate").json()
    eq(dup["name"], "改过的双均线 副本", "复制一份要标出来")
    true(dup["id"] != sid, "复制是新的一条")
    eq(dup["dsl"], upd.json()["dsl"], "复制的是同一套规则")

    saved = api().post(f"/api/strategies/{sid}/save-as-template", json={"name": "我的常用"}).json()
    true(saved["id"].startswith("u") and not saved["builtin"], "用户模板要能区分开")
    items = api().get("/api/templates").json()["items"]
    eq(len(items), 7, "内置 6 个 + 用户 1 个")
    eq(items[-1]["name"], "我的常用", "用户模板排在后面")
    eq(items[-1]["dsl"]["signals"]["buy"]["items"][0]["left"]["args"], [10],
       "模板存的是校验过的 DSL")
    eq(api().delete(f"/api/templates/{saved['row_id']}").json()["removed"], True, "删掉自己的模板")
    eq(len(api().get("/api/templates").json()["items"]), 6, "内置模板不受影响")

    eq(api().delete(f"/api/strategies/{dup['id']}").json()["deleted"], dup["id"], "删除策略")
    eq(api().get(f"/api/strategies/{dup['id']}").status_code, 400, "删掉之后再查要报错")
    eq(api().get(f"/api/strategies/{dup['id']}").json()["error"]["code"],
       "STRATEGY_NOT_FOUND", "错误码要能区分「没这条」")


@check("3")
def t33_dirty_dsl_never_reaches_the_database():
    """库里不能有脏 DSL：否则回测时就得猜「这条老策略是不是哪里写错了」。"""
    fresh_db()
    before = len(api().get("/api/strategies").json()["items"])
    for payload, why in (
        ({"name": "脏", "dsl": dsl_of(G(C(I("CLOSE"), ">", I("FOO"))))}, "未知指标"),
        ({"name": "脏", "dsl": dsl_of(G(C(I("CLOSE"), "sqlite", N(1))))}, "未知运算符"),
        ({"dsl": dsl_of(G(C(I("CLOSE"), ">", N(1))))}, "没名字"),
        ({"name": "脏", "dsl": dsl_of(G(C(I("CLOSE"), ">", N(1)))), "source": "telepathy"},
         "来源不认识"),
        ({"name": "脏", "dsl": dsl_of(G(C(I("CLOSE"), ">", N(1)))), "period": "60m"}, "周期越界"),
        ({"name": "脏", "dsl": dsl_of(G(C(I("MA", 9999), ">", N(1))))}, "参数越界"),
        ({"name": "".join("长" for _ in range(41)), "dsl": dsl_of(G(C(I("CLOSE"), ">", N(1))))},
         "名字太长"),
    ):
        resp = api().post("/api/strategies", json=payload)
        eq(resp.status_code, 400, f"{why} 要被拒绝")
        eq(resp.json()["error"]["kind"], KIND_INPUT, f"{why} · 分类")
    eq(len(api().get("/api/strategies").json()["items"]), before, "一条都没写进去")
    with_ = api().post("/api/strategies", json={"name": "合法的一条",
                                                "dsl": dsl_of(G(C(I("CLOSE"), ">", N(1))))})
    eq(with_.status_code, 200, "同批里合法的写法要能成")


@check("3")
def t34_deleting_a_strategy_takes_its_backtests_with_it():
    """删策略要连带删掉它的回测记录（确认框里那句「N 条回测记录」就是这个数）。"""
    fresh_db()
    sid = api().post("/api/strategies", json={"name": "要删掉的",
                                              "dsl": dsl_of(G(C(I("CLOSE"), ">", N(1))))}
                     ).json()["id"]
    with store.connect() as conn:
        conn.execute("INSERT INTO backtest_run(id, strategy_id, name) VALUES(1,?,?),(2,?,?)",
                     (sid, "跑1", sid, "跑2"))
        conn.execute("INSERT INTO backtest(id, run_id, code) VALUES(1,1,'600519'),"
                     "(2,1,'000001'),(3,2,'600519')")
    out = api().delete(f"/api/strategies/{sid}").json()
    eq(out["runs_removed"], 2, "两条回测任务")
    eq(out["backtests_removed"], 3, "三只股票的明细")
    with store.connect() as conn:
        eq(conn.execute("SELECT COUNT(*) c FROM backtest").fetchone()["c"], 0, "明细清空")
        eq(conn.execute("SELECT COUNT(*) c FROM backtest_run").fetchone()["c"], 0, "任务清空")
        eq(conn.execute("SELECT COUNT(*) c FROM strategy").fetchone()["c"], 0, "策略本身也没了")


@check("3")
def t35_derived_periods_are_still_read_only():
    """阶段 2 改的周期说法要在三层一致：谁也别想把派生周期当日线塞进库。"""
    eq(list(store.PERIODS), ["daily", "weekly", "monthly"], "对外的三个词")
    eq(check_period("weekly"), "weekly", "接口层同一套词")
    raises_error(lambda: check_period("w"), KIND_INPUT, "BAD_PERIOD", "旧的 w 写法要拒绝")
    raises_error(lambda: check_period("60m"), KIND_INPUT, "BAD_PERIOD", "分钟线 V1 不做")
    fresh_db()
    raises_error(lambda: store.ensure_kline("600519", "2026-01-05", "2026-01-16",
                                            period="weekly"),
                 KIND_INTEGRITY, "DERIVED_PERIOD", "周线不能直接下载")
    eq(len(api().get("/api/strategies").json()["items"]), 0,
       "守卫在下载之前就失败，没有留下半截数据")
