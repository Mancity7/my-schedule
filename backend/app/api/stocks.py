"""股票搜索与信息接口。"""

from fastapi import APIRouter, Body, Query

from .. import stocks, store
from ..db import connect
from .common import check_code, check_period, check_range

router = APIRouter()


@router.get("/stocks/search")
def search(kw: str = "", limit: int = 8) -> dict:
    return {"kw": kw, "items": stocks.search_stocks(kw, limit)}


@router.post("/stocks/sync")
def sync(force: bool = Body(True, embed=True)) -> dict:
    """刷新全A股票列表。首次使用时搜索会自动触发，这里给用户一个手动入口。"""
    return stocks.sync_stock_list(force=force)


@router.get("/stocks/{code}")
def stock_detail(code: str) -> dict:
    code = check_code(code)
    info = stocks.get_stock(code)
    if info is None:
        return {"code": code, "found": False, "name": None, "market": None,
                "valuation": None, "cache": store.cache_state(code, "1990-12-19", store.today())}
    with connect() as conn:
        row = conn.execute(
            "SELECT MIN(ts) first_ts, MAX(ts) last_ts, COUNT(*) rows_ FROM kline "
            "WHERE code=? AND period='daily'", (code,)
        ).fetchone()
    return {
        "code": code,
        "found": True,
        "name": info["name"],
        "market": info["market"],
        "industry": info.get("industry") or None,      # 免费源没有，界面不显示
        "pinyin": info.get("pinyin"),
        "is_st": stocks.is_st(info["name"]),
        "valuation": store.get_valuation(code),
        "cache": {
            "first_ts": row["first_ts"], "last_ts": row["last_ts"], "rows": row["rows_"] or 0,
            "sources": [s for s in store.sources_of(code) if s],
        },
    }


@router.get("/stocks/{code}/kline")
def kline_data(code: str, start: str = Query(""), end: str = Query(""),
               period: str = Query("daily")) -> dict:
    """返回 K 线 OHLCV 数据，供前端画图。"""
    code = check_code(code)
    period = check_period(period)
    if start and end:
        start, end = check_range(start, end)
    else:
        start, end = "2020-01-01", store.today()
    df = store.get_kline(code, period, start, end, ensure=True)
    if df is None or not len(df):
        return {"code": code, "period": period, "bars": []}
    records = df.to_dict(orient="records")
    for r in records:
        for k in ("open", "high", "low", "close", "volume", "amount", "pct_chg"):
            if k in r and r[k] is not None:
                try:
                    r[k] = round(float(r[k]), 4) if k != "volume" else int(r[k])
                except (ValueError, TypeError):
                    pass
    return {"code": code, "period": period, "bars": records}
