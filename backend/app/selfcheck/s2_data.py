"""阶段 2 自检：缓存层的下载/复用、单源约束、派生周期、搜索、缓存管理、接口。

全部离线：把 `fetch_daily` / `fetch_index_daily` / `fetch_valuation_latest` 打桩，
所以"第二次不触网""周线不发接口请求"这类断言是真的在数调用次数（验收 C4 / C9）。
对应 PRD 验收 C1-C9、K5-K8、F6-F13。
"""

import contextlib

import pandas as pd

from .. import datasource, store
from .. import stocks as stocks_mod
from ..api import settings as api_settings
from ..config import DEFAULT_SETTINGS
from ..db import connect
from ..errors import KIND_INTEGRITY
from .harness import check, client, eq, fresh_db, near, raises_error, stub, true

START = "2026-01-05"
DATES = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
         "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16"]
# 日历里比合成K线多出两个交易日：用来验证"缺尾"判断是按交易日而不是自然日。
# 末尾再加上"今天"，让 ensure_calendar 认为这份日历够用，不会去联网取真日历。
CAL = DATES + ["2026-01-19", "2026-01-20", store.today()]


def make_bars(dates=None, source="sina", start=0):
    """造一段规整的合成K线：close = 10.5 + i。"""
    dates = dates or DATES
    n = len(dates)
    return pd.DataFrame({
        "ts": dates,
        "open": [10 + i + start for i in range(n)],
        "high": [11 + i + start for i in range(n)],
        "low": [9 + i + start for i in range(n)],
        "close": [10.5 + i + start for i in range(n)],
        "volume": [100.0] * n,
        "amount": [1000.0] * n,
        "turnover": [0.5] * n,
        "pct_chg": [float("nan")] + [None] * (n - 1),
        "source": [source] * n,
    })


def counter(df=None, err=None):
    """记录每次取数请求的参数，用来断言"没联网"或"只发了一次"。"""
    calls: list = []

    def fn(code, start, end, adjust="qfq", order=None, on_attempt=None):
        calls.append({"code": code, "start": start, "end": end})
        if err:
            raise err
        return df if df is not None else make_bars()
    fn.calls = calls
    return fn


def range_bars(source="sina"):
    """按请求区间出牌的假上游：日期落在 [start, end] 内的才返回。

    counter() 那种"不管问什么都给那 10 根"的假实现测不出"重下把尾巴删掉"这类
    范围错误，所以判断缓存覆盖范围时必须用它。
    """
    calls: list = []

    def fn(code, start, end, adjust="qfq", order=None, on_attempt=None):
        calls.append({"code": code, "start": start, "end": end})
        return make_bars([d for d in CAL if start <= d <= end], source=source)
    fn.calls = calls
    return fn


def seed_stock(code="600519", name="贵州茅台", market="sh"):
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stock_basic(code,name,market,industry,pinyin,updated_at) "
            "VALUES(?,?,?,?,?,datetime('now'))",
            (code, name, market, "", stocks_mod.pinyin_of(name)),
        )


@contextlib.contextmanager
def offline(daily=None, cal=None):
    """把"下载日线"和"取交易日历"都换成假实现，同时数出发了几次请求。

    阶段 2 的断言核心就是请求次数（C4 不重复下载 / C9 不发周线请求），
    所以这里返回的 fn.calls 就是被测代码真实的联网痕迹。
    """
    fn = counter(daily)
    calls: list = []

    def fake_cal():
        calls.append(1)
        return list(CAL if cal is None else cal)

    with stub(store, "fetch_daily", fn), stub(store, "fetch_trade_calendar", fake_cal):
        fn.cal_calls = calls
        yield fn


# ------------------------------------------------------------------ 读写


@check("2")
def t01_upsert_and_load_roundtrip():
    fresh_db()
    n = store.upsert_kline(make_bars(), "600519")
    eq(n, 10, "写入 10 根")
    df = store.load_kline("600519", "daily", "qfq")
    eq(len(df), 10, "读出 10 根")
    eq(list(df.index)[:2], ["2026-01-05", "2026-01-06"], "索引是升序 ts")
    eq(df["source"].unique().tolist(), ["sina"], "source 入库（验收 C5）")
    near(df["close"].iloc[0], 10.5, 1e-9, "收盘价")
    near(df["volume"].iloc[0], 100.0, 1e-9, "成交量单位是手，不重新解释")


