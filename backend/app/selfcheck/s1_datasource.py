"""阶段 1 自检：取数层单位换算、降级顺序、重试、真实网络连通。

离线部分用假上游，稳定可重复；联网部分才是这一阶段真正的门禁
（对应 PRD 验收 C5 / C6 / C8 / C10）。
"""

import sys
import types

import pandas as pd

from ..config import index_symbol
from ..datasource import (
    CANONICAL,
    UPSTREAMS,
    _finalize,
    fetch_daily,
    fetch_index_daily,
    fetch_stock_list,
    fetch_valuation_latest,
    test_upstream,
)
from ..errors import KIND_DATASOURCE, datasource_error
from .harness import check, eq, raises_error, true

SAMPLE_SINA = pd.DataFrame({
    "ts": ["2026-01-06", "2026-01-05", "2026-01-07"],
    "open": [10.0, 9.0, 11.0],
    "high": [10.5, 9.5, 11.5],
    "low": [9.5, 8.5, 10.5],
    "close": [10.2, 9.0, 11.0],
    "volume": [200000.0, 100000.0, 300000.0],      # 股
    "amount": [2.04e6, 9.0e5, 3.3e6],
    "turnover": [0.0020, 0.0010, 0.0030],           # 小数
})


@check("1")
def t01_sina_units_normalized():
    out = _finalize(SAMPLE_SINA, "sina")
    eq(list(out["ts"]), ["2026-01-05", "2026-01-06", "2026-01-07"], "必须按日期升序")
    eq(float(out["volume"].iloc[0]), 1000.0, "成交量 股→手（除以100）")
    eq(round(float(out["turnover"].iloc[0]), 4), 0.1, "换手率 0.0010 → 0.1%")
    eq(list(out.columns), CANONICAL + ["source"], "规范列")
    eq(set(out["source"]), {"sina"}, "整段只有一个 source")


@check("1")
def t02_eastmoney_units_normalized():
    east = pd.DataFrame({
        "ts": ["2026-01-05", "2026-01-06"],
        "open": ["9.00", "10.00"],      # 东财返回字符串，必须能转
        "high": ["9.50", "10.50"],
        "low": ["8.50", "9.50"],
        "close": ["9.00", "10.20"],
        "volume": ["1000", "2000"],      # 手
        "amount": ["9e5", "2e6"],
        "turnover": ["0.10", "0.20"],    # 已是百分数
    })
    out = _finalize(east, "eastmoney", volume_unit="lots", turnover_unit="percent")
    eq(float(out["volume"].iloc[1]), 2000.0, "东财成交量本来就是手，不能再除100")
    eq(float(out["turnover"].iloc[1]), 0.2, "东财换手率已带百分号，不能再乘100")
    eq(round(float(out["close"].iloc[1]), 2), 10.2, "字符串价格要转成数字")


@check("1")
def t03_low_turnover_not_inflated():
    """换手率 0.8% 的股票，若靠"小于1就乘100"猜单位会被错算成 80%。"""
    low = SAMPLE_SINA.copy()
    out = _finalize(low.assign(turnover=[0.8, 0.5, 0.9]), "eastmoney", turnover_unit="percent")
    eq(float(out["turnover"].max()), 0.9, "percent 单位下不得放大")


@check("1")
def t04_pct_chg_first_is_nan_and_others_correct():
    out = _finalize(SAMPLE_SINA, "sina")
    true(pd.isna(out["pct_chg"].iloc[0]), "第一根没有前收盘，必须是 NaN 而不是 0")
    eq(round(float(out["pct_chg"].iloc[1]), 4), 13.3333, "9.0 -> 10.2 应为 +13.3333%")


@check("1")
def t05_bad_rows_dropped_and_dupes_removed():
    dirty = pd.concat([SAMPLE_SINA, SAMPLE_SINA.iloc[[0]]]).reset_index(drop=True)
    dirty.loc[len(dirty) - 1, "close"] = None
    out = _finalize(dirty, "sina")
    eq(len(out), 3, "重复行与缺价行都应剔除")
    eq(len(out.drop_duplicates(subset=["ts"])), 3, "ts 唯一")


SAMPLE_TENCENT = _finalize(SAMPLE_SINA, "tencent")


def _fake_upstream(name, df=None, err=None, counter=None):
    def fn(code, start, end, adjust):
        if counter is not None:
            counter.append(name)
        if err:
            raise err
        return df
    return fn


@check("1")
def t06_fallback_order():
    """主源失败时必须自动换到下一个上游，而不是报错（验收 C6）。"""
    calls: list = []
    saved = dict(UPSTREAMS)
    UPSTREAMS.update({
        "sina": _fake_upstream("sina", err=datasource_error("X", "新浪挂了"), counter=calls),
        "tencent": _fake_upstream("tencent", df=SAMPLE_TENCENT, counter=calls),
    })
    try:
        out = fetch_daily("600519", "2026-01-01", "2026-01-31", order=["sina", "tencent"])
    finally:
        UPSTREAMS.clear()
        UPSTREAMS.update(saved)
    eq(list(out["source"]), ["tencent"] * len(out), "降级后整段来自腾讯")
    eq(calls.count("sina"), 3, "每个上游重试 3 次")
    eq(calls.count("tencent"), 1, "腾讯第一次就成功，不该再试第三家")


@check("1")
def t07_all_failed_raises_datasource_error():
    saved = dict(UPSTREAMS)
    UPSTREAMS.update({
        "sina": _fake_upstream("sina", err=RuntimeError("新浪挂了")),
        "tencent": _fake_upstream("tencent", err=RuntimeError("腾讯挂了")),
    })
    try:
        def go():
            fetch_daily("600519", "2026-01-01", "2026-01-31", order=["sina", "tencent"])
        raises_error(go, KIND_DATASOURCE, "ALL_UPSTREAMS_FAILED", "三源全挂必须归类为数据源问题")
    finally:
        UPSTREAMS.clear()
        UPSTREAMS.update(saved)


