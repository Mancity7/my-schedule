"""行情缓存接口：下载/更新、状态查询、删除、清空。"""

from fastapi import APIRouter, Body

from .. import store
from ..errors import AppError
from .common import check_code, check_codes, check_period, check_range

router = APIRouter()


@router.get("/cache/stats")
def stats() -> dict:
    return store.cache_stats()


@router.get("/cache/stocks")
def cached_list() -> dict:
    return {"items": store.list_cached()}


@router.post("/cache/check")
def check_cache(codes: list[str] = Body(..., embed=True),
                start_date: str = Body(..., embed=True),
                end_date: str = Body(..., embed=True),
                period: str = Body("daily", embed=True)) -> dict:
    """首页那行「茅台 ✓缓存至09-24 · 平安 ⚠未缓存」。不下载行情，只可能补一次交易日历。"""
    start, end = check_range(start_date, end_date)
    period = check_period(period)
    store.ensure_calendar()          # 一个月一次的小请求，没有它判断不了"缺不缺最新一天"
    items = []
    for code in check_codes(codes):
        # 周线/月线由日线派生，所以缓存状态只看日线
        state = store.cache_state(code, start, end, "daily")
        items.append({"code": code, **state})
    return {"start": start, "end": end, "period": period, "items": items}


@router.post("/cache/refresh")
def refresh(codes: list[str] = Body(..., embed=True),
            start_date: str = Body(..., embed=True),
            end_date: str = Body(..., embed=True),
            force: bool = Body(False, embed=True)) -> dict:
    """按需下载/更新。逐只返回结果，一只失败不影响其他（回测时缺哪只报哪只）。"""
    start, end = check_range(start_date, end_date)
    results = []
    for code in check_codes(codes):
        attempts: list = []
        try:
            r = store.ensure_kline(code, start, end, force=force,
                                   on_attempt=lambda name, *a: attempts.append(name))
            results.append({"code": code, "ok": True, "attempts": attempts, **r})
        except AppError as exc:
            payload = exc.payload()["error"]
            results.append({"code": code, "ok": False, "attempts": attempts, **payload})
    ok = sum(1 for r in results if r["ok"])
    return {"start": start, "end": end, "ok": ok, "total": len(results), "items": results}


@router.delete("/cache/{code}")
def drop(code: str) -> dict:
    code = check_code(code)
    return {"code": code, "rows_removed": store.drop_cache(code)}


@router.post("/cache/clear")
def clear() -> dict:
    """清空行情缓存。策略、回测记录、设置都不动（验收 K8）。"""
    return store.clear_market_cache()