@check("2")
def t02_load_range_and_null_handling():
    fresh_db()
    store.upsert_kline(make_bars(), "600519")
    df = store.load_kline("600519", "daily", "qfq", "2026-01-07", "2026-01-09")
    eq(list(df.index), ["2026-01-07", "2026-01-08", "2026-01-09"], "按区间裁切")
    with connect() as conn:  # 换手率整列缺失时要存 NULL，不能填 0
        conn.execute("UPDATE kline SET turnover=NULL, amount=NULL WHERE ts='2026-01-05'")
    got = store.load_kline("600519", "daily", "qfq", "2026-01-05", "2026-01-05")
    eq(bool(pd.isna(got["turnover"].iloc[0])), True, "缺失就是缺失，不补 0")
    eq(store.upsert_kline(pd.DataFrame(), "600519"), 0, "空表写入返回 0，不报错")


@check("2")
def t03_second_request_hits_cache_no_network():
    """验收 C4：同一区间再跑一次不再下载。"""
    fresh_db()
    with offline() as fn:
        first = store.ensure_kline("600519", "2026-01-05", "2026-01-16")
        second = store.ensure_kline("600519", "2026-01-05", "2026-01-16")
        # 请求区间是缓存的子集：走缓存，且不能偷偷再发一次网络
        narrow = store.ensure_kline("600519", "2026-01-05", "2026-01-09")
    eq(first["downloaded"], True, "第一次要下载")
    eq(second["downloaded"], False, "第二次必须用缓存")
    eq(len(fn.calls), 1, f"网络只发了 1 次，实际 {len(fn.calls)}")
    eq(second["rows"], 10, "缓存里读出同样 10 根")
    # 两条分支的 rows 必须是同一个意思（请求区间内的根数），前端才能同一处显示
    eq(first["rows"], second["rows"], "下载与走缓存两次返回的 rows 口径一致")
    eq(first["written"], 10, "整段实际写入 10 根")
    eq(narrow["downloaded"], False, "子区间请求也走缓存")
    eq(narrow["rows"], 5, "只请求前 5 天时 rows=5，不是整段根数")


@check("2")
def t03b_no_refetch_when_source_has_no_newer_bar():
    """真实世界里最容易踩的一种：请求"到今天"，但数据源当天还没出K线（收盘前/周末/长假）。
    如果只按"最后一根 < 今天"判断，每次回测都会重下一遍，既违反 C4 又容易被限流。"""
    fresh_db()
    end = store.today()                     # 2026-09-25，日历里根本没有这几个交易日
    with offline() as fn:
        first = store.ensure_kline("600519", "2026-01-05", end)
        second = store.ensure_kline("600519", "2026-01-05", end)
    eq(first["downloaded"], True, "第一次下载")
    eq(second["downloaded"], False, "已经请求到 today 了，数据源没有新的就不该再下")
    eq(len(fn.calls), 1, f"两次只发 1 次行情请求，实际 {len(fn.calls)}")
    st = store.cache_state("600519", "2026-01-05", end)
    eq(st["cached"], True, "状态行也要显示已缓存，而不是永远 ⚠")


@check("2")
def t03c_calendar_drives_the_trading_day_decision():
    """交易日历本身：一次取全、长期缓存、以及"下一个交易日"的判断。"""
    fresh_db()
    with offline() as fn:
        a = store.ensure_calendar()
        b = store.ensure_calendar()
    eq(a["downloaded"], True, "空库要取一次日历")
    eq(b["downloaded"], False, "第二次走本地")
    eq(len(fn.cal_calls), 1, "日历请求也只发一次")
    eq(store.count_trading_days("2026-01-05", "2026-01-09"), 5, "一周 5 个交易日")
    eq(store.count_trading_days("2026-01-10", "2026-01-11"), 0, "周末不是交易日")
    eq(store.last_trading_day("2026-01-11"), "2026-01-09", "周日回看最后一个交易日")
    eq(store.last_trading_day("2026-01-16"), "2026-01-16", "当天是交易日就用当天")
    eq(store.is_trading_day("2026-01-10"), False, "周末 False")
    eq(store.is_trading_day("2026-01-05"), True, "工作日 True")
    # 缺尾判断以日历为准：日历里还有 01-19 / 01-20 没缓存到
    store.upsert_kline(make_bars(), "600519")
    store.set_meta("600519", DATES[0], DATES[-1], attempted_to=DATES[-1])
    st = store.cache_state("600519", "2026-01-05", "2026-01-20")
    eq(st["reason"], "missing_tail", "请求到日历内更新的交易日，才算缺尾")


