"""合成K线夹具：把「次日一字涨停」「盘中触止损」这些验收场景写成一行一行能读的东西。

自检不碰外网、不碰正式库。这里造出来的 DataFrame 与 `store.load_kline` 的结构
完全一致（同样的列、同样的索引、同样的单位），所以指标层和撮合层分不清它是真是假
—— 这正是「引擎算错没有」能被数字复核的前提。

单位照 PRD §5.3：volume=手（1 手 100 股）、amount=元、turnover/pct_chg 是百分数。
"""

from typing import Any

import pandas as pd

from .config import round_money

COLS = ["ts", "open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg", "source"]
DEFAULT_FIRST = "2025-01-02"
DEFAULT_VOLUME = 10000.0        # 手。足够大到不会被"资金不够买一手"干扰，想测那只场景就自己写小


def bar(close: float, o=None, h=None, l=None, v: float = DEFAULT_VOLUME,
        **extra: Any) -> dict:
    """一根K线。省略 o/h/l 时默认等于收盘价（十字线）。"""
    out: dict[str, Any] = {"c": float(close), "o": o, "h": h, "l": l, "v": v}
    out.update(extra)
    return out


def one_word(close: float, v: float = 1000.0, **extra: Any) -> dict:
    """一字板：开=高=低=收。涨停/跌停封死用的就是它（有成交，所以量不为 0）。"""
    return bar(close, o=close, h=close, l=close, v=v, **extra)


def suspended(**extra: Any) -> dict:
    """停牌日：没有一笔成交，价格贴在昨收上（真实数据里通常是整天没有这一行，两种都算跳过）。"""
    out: dict[str, Any] = {"suspended": True}
    out.update(extra)
    return out


def level(n: int, close: float, **extra: Any) -> list[dict]:
    """连续 n 根收在同一个价。测均线交叉、测「拿满 N 天」时用得上。"""
    return [bar(close, **extra) for _ in range(int(n))]


def make_bars(rows: list[dict], first: str = DEFAULT_FIRST, freq: str = "B",
              source: str = "synth") -> pd.DataFrame:
    """把 bar() 列表变成回测能直接吃的 DataFrame（索引 = 日期字符串，升序）。

    `freq` 默认 "B"（工作日）：造出来的日期是真实交易日样子，配合同一段日历做缺口检查。
    某一行的 `ts` 可以显式指定日期，用来造停牌缺口。
    """
    if not rows:
        return pd.DataFrame(columns=COLS).set_index("ts")
    days = [str(d)[:10] for d in pd.date_range(first, periods=len(rows), freq=freq)]
    out: list[dict] = []
    prev_close: float | None = None
    for i, (r, day) in enumerate(zip(rows, days)):
        ts = str(r.get("ts") or day)
        if r.get("suspended"):
            c = float(r["c"]) if r.get("c") is not None else (prev_close or 0.0)
            o = h = l = c
            v = 0.0
        else:
            c = float(r["c"])
            o = float(r["o"]) if r.get("o") is not None else c
            h = float(r["h"]) if r.get("h") is not None else max(o, c)
            l = float(r["l"]) if r.get("l") is not None else min(o, c)
            v = float(r["v"])
        _sane(ts, o, h, l, c)
        pct = r.get("pct_chg")
        if pct is None:
            pct = round_money((c / prev_close - 1.0) * 100.0, 4) if prev_close else None
        out.append({
            "ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v,
            "amount": round_money(float(r["amount"])) if r.get("amount") is not None
                      else round_money(v * 100.0 * c),
            "turnover": float(r["turnover"]) if r.get("turnover") is not None else None,
            "pct_chg": pct, "source": r.get("source") or source,
        })
        prev_close = c
    df = pd.DataFrame(out)[COLS].astype({"turnover": "float64", "pct_chg": "float64"})
    return df.set_index("ts").sort_index()


def _sane(ts: str, o: float, h: float, l: float, c: float) -> None:
    """夹具写错要当场喊停：一根最高价低于收盘价的K线不存在，留着它算出来的数字全是废的。"""
    if h < max(o, c) or l > min(o, c) or l <= 0:
        raise ValueError(f"合成K线 {ts} 自相矛盾：开{o} 高{h} 低{l} 收{c}"
                         f"（最高价必须盖住开盘和收盘，最低价不能高于它们）")
