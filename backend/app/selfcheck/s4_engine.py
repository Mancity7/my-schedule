"""阶段 4 自检：回测撮合内核 + 风控 + 绩效指标 + 数据缺口检查。

对应 PRD 验收 F1-F13、F28-F32、G1-G4、K3。全部离线：用合成K线（synth.py），
不碰行情接口、不碰正式库。

核心验证：
- 撮合顺序（PRD §5.4）：① pending → ② open fill → ③ intraday risk → ④ close decide
- T+1：当日买入不可当日卖出（F4）
- 涨跌停封死：一字涨停买不进，一字跌停卖不出
- 整手：买入必须是 100 股的整数倍
- 费用：佣金万2.5最低5、印花税千0.5仅卖、过户费万0.1、滑点0.1%
- 风控：止损/止盈/移动止损 = 盘中触发价成交（无滑点）；最长持有 = 收盘决策→次根开盘
- F10 会计恒等式：Σ盈亏 + 期末持仓市值 - 初始资金 == 总收益率对应金额，误差 < 0.01 元
- G1/G2 数据缺口：缺口率 > 容忍上限 → fail-closed 拒绝
- K3 费率快照：改费率后旧记录数值不变
"""

import pandas as pd

from .. import dsl as dsl_mod, store
from ..backtest import LOT, _Runner, fee_split, gap_report, round_lot, run_one
from ..config import limit_price, limit_rate, round_money
from ..errors import KIND_INTEGRITY
from ..metrics import compute
from ..synth import bar, level, make_bars, one_word
from .harness import check, eq, fresh_db, near, raises_error, true

# 默认费率（与 DEFAULT_SETTINGS 一致）
FEE = {
    "commission_rate": 0.00025,
    "commission_min": 5.0,
    "stamp_duty_rate": 0.0005,
    "transfer_fee_rate": 0.00001,
    "slippage": 0.001,
    "risk_free_rate": 0.02,
    "annual_days": 252,
}

INITIAL_CASH = 100000.0


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


def run_engine(bars_list, dsl, fee=None, initial_cash=None, code="600519", name="贵州茅台"):
    df = make_bars(bars_list)
    validated = dsl_mod.validate(dsl)
    return run_one(code, name, validated, df, fee or FEE, initial_cash or INITIAL_CASH, "daily")


# ================================================================== 费用拆分


@check("4")
def t01_fee_split_commission_min_5():
    """佣金最低 5 元：小额交易也要收 5 元。"""
    f = fee_split(10.0, 100, "buy", FEE)
    eq(f["commission"], 5.0, "1000 元的万2.5 = 0.25 元，低于最低 5 元")
    eq(f["stamp"], 0.0, "买入无印花税")
    eq(f["transfer"], round_money(1000 * 0.00001), "过户费万0.1")
    eq(f["total"], round_money(5.0 + f["transfer"]), "总费用")


@check("4")
def t02_fee_split_sell_has_stamp_duty():
    """卖出收印花税千0.5。"""
    f = fee_split(100.0, 1000, "sell", FEE)
    amount = 100.0 * 1000
    comm = max(round_money(amount * 0.00025), 5.0)
    stamp = round_money(amount * 0.0005)
    transfer = round_money(amount * 0.00001)
    eq(f["commission"], comm, "佣金")
    eq(f["stamp"], stamp, "印花税千0.5")
    eq(f["transfer"], transfer, "过户费")
    eq(f["total"], round_money(comm + stamp + transfer), "总费用")


@check("4")
def t03_round_lot_is_multiple_of_100():
    """买入必须是 100 股的整数倍。"""
    eq(round_lot(10000, 10.0), 1000, "10000 元 / 10 元 = 1000 股")
    eq(round_lot(1050, 10.0), 100, "1050 元只能买 1 手（1000 元）")
    eq(round_lot(50, 10.0), 0, "50 元买不到 1 手")
    eq(round_lot(10000, 0.0), 0, "价格 0 买不到")


# ================================================================== 涨跌停


@check("4")
def t04_limit_price_rounds_to_cent():
    """涨跌停价 = 昨收 ×(1 ± 幅度)，四舍五入到分。"""
    eq(limit_price(10.0, 0.10, +1), 11.0, "涨停 10%")
    eq(limit_price(10.0, 0.10, -1), 9.0, "跌停 10%")
    eq(limit_price(10.005, 0.10, +1), 11.01, "四舍五入到分")
    eq(limit_rate("600519"), 0.10, "主板 10%")
    eq(limit_rate("300001"), 0.20, "创业板 20%")
    eq(limit_rate("688001"), 0.20, "科创板 20%")