@check("2")
def t04_cache_state_reasons():
    fresh_db()
    eq(store.cache_state("600519", "2026-01-05", "2026-01-16")["cached"], False, "没缓存")
    eq(store.cache_state("600519", "2026-01-05", "2026-01-16")["reason"], "never_synced", "原因")
    eq(store.cache_state("600519", "2026-01-05", "2026-01-16")["mixed"], False,
       "没缓存也要带齐字段，前端不用判空")
    with offline():
        store.ensure_kline("600519", "2026-01-05", "2026-01-16")
    eq(store.cache_state("600519", "2026-01-05", "2026-01-16")["cached"], True, "区间已覆盖")
    st = store.cache_state("600519", "2025-01-01", "2026-01-16")
    eq(st["reason"], "missing_head", "往前要更多历史 = 缺头")
    st = store.cache_state("600519", "2026-01-05", "2026-01-09")
    eq(st["reason"], None, "请求区间完全落在缓存内")
    with connect() as conn:  # 把更新时间改到 30 天前
        conn.execute("UPDATE sync_meta SET updated_at='2026-08-01 09:00:00'")
    st = store.cache_state("600519", "2026-01-05", "2026-01-16")
    eq(st["reason"], "expired", "前复权序列会随除权整体重锚，缓存过期就得重下")


@check("2")
def t05_source_switch_overwrites_whole_range():
    """验收 C6：换源后整段只留新源，绝不能一半新浪一半腾讯。"""
    fresh_db()
    with offline(make_bars(source="sina")):
        store.ensure_kline("600519", "2026-01-05", "2026-01-16")
    with offline(make_bars(source="tencent")) as fn:
        store.ensure_kline("600519", "2026-01-12", "2026-01-16", force=True)   # 只请求后半段
    eq(fn.calls[0]["start"], "2026-01-05", "必须从缓存最早一天开始整段重下")
    eq(store.sources_of("600519"), ["tencent"], "整段换成腾讯")
    df = store.load_kline("600519", "daily", "qfq")
    eq(len(df), 10, "老数据不重复也不丢失")
    store.assert_single_source("600519")


@check("2")
def t05b_force_refresh_never_shrinks_the_cache():
    """F13「整段重下」的另一半：重下一小段不能把已缓存的其余部分删掉。

    这是实网冒烟踩出来的：对已缓存到 09-24 的股票点[重新下载]、只请求 01-05~01-16，
    实现按"请求区间"删了再写，结果 01-16 之后的 485 根凭空消失。
    """
    fresh_db()
    fn = range_bars()
    with stub(store, "fetch_daily", fn), stub(store, "fetch_trade_calendar", lambda: list(CAL)):
        first = store.ensure_kline("600519", "2026-01-05", store.today())
        eq(first["to_ts"], store.today(), "先缓存到最新交易日")
        fn.calls.clear()
        again = store.ensure_kline("600519", "2026-01-05", "2026-01-09", force=True)
        kept = len(store.load_kline("600519", "daily", "qfq"))
    eq(again["downloaded"], True, "force 一定要重下")
    eq(fn.calls[0]["end"], store.today(), "重下要请求到缓存已有的最后一天，不能只要这一小段")
    eq(store.get_meta("600519")["last_ts"], store.today(), "尾巴一根都不能少")
    eq(kept, len(CAL), "整段根数和重下前一致")
    eq(again["rows"], 5, "rows 仍是「请求区间内」的根数")
    eq(again["written"], len(CAL), "整段写入根数另放在 written")


