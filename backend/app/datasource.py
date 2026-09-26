"""取数层：把三个上游（新浪 / 腾讯 / 东财）统一成一套规范列。

2026-09-25 实测结论（详见 docs/数据源实测记录.md）：
- baostock 走 TCP 10030，本机网络不通，适配器保留但默认不启用；
- 东财系接口本机经常 RemoteDisconnected，排第三顺位；
- 新浪与腾讯的**前复权**序列在历史段可差 56%，因此同一只股票绝不允许混源拼接。

规范列：ts, open, high, low, close, volume(手), amount(元), turnover(%), pct_chg(%), source
"""

import time
from datetime import date, datetime
from typing import Callable

import pandas as pd

from .config import index_symbol, market_prefix
from .errors import datasource_error

CANONICAL = ["ts", "open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"]

_akshare = None


def _ak():
    """akshare 导入要 2 秒左右，且会拉起 py_mini_racer，按需加载。"""
    global _akshare
    if _akshare is None:
        import akshare

        _akshare = akshare
    return _akshare


def _compact(d: str | date) -> str:
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    s = str(d).strip().replace("-", "").replace("/", "")
    return s


def _iso(v) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]


def _num(col) -> pd.Series:
    return pd.to_numeric(col, errors="coerce")


def _finalize(df: pd.DataFrame, source: str, volume_unit: str = "shares",
              turnover_unit: str = "fraction") -> pd.DataFrame:
    """统一列名、单位与类型。缺列一律补 NaN，不猜值。

    volume_unit:     "shares"=股（新浪/腾讯/baostock）  "lots"=手（东财）
    turnover_unit:   "fraction"=0.0025 这样的小数（新浪/腾讯）  "percent"=0.25 这样已带百分号（东财/baostock）

    单位由调用方显式声明，绝不靠数值大小猜：一只全天换手率不到 1% 的股票，
    东财给的 0.8 会被"小于1就乘100"的猜法错变成 80%。
    """
    out = pd.DataFrame()
    out["ts"] = [_iso(v) for v in df["ts"]]
    for col in ("open", "high", "low", "close", "amount"):
        out[col] = _num(df[col]) if col in df.columns else float("nan")
    if "volume" in df.columns:
        v = _num(df["volume"])
        out["volume"] = v / 100.0 if volume_unit == "shares" else v
    else:
        out["volume"] = float("nan")
    if "turnover" in df.columns:
        t = _num(df["turnover"])
        out["turnover"] = t * 100.0 if turnover_unit == "fraction" else t
    else:
        out["turnover"] = float("nan")
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    # 涨跌幅一律由本层用（前复权）收盘价算出，三家同一口径。
    # 代价：除权除息当天，这里算出的值会和行情软件显示的略有差别（本层含分红再投资）。
    out["pct_chg"] = out["close"].pct_change() * 100.0
    out["source"] = source
    return out[CANONICAL + ["source"]]


# ---------------------------------------------------------------- 各上游适配器