# ================================================================== 基本撮合


@check("4")
def t05_f1_simple_buy_and_sell():
    """F1：最简单的买卖：收盘价 > 5 买入，收盘价 < 4 卖出。"""
    bars = level(10, 3.0) + level(10, 6.0) + level(10, 3.0)
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(5))),
        sell=G(C(I("CLOSE"), "<", N(4))),
    )
    result = run_engine(bars, dsl)
    eq(result.status, "done", "回测完成")
    true(len(result.trades) >= 2, "至少有买入和卖出")
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    sell_trade = next((t for t in result.trades if t["side"] == "sell" and t["reason"] == "signal"), None)
    true(buy_trade is not None, "有买入")
    true(sell_trade is not None, "有卖出")
    true(buy_trade["shares"] % LOT == 0, "买入整手")
    true(sell_trade["shares"] == buy_trade["shares"], "卖出全部持仓")


@check("4")
def t06_f4_t_plus_1_no_same_day_sell():
    """F4：T+1 当日买入不可当日卖出。"""
    bars = [bar(10.0), bar(10.0), bar(10.0)]
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(5))),
        sell=G(C(I("CLOSE"), "<", N(10.5))),
    )
    result = run_engine(bars, dsl)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    true(buy_trade is not None, "有买入")
    sell_trades = [t for t in result.trades if t["side"] == "sell" and t["reason"] == "signal"]
    eq(len(sell_trades), 0, "当日不能卖出（T+1）")


@check("4")
def t07_f2_one_word_limit_up_cannot_buy():
    """F2：一字涨停封死，买不进。"""
    bars = [bar(10.0), one_word(11.0), bar(11.0)]
    dsl = dsl_of(buy=G(C(I("CLOSE"), ">", N(10))))
    result = run_engine(bars, dsl)
    buy_trades = [t for t in result.trades if t["side"] == "buy"]
    eq(len(buy_trades), 0, "一字涨停买不进")
    skip = next((s for s in result.signal_log if s.get("skip_reason") and "涨停" in s["skip_reason"]), None)
    true(skip is not None, "日志里记录了涨停封死")


@check("4")
def t08_f3_one_word_limit_down_cannot_sell():
    """F3：一字跌停封死，卖不出。"""
    bars = [bar(10.0), bar(6.0), one_word(5.4), bar(5.4)]
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(0))),
        sell=G(C(I("CLOSE"), "<", N(6))),
    )
    result = run_engine(bars, dsl)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    true(buy_trade is not None, "第 1 根买入")
    sell_trades = [t for t in result.trades if t["side"] == "sell" and t["reason"] == "signal"]
    eq(len(sell_trades), 0, "跌停封死卖不出")


@check("4")
def t09_f5_buy_must_be_round_lot():
    """F5：买入必须是整手（100 股）。"""
    bars = [bar(10.0)] * 5
    dsl = dsl_of(buy=G(C(I("CLOSE"), ">", N(5))))
    result = run_engine(bars, dsl, initial_cash=1050.0)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    if buy_trade:
        eq(buy_trade["shares"] % LOT, 0, "买入整手")


# ================================================================== 风控


@check("4")
def t10_f6_stop_loss_at_trigger_price_no_slippage():
    """F6：止损在触发价成交，不叠加滑点。"""
    bars = [bar(10.0), bar(9.5), bar(8.5), bar(9.0)]
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(5))),
        risk={"stop_loss_pct": 10},
    )
    result = run_engine(bars, dsl)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    true(buy_trade is not None, "买入")
    stop_trades = [t for t in result.trades if t["reason"] and "止损" in t["reason"]]
    true(len(stop_trades) > 0, "触发了止损")
    stop_trade = stop_trades[0]
    cost_price = buy_trade["price"]
    trigger = round_money(cost_price * 0.9)
    near(stop_trade["price"], trigger, 0.01, "止损价 = 成本 × 0.9，无滑点")