@check("2")
def t05c_future_end_date_does_not_freeze_the_cache():
    """请求到未来日期时不能把 attempted_to 记成未来，否则"缺最新一天"永远判不出来。"""
    fresh_db()
    fn = range_bars()
    with stub(store, "fetch_daily", fn), stub(store, "fetch_trade_calendar", lambda: list(CAL)):
        store.ensure_kline("600519", "2026-01-05", "2030-12-31")
        meta = store.get_meta("600519")
        st = store.cache_state("600519", "2026-01-05", store.today())
    eq(meta["attempted_to"], store.today(), "请求日期夹到今天，不记 2030")
    eq(st["reason"], None, "夹完之后区间是齐的")
    eq(fn.calls[0]["end"], store.today(), "也不会去要 2030 年的数据")


@check("2")
def t06_mixed_source_is_detected_and_rejected():
    """验收 C7 / F13：库里一旦混源，回测必须被完整性错误挡下来。"""
    fresh_db()
    store.upsert_kline(make_bars(DATES[:5], source="sina"), "600519")
    store.upsert_kline(make_bars(DATES[5:], source="tencent"), "600519")
    st = store.cache_state("600519", "2026-01-05", "2026-01-16")
    eq(st["mixed"], True, "cache_state 要能标出混源")
    eq(sorted(st["sources"]), ["sina", "tencent"], "列出两个源")
    raises_error(lambda: store.assert_single_source("600519"),
                 KIND_INTEGRITY, "MIXED_SOURCE", "混源必须 fail-closed")
    items = [i for i in store.list_cached() if i["code"] == "600519"]
    eq(items[0]["mixed"], True, "设置页列表里也要带混源标记（验收 K6）")


# ------------------------------------------------------------------ 派生周期


@check("2")
def t07_resample_weekly_matches_hand_calc():
    fresh_db()
    store.upsert_kline(make_bars(), "600519")
    daily = store.load_kline("600519", "daily", "qfq")
    w = store.resample(daily, "weekly")
    eq(list(w["ts"]), ["2026-01-09", "2026-01-16"], "两周各一根，日期是周五")
    eq([float(w["open"].iloc[0]), float(w["high"].iloc[0]),
        float(w["low"].iloc[0]), float(w["close"].iloc[0])],
       [10.0, 15.0, 9.0, 14.5], "open=首、high=max、low=min、close=末")
    eq(float(w["volume"].iloc[0]), 500.0, "成交量按周累加")
    eq(float(w["turnover"].iloc[0]), 2.5, "换手率按周累加（一周累计换手）")
    near(float(w["pct_chg"].iloc[1]), (19.5 / 14.5 - 1) * 100, 1e-9, "涨跌幅用派生收盘重算")
    eq(bool(pd.isna(w["pct_chg"].iloc[0])), True, "第一根没有上一周收盘，是 NaN")


@check("2")
def t08_resample_monthly_and_source_kept():
    fresh_db()
    jan = [f"2026-01-{d:02d}" for d in (5, 6, 7, 8, 9)]
    feb = [f"2026-02-{d:02d}" for d in (2, 3, 4, 5, 6)]
    store.upsert_kline(make_bars(jan), "600519")
    store.upsert_kline(make_bars(feb, start=10), "600519")
    m = store.resample(store.load_kline("600519", "daily", "qfq"), "monthly")
    eq(list(m["ts"]), ["2026-01-31", "2026-02-28"], "月线归到月末最后一天")
    eq(float(m["close"].iloc[0]), 14.5, "月收盘 = 当月最后一个交易日收盘")
    eq(set(m["source"]), {"sina"}, "派生周期仍只有一个来源")
    raises_error(lambda: store.resample(pd.DataFrame(), "Q"), KIND_INTEGRITY, "BAD_PERIOD",
                 "不支持的周期要拒绝而不是默默返回空")