@check("1")
def t08_baostock_login_failure_is_readable():
    """baostock 在本机不通（TCP 10030），要给出可读的数据源错误而不是崩溃。"""
    fake = types.ModuleType("baostock")
    fake.login = lambda: types.SimpleNamespace(error_code="10002007", error_msg="网络接收错误。")
    fake.logout = lambda: None
    sys.modules["baostock"] = fake
    try:
        def go():
            UPSTREAMS["baostock"]("600519", "2026-01-01", "2026-01-31", "qfq")
        raises_error(go, KIND_DATASOURCE, "baostock", "baostock 失败要能被识别为数据源问题")
    finally:
        sys.modules.pop("baostock", None)


@check("1")
def t09_unknown_upstream_test():
    r = test_upstream("nope")
    eq(r["ok"], False, "未知上游应返回不通过")


# ------------------------------------------------------------------ 联网实测部分


@check("1")
def t10_live_sina_daily():
    out = fetch_daily("600519", "2026-08-01", "2026-09-25", order=["sina"])
    true(len(out) > 20, f"新浪应返回约一个月的数据，实际 {len(out)} 行")
    eq(set(out["source"]), {"sina"}, "source 入库不为 NULL（验收 C5）")
    true(out["ts"].tolist() == sorted(out["ts"].tolist()), "日期升序")
    true(bool((out["high"] >= out["low"]).all()), "high 必须 >= low")
    true(bool((out["close"] > 0).all()), "收盘价必须为正")
    true(bool((out["turnover"] >= 0).all() and (out["turnover"] <= 100).all()),
         f"换手率应在 0~100% 之间，实际最大 {out['turnover'].max()}")


@check("1")
def t11_live_tencent_fallback_agrees_on_latest():
    """换源整段重下时，最新价必须一致（两家都以最新价为锚）。历史段允许不一致，
    这正是我们禁止混源拼接的原因。"""
    a = fetch_daily("600519", "2026-09-01", "2026-09-25", order=["sina"])
    b = fetch_daily("600519", "2026-09-01", "2026-09-25", order=["tencent"])
    eq(float(a["close"].iloc[-1]), float(b["close"].iloc[-1]), "新浪与腾讯最新收盘价应一致")
    eq(list(a["ts"])[-1], list(b["ts"])[-1], "最新交易日一致")


@check("1")
def t12_live_all_boards():
    """主板 / 创业板 / 科创板 / 北交所都要能取到，否则板块相关逻辑无法验证。"""
    for code in ("600519", "000001", "300750", "688981", "920047"):
        out = fetch_daily(code, "2026-06-01", "2026-09-25", order=["sina", "tencent"])
        true(len(out) > 30, f"{code} 只取到 {len(out)} 行")


@check("1")
def t13_index_symbol_uses_index_rule():
    """指数前缀不是个股前缀。000300 拼成 sz000300 会取回空表（实测踩过）。"""
    eq(index_symbol("000300"), "sh000300", "沪深300 在沪市")
    eq(index_symbol("000905"), "sh000905", "中证500 在沪市")
    eq(index_symbol("399006"), "sz399006", "创业板指在深市")
    eq(index_symbol("sh000300"), "sh000300", "已带前缀原样返回")


@check("1")
def t13b_live_benchmark_index():
    out = fetch_index_daily("000300", "2026-01-01", "2026-09-25")
    true(len(out) > 100, f"沪深300 应有上百个交易日，实际 {len(out)}")
    true(bool((out["close"] > 0).all()), "指数点位为正")
    eq(list(out["ts"]), sorted(out["ts"].tolist()), "指数日期升序")
    true(out["ts"].iloc[0] >= "2026-01-01" and out["ts"].iloc[-1] <= "2026-09-25",
         "必须按请求区间裁切")


@check("1")
def t14_live_stock_list():
    out = fetch_stock_list()
    true(len(out) > 5000, f"A股应有 5000+ 只，实际 {len(out)}")
    true("600519" in set(out["code"]), "列表里要有贵州茅台")
    eq(list(out.columns), ["code", "name", "market", "industry"], "股票列表列")
    eq(out.loc[out["code"] == "600519", "market"].iloc[0], "sh", "市场由代码推断")
    eq(out.loc[out["code"] == "300750", "market"].iloc[0], "sz", "创业板归深市")
    eq(out.loc[out["code"] == "920047", "market"].iloc[0], "bj", "北交所归 bj")


@check("1")
def t15_live_valuation_is_sparse_as_measured():
    """实测结论复核：估值是稀疏序列，所以只能当展示值，不能逐日参与回测。"""
    v = fetch_valuation_latest("600519")
    true(v.get("pe_ttm") is None or v["pe_ttm"] > 0, f"PE 取值异常 {v}")
    true(v.get("pb") is None or v["pb"] > 0, f"PB 取值异常 {v}")


@check("1")
def t16_live_upstream_test_reports_latency():
    r = test_upstream("sina")
    eq(r["ok"], True, f"新浪连通性测试应通过：{r.get('error')}")
    true(r["latency"] > 0, "要报出耗时")
    true(r["rows"] > 0, "要报出行数")


@check("1")
def t17_live_baostock_clean_failure():
    """本机 baostock 端口不通。它必须报数据源类错误，绝不能卡死或抛未分类异常。"""
    r = test_upstream("baostock")
    eq(r["ok"], False, "baostock 在本机应测试失败（10030 端口不通）")
    true(bool(r.get("error")), "必须给出具体失败原因，供设置页展示")