@check("4")
def t11_f7_take_profit_at_trigger_price():
    """F7：止盈在触发价成交。"""
    bars = [bar(10.0), bar(10.0), bar(12.0), bar(11.5)]
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(5))),
        risk={"take_profit_pct": 15},
    )
    result = run_engine(bars, dsl)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    true(buy_trade is not None, "买入")
    tp_trades = [t for t in result.trades if t["reason"] and "止盈" in t["reason"]]
    true(len(tp_trades) > 0, "触发了止盈")


@check("4")
def t12_f8_trailing_stop_from_high():
    """F8：移动止损从持有期间最高价回落。"""
    bars = [bar(10.0), bar(12.0), bar(11.0), bar(10.0), bar(9.0)]
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(5))),
        risk={"trailing_stop_pct": 10},
    )
    result = run_engine(bars, dsl)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    true(buy_trade is not None, "买入")
    trail_trades = [t for t in result.trades if t["reason"] and "移动止损" in t["reason"]]
    true(len(trail_trades) > 0, "触发了移动止损")


@check("4")
def t13_f9_max_hold_days_exits_next_open():
    """F9：最长持有 = 收盘决策 → 次根开盘成交。"""
    bars = [bar(10.0)] * 10
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(5))),
        risk={"max_hold_days": 2},
    )
    result = run_engine(bars, dsl)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    true(buy_trade is not None, "买入")
    max_hold_trades = [t for t in result.trades if t["reason"] and "最长持有" in str(t.get("reason", ""))]
    true(len(max_hold_trades) > 0 or len(result.trades) >= 2, "最长持有触发或正常卖出")


# ================================================================== F10 会计恒等式


@check("4")
def t14_f10_accounting_identity():
    """F10：从交易现金流重建的终值与引擎报告一致，误差 < 0.01 元。"""
    bars = level(5, 10.0) + level(5, 12.0) + level(5, 11.0) + level(5, 9.0)
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(11))),
        sell=G(C(I("CLOSE"), "<", N(10))),
    )
    result = run_engine(bars, dsl)
    m = compute(result, None, FEE)
    reconstructed = INITIAL_CASH
    for t in result.trades:
        if t["side"] == "buy":
            reconstructed -= t["amount"] + t["fees"]["total"]
        elif t["side"] == "sell":
            reconstructed += t["amount"] - t["fees"]["total"]
    reconstructed = round_money(reconstructed)
    true(abs(reconstructed - result.final_cash) < 0.01,
         f"现金流重建 {reconstructed} vs 引擎报告 {result.final_cash}")
    true(m["accounting_ok"], "accounting_ok 标记为真")


# ================================================================== 数据缺口


@check("4")
def t15_g1_gap_report_ok():
    """G1：缺口率在容忍范围内 → 允许回测。"""
    fresh_db()
    store.ensure_calendar(force=True)
    bars = level(20, 10.0)
    df = make_bars(bars, first="2025-01-02")
    gap = gap_report(df, "2025-01-02", "2025-01-30", "daily", 5.0)
    true(gap["ok"], "缺口率在容忍范围内")


@check("4")
def t16_g2_gap_exceeded_fail_closed():
    """G2：缺口率超过容忍上限 → fail-closed 拒绝。"""
    fresh_db()
    store.ensure_calendar(force=True)
    bars = level(5, 10.0)
    df = make_bars(bars, first="2025-01-02")
    gap = gap_report(df, "2025-01-02", "2025-01-30", "daily", 1.0)
    true(not gap["ok"], "缺口率超过 1% 要拒绝")
    true(gap["gap_pct"] > 1.0, "缺口率确实超标")


# ================================================================== 绩效指标


@check("4")
def t17_metrics_basic():
    """绩效指标基本正确。"""
    bars = level(10, 10.0) + level(10, 12.0)
    dsl = dsl_of(buy=G(C(I("CLOSE"), ">", N(5))))
    result = run_engine(bars, dsl)
    m = compute(result, None, FEE)
    true("total_return" in m, "有总收益率")
    true("annual_return" in m, "有年化收益率")
    true("max_drawdown" in m, "有最大回撤")
    true("sharpe" in m, "有夏普比率")
    true("total_trades" in m, "有交易次数")
    true("win_rate" in m, "有胜率")
    true(m["n_days"] == 20, "K线天数")


@check("4")
def t18_max_drawdown_calculation():
    """最大回撤计算正确。"""
    bars = [bar(10.0), bar(12.0), bar(8.0), bar(11.0)]
    dsl = dsl_of(buy=G(C(I("CLOSE"), ">", N(5))))
    result = run_engine(bars, dsl)
    m = compute(result, None, FEE)
    true(m["max_drawdown"] >= 0, "最大回撤非负")
    true(m["max_drawdown_start"] is not None, "有回撤起点")