@check("2")
def t09_weekly_backtest_never_calls_weekly_api():
    """验收 C9：选周线只发一次日线请求，周线接口请求数为 0。"""
    fresh_db()
    with offline() as fn:
        w = store.get_kline("600519", "weekly", "2026-01-05", "2026-01-16")
    eq(len(fn.calls), 1, "只请求日线一次")
    eq(fn.calls[0]["start"], "2026-01-05", "请求的是日线区间")
    eq(len(w), 2, "周线由日线派生")
    eq(len(fn.calls), 1, "派生周线没有再发任何请求")


# ------------------------------------------------------------------ 搜索


@check("2")
def t10_pinyin_generation():
    eq(stocks_mod.pinyin_of("贵州茅台"), "gzmt", "纯中文")
    eq(stocks_mod.pinyin_of("TCL科技"), "tclkj", "字母保留并小写")
    eq(stocks_mod.pinyin_of("*ST海航"), "sthh", "星号丢掉，ST 留下")
    eq(stocks_mod.pinyin_of("中国 银行"), "zgyh", "空格丢掉")
    # 多音字必须按词组读，逐字转会错：重->zhong、行->xing、长->zhang
    eq(stocks_mod.pinyin_of("重庆银行"), "cqyh", "重庆 / 银行 都是多音字")
    eq(stocks_mod.pinyin_of("长江电力"), "cjdl", "长 在词组里读 chang")
    eq(stocks_mod.pinyin_of("西安旅游"), "xaly", "西安 的 西 读 xi")
    eq(stocks_mod.pinyin_of("甬金股份"), "yjgf", "股 读 gǔ")


@check("2")
def t11_three_ways_to_find_one_stock():
    """验收 C1 / C2：代码、名称、拼音三种输入都要命中 600519 贵州茅台。"""
    fresh_db()
    seed_stock("600519", "贵州茅台")
    seed_stock("000001", "平安银行")
    seed_stock("300750", "宁德时代")
    seed_stock("601963", "重庆银行")
    for kw in ("gzmt", "600519", "贵州", "茅台", "mt"):
        hits = stocks_mod.search_stocks(kw)
        true(bool(hits), f"「{kw}」一个都没搜到")
        eq(hits[0]["code"], "600519", f"「{kw}」首位应是贵州茅台，实际 {hits[0]}")
    eq([h["code"] for h in stocks_mod.search_stocks("cq")], ["601963"],
       "多音字按词组读，搜 cq 命中的是重庆银行而不是别的")
    eq(stocks_mod.search_stocks("zgyh"), [], "库里没有的名字不硬猜")
    eq(stocks_mod.search_stocks(""), [], "空输入不返回全表")
    eq(stocks_mod.search_stocks("zzzzzz"), [], "没有匹配就是空，不猜相近的")
    eq([h["code"] for h in stocks_mod.search_stocks("0")][:1], ["000001"], "代码前缀匹配")


@check("2")
def t12_stock_list_sync_writes_pinyin():
    fresh_db()
    fn = counter()
    df = pd.DataFrame({"code": ["600519", "920047"], "name": ["贵州茅台", "某某北"],
                       "market": ["sh", "bj"], "industry": ["", ""]})

    def go():
        fn.calls.append("list")
        return df

    with stub(stocks_mod, "fetch_stock_list", go):
        r1 = stocks_mod.sync_stock_list(force=False)
        r2 = stocks_mod.sync_stock_list(force=False)
    eq(r1["downloaded"], True, "空库要同步一次")
    eq(r2["downloaded"], False, "已有列表不重复同步")
    eq(len(fn.calls), 1, "网络只发一次")
    eq(r1["count"], 2, "写入 2 条")
    got = stocks_mod.get_stock("920047")
    eq(got["market"], "bj", "市场来自取数层")
    eq(got["industry"], "", "行业留空，界面不显示（实测免费源没有）")
    eq(got["pinyin"], "mmb", "拼音首字母入库（某 读 mǒu）")


# ------------------------------------------------------------------ 基准与估值


