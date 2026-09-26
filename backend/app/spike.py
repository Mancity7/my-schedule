"""数据源探针（阶段 1）。

目的只有一个：把我"以为存在"的 akshare / baostock 接口，变成"这台机器上真的能跑通、
返回的列名到底是什么"的事实记录。跑完会把结果写到 data/spike-<日期>.txt。

用法（容器内）：
    python -m app.spike            全量探针
    python -m app.spike ak_hist    只跑某一个探针
"""

import io
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path

from .config import DATA_DIR

START = "20190101"
END = date.today().strftime("%Y%m%d")
START_ISO = "2019-01-01"
CODE = "600519"      # 贵州茅台，主板
CODE_GEM = "300750"  # 宁德时代，创业板
INDEX = "000300"    # 沪深300

RESULTS: list[dict] = []


def probe(name: str, fn):
    """跑一个探针，任何异常都记录成结果，绝不让整个脚本崩掉。"""
    t0 = time.time()
    rec: dict = {"name": name}
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001
        rec.update(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            traceback="".join(traceback.format_exc()[-800:]),
            secs=round(time.time() - t0, 2),
        )
        RESULTS.append(rec)
        print(f"  ✗ {name:<34} {rec['error'][:90]}")
        return None
    secs = round(time.time() - t0, 2)
    df = out
    try:
        cols = [str(c) for c in df.columns]
    except AttributeError:
        # 不是 DataFrame（比如 baostock 的 result 对象或被转成了 list）
        rec.update(ok=True, kind=type(out).__name__, secs=secs, repr=repr(out)[:400])
        RESULTS.append(rec)
        print(f"  ✓ {name:<34} 非DataFrame {type(out).__name__} {secs}s")
        return out
    n = len(df)
    rec.update(
        ok=True,
        kind="DataFrame",
        rows=int(n),
        columns=cols,
        head=df.head(2).astype(str).to_dict("records") if n else [],
        secs=secs,
    )
    for cand in ("日期", "date", "trade_date"):
        if cand in cols:
            rec["first"] = str(df[cand].iloc[0])
            rec["last"] = str(df[cand].iloc[-1])
            break
    RESULTS.append(rec)
    flag = "✓" if n else "! 空表"
    print(f"  {flag} {name:<34} {n}行 {secs}s 列={cols[:12]}")
    return df


# -------------------------------------------------------------------------- akshare

def ak_version():
    import akshare
    return {"name": "akshare.__version__", "value": akshare.__version__}


def ak_code_name():
    import akshare as ak
    return ak.stock_info_a_code_name()


def ak_spot_em():
    import akshare as ak
    return ak.stock_zh_a_spot_em()


def ak_industry_of_one():
    """单只股票的行业与总股本，用于确认股票列表能否带上行业。"""
    import akshare as ak
    return ak.stock_individual_info_em(symbol=CODE)


def ak_hist_qfq():
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=CODE, period="daily", start_date=START, end_date=END, adjust="qfq"
    )


def ak_hist_raw():
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=CODE, period="daily", start_date=START, end_date=END, adjust=""
    )


def ak_hist_gem():
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=CODE_GEM, period="daily", start_date=START, end_date=END, adjust="qfq"
    )


def ak_hist_weekly():
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=CODE, period="weekly", start_date=START, end_date=END, adjust="qfq"
    )


def ak_hist_60min():
    """V2 候选：60分钟线，顺便看看历史能往前多远。"""
    import akshare as ak
    return ak.stock_zh_a_hist_min_em(symbol=CODE, period="60", adjust="qfq")


def ak_index_hist():
    import akshare as ak
    return ak.index_zh_a_hist(symbol=INDEX, period="daily", start_date=START, end_date=END)


def ak_valuation_lg():
    """历史每日 PE/PB —— 决定 PRD 白名单里 PE/PB 能否用于回测。"""
    import akshare as ak
    return ak.stock_a_indicator_lg(symbol=CODE)


def ak_spot_hist_one():
    """换手率/涨跌幅是否在日线里直接给出（否则要自己算）。"""
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=CODE_GEM, period="daily", start_date="20250101", end_date=END, adjust="qfq"
    )


# ------------------------------------------------------------------------- baostock

_bs_logged_in = False


def bs_login():
    global _bs_logged_in
    import baostock as bs
    r = bs.login()
    _bs_logged_in = True
    return {"error_code": r.error_code, "error_msg": r.error_msg}


def _bs_to_df(result):
    import baostock as bs
    import pandas as pd
    rows = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    return pd.DataFrame(rows, columns=result.fields.split(","))


def bs_stock_basic():
    import baostock as bs
    bs.login()
    return _bs_to_df(bs.query_stock_basic())


def bs_hist_qfq():
    import baostock as bs
    bs.login()
    fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,pctChg,peTTM,pbMRQ"
    return _bs_to_df(
        bs.query_history_k_data_plus(
            f"sh.{CODE}", fields, start_date=START_ISO, end_date=END, frequency="d", adjustflag="2"
        )
    )


def bs_hist_qfq_gem():
    import baostock as bs
    bs.login()
    fields = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"
    return _bs_to_df(
        bs.query_history_k_data_plus(
            f"sz.{CODE_GEM}", fields, start_date=START_ISO, end_date=END, frequency="d", adjustflag="2"
        )
    )


def bs_index_hist():
    import baostock as bs
    bs.login()
    fields = "date,code,open,high,low,close,volume,amount,pctChg"
    return _bs_to_df(
        bs.query_history_k_data_plus(
            f"sh.{INDEX}", fields, start_date=START_ISO, end_date=END, frequency="d", adjustflag="3"
        )
    )