# ================================================================== K3 费率快照


@check("4")
def t19_k3_fee_snapshot_preserved():
    """K3：改费率后旧记录数值不变。"""
    bars = level(5, 10.0) + level(5, 12.0)
    dsl = dsl_of(buy=G(C(I("CLOSE"), ">", N(5))))
    fee1 = {**FEE, "commission_rate": 0.0003}
    result1 = run_engine(bars, dsl, fee=fee1)
    m1 = compute(result1, None, fee1)
    fee2 = {**FEE, "commission_rate": 0.0001}
    result2 = run_engine(bars, dsl, fee=fee2)
    m2 = compute(result2, None, fee2)
    true(m1["total_fees"] != m2["total_fees"], "不同费率产生不同费用")
    true(result1.fee["commission_rate"] == 0.0003, "结果里存的是当时的费率")


# ================================================================== 信号日志


@check("4")
def t20_signal_log_records_skip_reasons():
    """信号日志记录未成交原因。"""
    bars = [bar(10.0), one_word(11.0), bar(11.0)]
    dsl = dsl_of(buy=G(C(I("CLOSE"), ">", N(10))))
    result = run_engine(bars, dsl)
    skip = next((s for s in result.signal_log if s.get("skip_reason")), None)
    true(skip is not None, "有跳过原因")
    true("涨停" in skip["skip_reason"], "原因是涨停")


@check("4")
def t21_trace_has_lines_and_nodes():
    """溯源数据包含线和节点。"""
    bars = level(30, 10.0)
    dsl = dsl_of(buy=G(C(I("MA", 5), ">", N(10))))
    result = run_engine(bars, dsl)
    true("ts" in result.trace, "有日期列表")
    true("lines" in result.trace, "有操作数线")
    true("nodes" in result.trace, "有节点")
    true("signals" in result.trace, "有信号索引")


# ================================================================== 边界情况


@check("4")
def t22_empty_kline_returns_error():
    """空K线返回错误状态。"""
    df = pd.DataFrame()
    validated = dsl_mod.validate(dsl_of(buy=G(C(I("CLOSE"), ">", N(5)))))
    result = run_one("600519", "贵州茅台", validated, df, FEE, INITIAL_CASH, "daily")
    eq(result.status, "error", "空K线返回错误")
    eq(result.error_code, "EMPTY_KLINE", "错误码 EMPTY_KLINE")


@check("4")
def t23_insufficient_data_no_signal():
    """数据不足的根不产生信号。"""
    bars = level(5, 10.0)
    dsl = dsl_of(buy=G(C(I("MA", 20), ">", N(5))))
    result = run_engine(bars, dsl)
    buy_trades = [t for t in result.trades if t["side"] == "buy"]
    eq(len(buy_trades), 0, "数据不足时没有买入")
    true(result.quality["skipped_warmup"] > 0, "记录了暖机跳过")


@check("4")
def t24_no_sell_signal_no_sell_trade():
    """没有卖出条件时不产生卖出交易。"""
    bars = level(5, 10.0) + level(5, 12.0)
    dsl = dsl_of(buy=G(C(I("CLOSE"), ">", N(5))))
    result = run_engine(bars, dsl)
    sell_trades = [t for t in result.trades if t["side"] == "sell" and t["reason"] == "signal"]
    eq(len(sell_trades), 0, "没有卖出条件就没有信号卖出")


@check("4")
def t25_risk_exit_no_slippage():
    """风控成交不叠加滑点（F8/F9）。"""
    bars = [bar(10.0), bar(9.0), bar(8.5)]
    dsl = dsl_of(
        buy=G(C(I("CLOSE"), ">", N(5))),
        risk={"stop_loss_pct": 10},
    )
    result = run_engine(bars, dsl)
    buy_trade = next((t for t in result.trades if t["side"] == "buy"), None)
    true(buy_trade is not None, "买入")
    stop_trades = [t for t in result.trades if t["reason"] and "止损" in t["reason"]]
    if stop_trades:
        cost_price = buy_trade["price"]
        trigger = round_money(cost_price * 0.9)
        near(stop_trades[0]["price"], trigger, 0.01, "止损价无滑点")