@check("2")
def t13_benchmark_cached_after_first_call():
    fresh_db()
    calls: list = []
    df = pd.DataFrame({"ts": DATES, "open": [1.0] * 10, "high": [2.0] * 10,
                       "low": [0.5] * 10, "close": [3900.0 + i for i in range(10)],
                       "volume": [1.0] * 10, "amount": [1.0] * 10})

    def fake(code="000300", start="", end=""):
        calls.append((code, start, end))
        return df

    with stub(store, "fetch_index_daily", fake):
        a = store.get_benchmark("000300", "2026-01-05", "2026-01-16")
        b = store.get_benchmark("000300", "2026-01-05", "2026-01-16")
    eq(len(calls), 1, "基准第二次不联网")
    eq(len(a), 10, "读出 10 个点")
    eq(list(b["close"].astype(float))[-1], 3909.0, "收盘价一致")
    eq(a.index.name, "ts", "索引是 ts")


@check("2")
def t14_valuation_is_display_only_and_cached():
    """验收 F12：PE/PB 只作为展示值缓存，取不到也不影响别的功能。"""
    fresh_db()
    calls: list = []

    def fake(code):
        calls.append(code)
        return {"code": code, "pe_ttm": 18.5, "pe_ttm_date": "2026-09-24",
                "pb": 6.2, "pb_date": "2026-09-24"}

    with stub(datasource, "fetch_valuation_latest", fake):
        v1 = store.get_valuation("600519")
        v2 = store.get_valuation("600519")
    eq(len(calls), 1, "第二次走缓存")
    near(v1["pe_ttm"], 18.5, 1e-9, "PE")
    eq(v1["available"], True, "有值")
    eq(v2["pb"], 6.2, "缓存回来的 PB")

    def fail(code):
        return {"code": code, "pe_ttm": None, "pb": None}

    with stub(datasource, "fetch_valuation_latest", fail):
        v3 = store.get_valuation("000001", force=True)
    eq(v3["available"], False, "取不到就标记没有，不编数字")
    eq(v3["_at"], None, "没取到不写缓存")


# ------------------------------------------------------------------ 缓存管理


@check("2")
def t15_drop_and_clear_keep_strategies():
    """验收 K7 / K8：删行情不能碰策略与回测记录。"""
    fresh_db()
    store.upsert_kline(make_bars(), "600519")
    store.upsert_kline(make_bars(dates=DATES[:6], source="tencent"), "000001")
    with connect() as conn:
        conn.execute("INSERT INTO strategy(id,name,dsl) VALUES(1,'我的策略','{}')")
        conn.execute("INSERT INTO backtest(id,run_id,code,metrics) VALUES(1,1,'600519','{}')")
    eq(store.drop_cache("600519"), 10, "删除返回行数")
    with connect() as conn:
        eq(int(conn.execute("SELECT COUNT(*) FROM strategy").fetchone()[0]), 1, "策略还在")
        eq(int(conn.execute("SELECT COUNT(*) FROM backtest").fetchone()[0]), 1, "回测记录还在")
        eq(int(conn.execute("SELECT COUNT(*) FROM sync_meta WHERE code='600519'").fetchone()[0]),
           0, "同步元信息一起清掉，否则下次以为还有")
    stats = store.clear_market_cache()
    eq(stats["kline_rows_removed"], 6, "清空按行情计")
    with connect() as conn:
        eq(int(conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]), 0, "K线清空")
        eq(int(conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]),
           len(DEFAULT_SETTINGS), "设置不动")


@check("2")
def t16_cache_stats_match_reality():
    fresh_db()
    store.upsert_kline(make_bars(), "600519")
    store.upsert_kline(make_bars(), "000001")
    s = store.cache_stats()
    eq(s["stocks"], 2, "已缓存股票数")
    eq(s["kline_rows"], 20, "K线总行数")
    eq(s["strategies"], 0, "策略数")
    eq(s["mixed_count"], 0, "没有混源")
    eq(s["db_size_mb"] > 0, True, "DB 文件大小")
    items = store.list_cached()
    eq(len(items), 2, "列表两条")
    eq(items[0]["rows_"], 10, "每只根数")
    eq(items[0]["last_ts"], "2026-01-16", "最后日期")


# ------------------------------------------------------------------ 接口层