def _sina_daily(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    ak = _ak()
    symbol = f"{market_prefix(code)}{code}"
    df = ak.stock_zh_a_daily(symbol=symbol, start_date=_compact(start), end_date=_compact(end),
                             adjust=adjust if adjust == "qfq" else "")
    if df is None or not len(df):
        raise datasource_error("EMPTY_RESPONSE", f"新浪没有返回 {code} 的任何数据")
    df = df.rename(columns={"outstanding_share": "_os"})
    df["ts"] = df["date"]
    return _finalize(df, "sina")


def _tencent_daily(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    ak = _ak()
    symbol = f"{market_prefix(code)}{code}"
    df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=_compact(start), end_date=_compact(end),
                              adjust=adjust if adjust == "qfq" else "")
    if df is None or not len(df):
        raise datasource_error("EMPTY_RESPONSE", f"腾讯没有返回 {code} 的任何数据")
    df["ts"] = df["date"]
    return _finalize(df, "tencent")


def _east_daily(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    ak = _ak()
    df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=_compact(start),
                            end_date=_compact(end), adjust=adjust if adjust == "qfq" else "")
    if df is None or not len(df):
        raise datasource_error("EMPTY_RESPONSE", f"东方财富没有返回 {code} 的任何数据")
    df = df.rename(columns={"日期": "ts", "开盘": "open", "收盘": "close", "最高": "high",
                            "最低": "low", "成交量": "volume", "成交额": "amount",
                            "换手率": "turnover"})
    return _finalize(df, "eastmoney", volume_unit="lots", turnover_unit="percent")


def _baostock_daily(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    """需要能连 www.baostock.com:10030。本机实测不通，保留给换网络环境的情况。"""
    import baostock as bs

    login = bs.login()
    if getattr(login, "error_code", "0") != "0":
        raise datasource_error("BAOSTOCK_LOGIN_FAILED",
                               f"baostock 登录失败：{getattr(login, 'error_msg', '未知原因')}",
                               detail="baostock 走 TCP 10030 端口，公司网络/代理可能不放行")
    try:
        fields = "date,open,high,low,close,volume,amount,turn,pctChg"
        res = bs.query_history_k_data_plus(f"{market_prefix(code)}.{code}", fields,
                                           start_date=f"{_compact(start)[:4]}-{_compact(start)[4:6]}-{_compact(start)[6:]}",
                                           end_date=f"{_compact(end)[:4]}-{_compact(end)[4:6]}-{_compact(end)[6:]}",
                                           frequency="d", adjustflag="2" if adjust == "qfq" else "3")
        rows = []
        while res.error_code == "0" and res.next():
            rows.append(res.get_row_data())
    finally:
        bs.logout()
    if not rows:
        raise datasource_error("EMPTY_RESPONSE", f"baostock 没有返回 {code} 的任何数据")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "amount",
                                     "turnover", "pct_chg"])
    return _finalize(df, "baostock", turnover_unit="percent")


UPSTREAMS: dict[str, Callable] = {
    "sina": _sina_daily,
    "tencent": _tencent_daily,
    "eastmoney": _east_daily,
    "baostock": _baostock_daily,
}

LABELS = {"sina": "新浪", "tencent": "腾讯", "eastmoney": "东方财富", "baostock": "baostock"}


# ---------------------------------------------------------------- 重试与降级