def bs_weekly():
    import baostock as bs
    bs.login()
    fields = "date,code,open,high,low,close,volume,amount"
    return _bs_to_df(
        bs.query_history_k_data_plus(
            f"sh.{CODE}", fields, start_date=START_ISO, end_date=END, frequency="w", adjustflag="2"
        )
    )


def bs_valuation_only():
    """baostock 的估值字段是否必须走 K 线接口（决定 PE/PB 兜底路径）。"""
    import baostock as bs
    bs.login()
    return _bs_to_df(bs.query_profit_data(code=f"sh.{CODE}", year=2024, quarter=2))


def bs_happy_year():
    """最早能取到哪一年 —— 决定 MA250 + 5年区间是否永远够用。"""
    import baostock as bs
    bs.login()
    fields = "date,code,open,high,low,close,volume,amount"
    return _bs_to_df(
        bs.query_history_k_data_plus(
            f"sh.{CODE}", fields, start_date="2000-01-01", end_date="2005-12-31",
            frequency="d", adjustflag="2"
        )
    )


def ak_happy_year():
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=CODE, period="daily", start_date="20000101", end_date="20051231", adjust="qfq"
    )


PROBES = {
    "ak_code_name": ak_code_name,
    "ak_spot_em": ak_spot_em,
    "ak_industry_of_one": ak_industry_of_one,
    "ak_hist_qfq": ak_hist_qfq,
    "ak_hist_raw": ak_hist_raw,
    "ak_hist_gem": ak_hist_gem,
    "ak_hist_weekly": ak_hist_weekly,
    "ak_hist_60min": ak_hist_60min,
    "ak_index_hist": ak_index_hist,
    "ak_valuation_lg": ak_valuation_lg,
    "ak_happy_year": ak_happy_year,
    "bs_stock_basic": bs_stock_basic,
    "bs_hist_qfq": bs_hist_qfq,
    "bs_hist_qfq_gem": bs_hist_qfq_gem,
    "bs_index_hist": bs_index_hist,
    "bs_weekly": bs_weekly,
    "bs_valuation_only": bs_valuation_only,
    "bs_happy_year": bs_happy_year,
}


def compare_qfq_gap():
    """两源前复权价差：决定混源警告的措辞与严重度（PRD F13）。"""
    import pandas as pd

    ak_df = next((r for r in RESULTS if r["name"] == "ak_hist_qfq" and r.get("rows")), None)
    if not ak_df:
        print("  ! 跳过两源对比：akshare 日线不可用")
        return
    a = ak_hist_qfq()
    b = bs_hist_qfq()
    if a is None or b is None or not len(a) or not len(b):
        print("  ! 跳过两源对比：有一方没返回数据")
        return
    acols = {c: c for c in a.columns}
    da = pd.DataFrame({"ts": a[acols.get("日期", "date")].astype(str).str[:10],
                       "ak_close": a[acols.get("收盘", "close")].astype(float)})
    db = pd.DataFrame({"ts": b["date"].astype(str).str[:10], "bs_close": b["close"].astype(float)})
    m = da.merge(db, on="ts", how="inner")
    if not len(m):
        print("  ! 两源日期对不上，无法对比")
        return
    m["gap_pct"] = (m.ak_close - m.bs_close) / m.bs_close * 100
    common = {
        "name": "cross_source_gap",
        "compared_days": int(len(m)),
        "mean_abs_gap_pct": round(float(m.gap_pct.abs().mean()), 4),
        "max_abs_gap_pct": round(float(m.gap_pct.abs().max()), 4),
        "days_over_0p1pct": int((m.gap_pct.abs() > 0.1).sum()),
        "days_over_1pct": int((m.gap_pct.abs() > 1.0).sum()),
        "ok": True,
    }
    RESULTS.append(common)
    print(f"  ✓ cross_source_gap 对比{common['compared_days']}天 "
          f"平均|差|{common['mean_abs_gap_pct']}% 最大{common['max_abs_gap_pct']}% "
          f"超0.1%的有{common['days_over_0p1pct']}天")


def main(argv: list[str]) -> int:
    only = argv[0] if argv else None
    targets = {only: PROBES[only]} if only else PROBES
    if only and only not in PROBES:
        print(f"未知探针 {only}，可选：{', '.join(PROBES)}")
        return 2

    buf = io.StringIO()
    print(f"数据源探针  {date.today()}  区间 {START}~{END}")
    try:
        print(f"  akshare 版本 {ak_version()['value']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! akshare 导入失败：{exc}")

    if only in (None,):
        print("\n-- akshare --")
        for k, f in PROBES.items():
            if k.startswith("ak_"):
                try:
                    probe(k, f)
                except Exception:  # noqa: BLE001
                    print(f"  ✗ {k} 探针本身炸了\n{traceback.format_exc()}")
        print("\n-- baostock --")
        print(f"  login -> {bs_login()}")
        for k, f in PROBES.items():
            if k.startswith("bs_"):
                try:
                    probe(k, f)
                except Exception:  # noqa: BLE001
                    print(f"  ✗ {k} 探针本身炸了\n{traceback.format_exc()}")
        print("\n-- 两源前复权一致性 --")
        compare_qfq_gap()
    else:
        probe(only, targets[only])

    try:
        import baostock as bs
        bs.logout()
    except Exception:  # noqa: BLE001
        pass

    out = DATA_DIR / f"spike-{date.today():%Y%m%d}.txt"
    payload = {
        "date": str(date.today()),
        "window": [START, END],
        "results": RESULTS,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整结果已写入 {out}")
    ok = sum(1 for r in RESULTS if r.get("ok") and r.get("rows"))
    print(f"可用接口：{ok} / 探针总数 {len(PROBES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