@check("2")
def t17_api_stocks_and_cache_endpoints():
    fresh_db()
    seed_stock("600519", "贵州茅台")
    c = client()
    with offline() as fn:
        r = c.post("/api/cache/refresh", json={"codes": ["600519"],
                                               "start_date": "2026-01-05",
                                               "end_date": "2026-01-16"})
        eq(r.status_code, 200, "refresh 应成功")
        item = r.json()["items"][0]
        eq(item["ok"], True, f"下载失败：{item}")
        eq(item["downloaded"], True, "首次是下载")
        eq(item["rows"], 10, "请求区间内根数")
        eq(item["written"], 10, "整段写入根数")

        r2 = c.post("/api/cache/refresh", json={"codes": ["600519"],
                                                "start_date": "2026-01-05",
                                                "end_date": "2026-01-16"})
        eq(r2.json()["items"][0]["downloaded"], False, "第二次走缓存")
        eq(len(fn.calls), 1, "接口层两次请求只发了一次行情")

        chk = c.post("/api/cache/check", json={"codes": ["600519", "000001"],
                                              "start_date": "2026-01-05",
                                              "end_date": "2026-01-16"}).json()
        by = {i["code"]: i for i in chk["items"]}
        eq(by["600519"]["cached"], True, "已缓存")
        eq(by["000001"]["cached"], False, "未缓存（首页要显示 ⚠）")

        eq(c.get("/api/cache/stats").json()["kline_rows"], 10, "统计接口")
        eq(len(c.get("/api/cache/stocks").json()["items"]), 1, "列表接口")
        eq(c.delete("/api/cache/000001").json()["rows_removed"], 0, "删不存在的不报错")
        eq(c.delete("/api/cache/600519").json()["rows_removed"], 10, "删除接口")

    search = c.get("/api/stocks/search", params={"kw": "gzmt"}).json()
    eq(search["items"][0]["code"], "600519", "接口层拼音搜索")
    eq(c.get("/api/stocks/search", params={"kw": "!!!"}).json()["items"], [], "垃圾输入返回空")


@check("2")
def t18_api_stock_detail_uses_valuation_cache():
    fresh_db()
    seed_stock("600519", "贵州茅台")
    with stub(datasource, "fetch_valuation_latest",
              lambda code: {"code": code, "pe_ttm": 20.0, "pe_ttm_date": "2026-09-24",
                            "pb": 5.0, "pb_date": "2026-09-24"}):
        d = client().get("/api/stocks/600519").json()
    eq(d["found"], True, "找到")
    eq(d["name"], "贵州茅台", "名称")
    eq(d["industry"], None, "行业不显示（免费源没有）")
    near(d["valuation"]["pe_ttm"], 20.0, 1e-9, "PE 展示值")
    eq(d["cache"]["rows"], 0, "还没下载K线")
    missing = client().get("/api/stocks/600000").json()
    eq(missing["found"], False, "查无此股不报错，返回 found=False")


@check("2")
def t19_api_input_validation_is_fail_closed():
    c = client()
    bad_calls = [
        ({"codes": ["60051"], "start_date": "2026-01-05", "end_date": "2026-01-16"}, "BAD_CODE"),
        ({"codes": ["600519"], "start_date": "2026-13-45", "end_date": "2026-01-16"}, "BAD_DATE"),
        ({"codes": ["600519"], "start_date": "2026-01-16", "end_date": "2026-01-05"}, "DATE_ORDER"),
        ({"codes": ["600519"], "start_date": "1980-01-01", "end_date": "2026-01-05"}, "DATE_TOO_EARLY"),
        ({"codes": [], "start_date": "2026-01-05", "end_date": "2026-01-16"}, "NO_STOCK"),
    ]
    for body, code_part in bad_calls:
        r = c.post("/api/cache/refresh", json=body)
        eq(r.status_code, 400, f"{body} 应该被拒绝")
        err = r.json()["error"]
        eq(err["kind"], "input", f"{body} 分类应为 input")
        true(code_part in err["code"], f"{body} 期望 {code_part}，实际 {err['code']}")