def _with_retry(fn: Callable, tries: int = 3, base_delay: float = 1.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 上游异常类型五花八门，统一按数据源问题处理
            last = exc
            if i < tries - 1:
                time.sleep(base_delay * (i + 1))
    raise last  # type: ignore[misc]


def fetch_daily(code: str, start: str, end: str, adjust: str = "qfq",
                order: list[str] | None = None, on_attempt: Callable | None = None) -> pd.DataFrame:
    """按顺序尝试各上游，每级重试 3 次。整段区间只出自一个上游（见文件头混源说明）。"""
    for name in (order or ["sina", "tencent", "eastmoney"]):
        fn = UPSTREAMS.get(name)
        if fn is None:
            continue
        if on_attempt:
            on_attempt(name)
        try:
            return _with_retry(lambda f=fn: f(code, start, end, adjust))
        except Exception as exc:  # noqa: BLE001
            if on_attempt:
                on_attempt(name, exc)
            continue
    raise datasource_error(
        "ALL_UPSTREAMS_FAILED",
        f"取不到 {code} 的行情：{'、'.join(LABELS.get(n, n) for n in (order or [])) or '所有已启用上游'}都失败了。",
        detail="可能是网络问题或对方临时限流。可以稍后点重试，或在设置里换个上游顺序。",
    )


def fetch_index_daily(index_code: str = "000300", start: str = "", end: str = "") -> pd.DataFrame:
    """基准指数日线。只用收盘价做对比曲线，单位问题不影响结论。

    注意：指数前缀不能套个股规则。沪深300 的代码是 000300，但新浪挂在 **sh**000300 下，
    按个股规则拼成 sz000300 会取回一张空表（akshare 在里面直接 KeyError 'date'）。
    """
    symbol = index_symbol(index_code)
    ak = _ak()

    def sina():
        return ak.stock_zh_index_daily(symbol=symbol)

    def tencent():
        # 腾讯指数接口分页拉全量，实测约 90 秒，只做新浪失败时的备胎。
        return ak.stock_zh_index_daily_tx(symbol=symbol)

    df, last = None, None
    for source in (sina, tencent):
        try:
            df = _with_retry(source, tries=2, base_delay=1.0)
            if df is not None and len(df):
                break
            last = datasource_error("EMPTY_RESPONSE", f"指数 {index_code} 无数据")
        except Exception as exc:  # noqa: BLE001
            last = exc
    if df is None or not len(df):
        raise datasource_error(
            "INDEX_UNAVAILABLE",
            f"取不到基准指数 {index_code}：{last}",
            detail="基准只影响对比曲线，不影响策略本身。可以先在设置里换一个基准或留空。",
        )

    out = pd.DataFrame()
    out["ts"] = [_iso(v) for v in df["date"]]
    for c in ("open", "high", "low", "close"):
        out[c] = _num(df[c]) if c in df.columns else float("nan")
    for src_col, col in (("volume", "volume"), ("amount", "amount")):
        out[col] = _num(df[src_col]) if src_col in df.columns else float("nan")
    out = out.dropna(subset=["close"]).drop_duplicates(subset=["ts"]).sort_values("ts")
    if start:
        out = out[out["ts"] >= _iso(start)]
    if end:
        out = out[out["ts"] <= _iso(end)]
    return out.reset_index(drop=True)


def fetch_stock_list() -> pd.DataFrame:
    """全 A股代码与名称。实测免费源不含行业，industry 列留空。"""
    ak = _ak()
    df = _with_retry(ak.stock_info_a_code_name)
    out = pd.DataFrame()
    out["code"] = df["code"].astype(str).str.zfill(6)
    out["name"] = df["name"].astype(str).str.strip()
    out["market"] = out["code"].map(market_prefix)
    out["industry"] = ""
    return out.drop_duplicates(subset=["code"]).reset_index(drop=True)


def fetch_trade_calendar() -> list[str]:
    """全部交易日（1990-12-19 到今年 12-31）。一次请求，之后长期用本地缓存。"""
    ak = _ak()
    df = _with_retry(ak.tool_trade_date_hist_sina)
    if df is None or not len(df):
        raise datasource_error("EMPTY_RESPONSE", "交易日历返回为空")
    return sorted({_iso(v) for v in df["trade_date"]})


def fetch_valuation_latest(code: str) -> dict:
    """最近一期 PE/PB，仅用于个股信息卡展示，不参与回测（实测源为稀疏序列）。"""
    ak = _ak()
    out: dict = {"code": code}
    for key, indicator in (("pe_ttm", "市盈率(TTM)"), ("pb", "市净率")):
        try:
            df = _with_retry(
                lambda i=indicator: ak.stock_zh_valuation_baidu(symbol=code, indicator=i, period="近五年"),
                tries=2,
            )
            out[key] = float(df["value"].iloc[-1])
            out[f"{key}_date"] = _iso(df["date"].iloc[-1])
        except Exception:  # noqa: BLE001 展示用数据，取不到就不显示，不影响回测
            out[key] = None
    return out


def test_upstream(name: str, code: str = "600519") -> dict:
    """设置页的 [测试] 按钮：真取一小段，而不是只 ping 端口。"""
    fn = UPSTREAMS.get(name)
    if fn is None:
        return {"name": name, "ok": False, "error": f"不认识的上游 {name}"}
    t0 = time.time()
    try:
        df = fn(code, "2026-01-01", date.today().strftime("%Y-%m-%d"), "qfq")
        return {
            "name": name,
            "label": LABELS.get(name, name),
            "ok": bool(len(df)),
            "rows": int(len(df)),
            "latency": round(time.time() - t0, 2),
            "last_ts": str(df["ts"].iloc[-1]) if len(df) else None,
            "error": None if len(df) else "连通但没有数据",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "label": LABELS.get(name, name),
            "ok": False,
            "latency": round(time.time() - t0, 2),
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