@check("2")
def t20_api_settings_validate_every_value():
    fresh_db()
    c = client()
    got = c.get("/api/settings").json()
    eq(got["values"]["commission_rate"], "0.00025", "默认值回显")
    eq(sorted(got["groups"]["data"]),
       ["cache_ttl_days", "datasource_order", "gap_tolerance_pct"], "分组")

    r = c.post("/api/settings", json={"slippage": "0.002"})
    eq(r.json()["values"]["slippage"], "0.002", "即时保存")
    r = c.post("/api/settings", json={"slippage": "0.5"})
    eq(r.status_code, 400, "超区间要拒绝（0.5 = 50% 滑点显然是填错了）")
    eq(r.json()["error"]["field"], "slippage", "错误要指到具体字段")
    eq(c.post("/api/settings", json={"slippage": "abc"}).status_code, 400, "非数字拒绝")
    eq(c.post("/api/settings", json={"evil_key": "1"}).status_code, 400, "未知设置项拒绝")
    eq(c.post("/api/settings", json={"datasource_order": "sina,google"}).status_code,
       400, "未知数据源拒绝")
    eq(c.post("/api/settings", json={"datasource_order": "tencent,sina"}).json()["values"]
       ["datasource_order"], "tencent,sina", "允许改顺序")
    eq(c.post("/api/settings", json={"benchmark": "300"}).status_code, 400, "基准要 6 位")
    eq(c.post("/api/settings/reset", json={"group": "trade"}).status_code, 200, "按组恢复")
    eq(c.get("/api/settings").json()["values"]["slippage"], "0.001", "该组回到默认")
    eq(c.get("/api/settings").json()["values"]["datasource_order"], "tencent,sina",
       "别组不受影响")


@check("2")
def t21_api_secret_setting_never_echoed():
    """API Key 只在本地：接口回显必须是掩码，导出时另有一道（验收 L9）。"""
    fresh_db()
    c = client()
    r = c.post("/api/settings", json={"deepseek_api_key": "sk-should-not-leak"})
    eq("sk-should-not-leak" in r.text, False, "响应里不能带出明文 Key")
    eq(r.json()["values"]["deepseek_api_key"], "***", "回显掩码")
    eq(r.json()["secret_set"]["deepseek_api_key"], True, "前端要能知道这里已经填过")
    c.post("/api/settings", json={"deepseek_api_key": "***"})   # 前端原样提交掩码
    with connect() as conn:
        raw = conn.execute("SELECT v FROM settings WHERE k='deepseek_api_key'").fetchone()[0]
    eq(raw, "sk-should-not-leak", "提交掩码不得覆盖真值")


@check("2")
def t22_datasource_status_reads_last_test_without_network():
    """首页小灯只读上次测试结果，不能为了亮灯去联网。"""
    fresh_db()
    c = client()
    calls: list = []

    def fake(name, code="600519"):
        calls.append(name)
        return {"name": name, "label": name, "ok": name == "sina", "rows": 10,
                "latency": 0.5, "last_ts": "2026-01-16", "error": None if name == "sina" else "挂了"}

    with stub(api_settings, "test_upstream", fake):
        r = c.post("/api/datasource/test", json={"name": ""})
    eq(r.status_code, 200, "测试接口")
    eq(len(calls), 4, "四个上游各测一次")
    body = c.get("/api/datasource/status").json()
    eq(len(calls), 4, "查状态不再联网")
    eq(body["ok"], True, "有一个可用就算绿")
    eq(body["ok_source"], "sina", "报出可用的是哪个")
    eq(bool(body["checked_at"]), True, "带上次测试时间")
    eq(len(body["results"]), 4, "每个上游各自的状态")


@check("2")
def t23_phase2_routes_are_all_mounted():
    """路由必须真的挂上了应用。写成 `app.include_router` 之外的路径不会报错，只会 404，
    所以这里逐个点名（防止 main.py 与自检各说各话）。"""
    from ..main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    for p in ["/api/stocks/search", "/api/stocks/sync", "/api/stocks/{code}",
              "/api/cache/stats", "/api/cache/stocks", "/api/cache/check",
              "/api/cache/refresh", "/api/cache/{code}", "/api/cache/clear",
              "/api/settings", "/api/settings/reset", "/api/datasource/test",
              "/api/datasource/status"]:
        true(p in paths, f"接口 {p} 没有挂载")
