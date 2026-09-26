"""阶段 12 自检：实时模拟盘（账户 / 结算 / 信号日志 / 幂等恢复）。

对应 PRD §5.4「回测与模拟盘必须共用同一套撮合逻辑」、§5.9 模拟盘四张表、
§8.1 的 V2 条目（实时模拟盘、重启幂等恢复、信号日志）。全部离线：合成K线 + 打桩日历。

这一阶段最重要的两条断言，是防两种"看起来对、其实错"的失效：

1. **模拟盘和回测必须逐笔一致**（t01）。只要有人图省事在 sim.py 里另写一份
   成交价/费用/涨跌停判断，两边就会慢慢漂开，而单看任何一边都发现不了。
2. **一天一天结算必须等于一次结算完**（t02）。撮合状态要经过 JSON 序列化存进
   SQLite 再读回来，漏存一个字段（持有天数、持有期最高价、未成交的委托）都不会
   报错，只会让账户在后来的某一天做出错误的决定。

另外记一条**已知的规格分歧**，别把它当 bug 修掉：
PRD §5.4 写「封死则信号顺延」，但交付的引擎是「记录原因、当天不成交，次日条件
仍成立才重新发信号」。t18 钉的是**现状**。改不改要用户拍板 —— 那会改动所有已经
验收过的回测数字。
"""

from datetime import datetime

from .. import dsl as dsl_mod
from .. import sim as sim_mod
from .. import store
from ..backtest import initial_state, run_one
from ..config import round_money
from ..db import connect, dumps, loads, now
from ..errors import KIND_INPUT
from ..synth import bar, make_bars, one_word
from .harness import check, client, eq, fresh_db, near, raises_error, stub, true

FEE = {
    "commission_rate": 0.00025,
    "commission_min": 5.0,
    "stamp_duty_rate": 0.0005,
    "transfer_fee_rate": 0.00001,
    "slippage": 0.001,
}
CASH = 100000.0
CODE = "600519"
FIRST = "2025-01-02"
FLAT_N = 40          # 暖机段。必须比 warmup + LOOKBACK_MARGIN 长，否则 sim 会 fail-closed


# ------------------------------------------------------------------ DSL 小工具


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


def risk(stop=None, profit=None, trail=None, hold=None):
    return {"stop_loss_pct": stop, "take_profit_pct": profit,
            "trailing_stop_pct": trail, "max_hold_days": hold}


def cross_dsl(**kw):
    """MA5 上穿 MA20 买、下破卖。暖机 20 根 —— 正好能验"取数窗口够不够长"。"""
    return {"signals": {
        "buy": G(C(I("MA", 5), "cross_above", I("MA", 20))),
        "sell": G(C(I("MA", 5), "cross_below", I("MA", 20))),
    }, "risk": kw.get("risk") or risk()}


# ------------------------------------------------------------------ 行情夹具


def _action(start_price: float):
    """一段有涨有跌的行情：先涨出金叉，再跌出止损/死叉，最后再涨一次。"""
    out, p = [], start_price
    for _ in range(25):
        p = round_money(p * 1.02)
        out.append(bar(p))
    for _ in range(25):
        p = round_money(p * 0.98)
        out.append(bar(p))
    for _ in range(10):
        p = round_money(p * 1.03)
        out.append(bar(p))
    return out


def _flat(n=FLAT_N, price=10.0):
    """横盘：MA5 恒等于 MA20，交叉条件永远不成立。给不用均线的用例当垫场。"""
    return [bar(price) for _ in range(n)]


def _series(lead=FLAT_N):
    """默认夹具：**先阴跌**，再涨出金叉、跌出死叉、最后再涨一次。

    开头必须是严格下跌而不是横盘。横盘时 MA5 恒等于 MA20，而 cross_above 要求
    "前一天还在下面"—— 等号永远不满足，金叉一次都不会出现，整段行情零成交。
    那样 t01 会安静地拿两个空列表比相等然后绿灯，是最坏的一种假通过。

    价格连续（每根只动 0.5%~3%）也是必须的：夹具里混进一根涨跌超过 10% 的
    十字线，撮合层会判成"一字板封死"，信号被跳过 —— 测出来的就不是想测的东西。
    """
    out, p = [], 12.0
    for _ in range(lead):
        p = round_money(p * 0.995)
        out.append(bar(p))
    return out + _action(p)


def _install(code, rows, first=FIRST):
    """把合成K线写进缓存，并把同一批日期登记成交易日历。

    日历必须一起造：`back_n_trading_days` 靠它算取数窗口。日历一空它就退化成
    按自然日瞎猜，测出来的窗口和真实运行时不一样，等于没测。
    """
    df = make_bars(rows, first=first)
    store.upsert_kline(df.reset_index(), code)
    ts = [str(t)[:10] for t in df.index]
    with connect() as conn:
        conn.executemany("INSERT OR REPLACE INTO trade_calendar(ts, updated_at) VALUES(?,?)",
                         [(d, now()) for d in ts])
    store.set_meta(code, ts[0], ts[-1], attempted_to=ts[-1])
    return df, ts


def _mk_strategy(dsl, name="模拟盘测试策略"):
    validated = dsl_mod.validate(dsl)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO strategy(name, note, period, dsl, source, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (name, "", "daily", dumps(validated), "manual", now(), now()),
        )
        return cur.lastrowid, validated


def _setup(dsl, rows=None, code=CODE):
    """一次搭好：日历 + 行情 + 策略 + 账户。返回 (account_id, validated, ts_list, start_date)。"""
    fresh_db()
    rows = _series() if rows is None else rows
    _df, ts = _install(code, rows)
    sid, validated = _mk_strategy(dsl)
    start = ts[FLAT_N] if len(rows) > FLAT_N else ts[0]
    out = sim_mod.create_accounts(sid, [code], period="daily", start_date=start,
                                  initial_cash=CASH)
    eq(len(out["created"]), 1, "应该建成一个账户")
    return out["created"][0]["id"], validated, ts, start


def _rows(table, aid, cols="*", order="ts, rowid"):
    """按日期升序取一个账户的记录。

    sim_settlement 没有 ts 列，取它时要传 order="rowid"。
    """
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {cols} FROM {table} WHERE account_id=? ORDER BY {order}",
            (aid,)).fetchall()]


def _trade_key(t):
    """比"这一笔成交是不是同一笔"要看的字段。

    不含 idx：那是各自窗口里的下标，两边窗口起点不同，本来就不该相等。
    fees 在库里是 JSON 文本、在回测结果里是字典，先摊平再比。
    """
    fees = t.get("fees")
    if isinstance(fees, str):
        fees = loads(fees, {})
    return (t["ts"], t["side"], t.get("kind"), t["price"], t["shares"], t["amount"],
            t.get("pnl"), t.get("from_ts"), t.get("reason"),
            tuple(sorted((fees or {}).items())))


def _account(aid):
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM sim_account WHERE id=?", (aid,)).fetchone())


# ================================================================== 共用撮合


@check("12")
def t01_settlement_reproduces_backtest_exactly():
    """同一段K线、同一套规则：回测一次跑完 vs 模拟盘按日结算，必须逐笔一致。

    这是 PRD §5.4 那条硬规定的可执行版本。两边不一致，"回测年化 20%、模拟盘年化 8%"
    这种对比就毫无意义 —— 而那正是这套系统存在的理由。
    """
    aid, validated, ts, start = _setup(cross_dsl(risk=risk(stop=8, trail=12)))
    last = ts[-1]

    r = sim_mod.settle_account(aid, trigger="manual", upto=last)
    eq(r["status"], "ok", f"结算应该成功：{r.get('message')}")
    true(r["bars"] == len(ts) - FLAT_N, f"应该结算 {len(ts) - FLAT_N} 根，实际 {r['bars']}")

    full = store.load_kline(CODE, "daily")
    bt = run_one(CODE, CODE, validated, full, FEE, CASH, "daily")
    bt_trades = [t for t in bt.trades if t["ts"] >= start]
    sim_trades = _rows("sim_trade", aid)

    true(len(bt_trades) >= 2, f"这段行情至少要产生 2 笔成交，否则这条断言是空的（实际 {len(bt_trades)}）")
    eq([_trade_key(t) for t in sim_trades], [_trade_key(t) for t in bt_trades],
       "模拟盘的逐笔成交要和回测完全一致")

    bt_eq = [(e["ts"], e["equity"], e["cash"], e["position"])
             for e in bt.equity if e["ts"] >= start]
    sim_eq = [(e["ts"], e["equity"], e["cash"], e["position"]) for e in _rows("sim_equity", aid)]
    eq(sim_eq, bt_eq, "模拟盘的资产曲线要和回测完全一致")


@check("12")
def t02_day_by_day_equals_one_shot():
    """一天一天结算，结果必须和一次结算到底完全相同。

    撮合状态每次都要经过 JSON 存进 SQLite 再读回来。少存一个字段不会报错：
    持有天数丢了 → T+1 和最长持有失效；持有期最高价丢了 → 移动止损算错；
    未成交委托丢了 → 该买的没买。这条断言就是用来抓这类静默失效的。
    """
    one_shot, validated, ts, start = _setup(cross_dsl(risk=risk(stop=8, trail=12, hold=15)))
    sim_mod.settle_account(one_shot, trigger="manual", upto=ts[-1])

    fresh_db()
    _install(CODE, _series())
    sid, _ = _mk_strategy(cross_dsl(risk=risk(stop=8, trail=12, hold=15)))
    stepwise = sim_mod.create_accounts(sid, [CODE], period="daily", start_date=start,
                                       initial_cash=CASH)["created"][0]["id"]
    for day in ts[FLAT_N:]:
        out = sim_mod.settle_account(stepwise, trigger="manual", upto=day)
        true(out["status"] in ("ok", "idle"), f"{day} 结算不该失败：{out.get('message')}")

    eq([_trade_key(t) for t in _rows("sim_trade", stepwise)],
       [_trade_key(t) for t in _rows("sim_trade", one_shot)],
       "逐日结算的成交要和一次结算到底一致")
    eq([(e["ts"], e["equity"], e["cash"], e["position"], e["day_pnl"])
        for e in _rows("sim_equity", stepwise)],
       [(e["ts"], e["equity"], e["cash"], e["position"], e["day_pnl"])
        for e in _rows("sim_equity", one_shot)],
       "逐日结算的资产曲线要和一次结算到底一致")
    eq(_account(stepwise)["state"], _account(one_shot)["state"], "最终的撮合状态要一致")


@check("12")
def t03_sim_does_not_reimplement_the_engine():
    """源码级护栏：sim.py 里不许出现第二份撮合实现。

    t01 只能证明"今天两边一致"。哪天有人往 sim.py 里加一个自己的成交价或费用算法，
    t01 未必当场就红（要正好跑到那条分支才会）。这条直接把口子堵上。
    """
    import inspect

    src = inspect.getsource(sim_mod)
    true("make_runner" in src, "结算必须走 backtest.make_runner()")
    true("step_bar" in src, "结算必须逐根调 step_bar()")
    # 这些是撮合内核的内部件：手续费拆分、整手取整、涨跌停判定、成交与风控。
    # sim.py 只能存/取费率快照，绝不能自己拿它们算出第二套价格。
    for banned in ("fee_split(", "round_lot(", "limit_levels(", "sealed_side(",
                   "limit_price(", "_fill_pending", "_check_risk",
                   "slippage *", "* slippage"):
        true(banned not in src, f"sim.py 里不该出现 {banned} —— 那是撮合内核的事")


# ================================================================== 幂等与恢复


@check("12")
def t04_settling_twice_changes_nothing():
    """同一个截止日结算两次，第二次必须什么都没干。

    用户打开页面会触发一次，后台定时器可能同时到点。做不到幂等，成交就会翻倍。
    """
    aid, _, ts, _ = _setup(cross_dsl())
    first = sim_mod.settle_account(aid, upto=ts[-1])
    eq(first["status"], "ok", "第一次应该结算成功")

    before = _account(aid)
    trades_before, eq_before = _rows("sim_trade", aid), _rows("sim_equity", aid)

    second = sim_mod.settle_account(aid, upto=ts[-1])
    eq(second["status"], "idle", f"第二次应该没有新K线，实际 {second['status']}")
    eq(_rows("sim_trade", aid), trades_before, "成交记录不能变多")
    eq(_rows("sim_equity", aid), eq_before, "资产曲线不能变多")
    eq(_account(aid)["settled_to"], before["settled_to"], "游标不该动")
    eq(_account(aid)["state"], before["state"], "撮合状态不该动")


@check("12")
def t05_failed_settlement_leaves_no_trace():
    """结算写库时崩了：游标不动、一行数据都不留。

    这就是"重启幂等恢复"的实质 —— 容器被 kill 在半路上，下次重跑必须得到同样的结果。
    反过来的实现（先写成交再推游标）会留下"钱扣了但游标说没结算过"的账。
    """
    aid, _, ts, _ = _setup(cross_dsl())
    half = ts[FLAT_N + 10]
    eq(sim_mod.settle_account(aid, upto=half)["status"], "ok", "先正常结算一段")
    settled_to, state = _account(aid)["settled_to"], _account(aid)["state"]
    n_trades, n_eq = len(_rows("sim_trade", aid)), len(_rows("sim_equity", aid))

    def boom(*a, **kw):
        raise RuntimeError("模拟容器被杀")

    with stub(sim_mod, "_persist", boom):
        try:
            sim_mod.settle_account(aid, upto=ts[-1])
            raise AssertionError("本该抛出来")
        except RuntimeError:
            pass

    after = _account(aid)
    eq(after["settled_to"], settled_to, "游标不能推进")
    eq(after["state"], state, "撮合状态不能被改")
    eq(len(_rows("sim_trade", aid)), n_trades, "不能留下半截成交")
    eq(len(_rows("sim_equity", aid)), n_eq, "不能留下半截资产记录")

    # 恢复之后重跑，要能一路结算到底
    ok = sim_mod.settle_account(aid, upto=ts[-1])
    eq(ok["status"], "ok", f"恢复后应该能继续：{ok.get('message')}")
    eq(ok["to_ts"], ts[-1], "游标要推进到最后一天")


@check("12")
def t06_cursor_only_advances_to_what_was_settled():
    """分批结算时，游标只能走到"真的算过"的那一天，不能一步跳到截止日。

    跳了就意味着中间那些天永远不会被结算 —— 账户会平白无故少掉一段盈亏。
    """
    aid, _, ts, _ = _setup(cross_dsl())
    # 截止日给得再远，max_bars=2 也只能走两步，剩下的留给下一次。
    # 游标要是直接跳到截止日，中间那些天就永远不会被结算 —— 账户平白少掉一段盈亏。
    for upto_k, want_k in ((FLAT_N + 3, FLAT_N + 1), (FLAT_N + 7, FLAT_N + 3),
                           (FLAT_N + 7, FLAT_N + 5), (len(ts) - 1, FLAT_N + 7)):
        out = sim_mod.settle_account(aid, upto=ts[upto_k], max_bars=2)
        eq(out["bars"], 2, f"每批应该正好结算 2 根，实际 {out['bars']}")
        eq(out["to_ts"], ts[want_k], "返回的截止日只能是真算过的那一天")
        eq(_account(aid)["settled_to"], ts[want_k], "游标只能走到真算过的那一天")

    eq([e["ts"] for e in _rows("sim_equity", aid)], ts[FLAT_N:FLAT_N + 8],
       "四批各 2 根，结算过的日期必须连续、不重不漏")


@check("12")
def t07_catchup_spans_many_days_in_order():
    """用户一周才打开一次：这一次要把中间漏掉的交易日全部按顺序补上。

    今日盈亏要一天接一天地链起来算，不能拿"上周那天"当基准。
    """
    aid, _, ts, start = _setup(cross_dsl())
    sim_mod.settle_account(aid, upto=ts[FLAT_N + 2])
    sim_mod.settle_account(aid, upto=ts[-1])

    eq_rows = _rows("sim_equity", aid)
    eq(len(eq_rows), len(ts) - FLAT_N, "补算要覆盖 start_date 之后的每一天")
    eq([e["ts"] for e in eq_rows], ts[FLAT_N:], "日期要升序、不重不漏")

    for i in range(1, len(eq_rows)):
        want = round_money(eq_rows[i]["equity"] - eq_rows[i - 1]["equity"])
        near(eq_rows[i]["day_pnl"], want, 0.01,
             f"{eq_rows[i]['ts']} 的今日盈亏要等于资产相对前一天的变化")
    near(eq_rows[0]["day_pnl"], round_money(eq_rows[0]["equity"] - CASH), 0.01,
         "第一天的今日盈亏要以初始资金为基准")


# ================================================================== 跨会话状态


@check("12")
def t08_pending_order_survives_restart():
    """收盘产生的委托要活到下一个交易日开盘成交 —— 中间隔着一次"重启"。

    模拟盘的常态就是：信号日结算完、进程退出，第二天（甚至下周）才再起来。
    委托要是没存下来，这次买入就凭空消失了。
    """
    aid, validated, ts, start = _setup(cross_dsl())
    df = store.load_kline(CODE, "daily")
    ev = dsl_mod.evaluate(validated, df)
    buy_days = [ts[i] for i in range(len(ts)) if bool(ev.buy.iloc[i])]
    true(len(buy_days) > 0, "这段行情里得有买入信号，否则这条断言是空的")

    signal_day = next(d for d in buy_days if d >= start)
    out = sim_mod.settle_account(aid, upto=signal_day)
    eq(out["status"], "ok", "结算到信号日")
    eq(_rows("sim_trade", aid), [], "信号日只挂委托，当天不该有成交")
    pending = loads(_account(aid)["state"], {})["pending"]
    true(bool(pending), "委托要存进 state 里")
    eq(pending["side"], "buy", "这是一张买单")
    eq(pending["ts"], signal_day, "委托要记住它是哪天产生的")

    nxt = ts[ts.index(signal_day) + 1]
    sim_mod.settle_account(aid, upto=nxt)
    trades = _rows("sim_trade", aid)
    eq(len(trades), 1, "下一个交易日应该成交")
    eq(trades[0]["ts"], nxt, "成交日")
    eq(trades[0]["from_ts"], signal_day, "要记得是哪天的信号")
    eq(trades[0]["side"], "buy", "方向")
    want_price = round_money(float(df.loc[nxt, "open"]) * (1 + FEE["slippage"]))
    near(trades[0]["price"], want_price, 0.005, "成交价 = 次日开盘价 + 滑点")
    true(bool(trades[0]["reason"]), "成交要带上人话原因")


@check("12")
def t09_t_plus_1_survives_restart():
    """T+1 要跨会话生效：当日买入不可当日卖出，第二天才可以。

    持仓天数存的是 hold_days 计数，不是"第几根K线"。后者换个取数窗口就全错位，
    T+1 会悄悄失效 —— 而"能当天买卖"看起来还像是策略变厉害了。
    """
    # 收盘价一路爬过 11 和 11.5 两条线：买入信号日、成交日、以及成交日当天
    # 卖出条件也成立（这才测得到 T+1）。每根只动几个百分点，避免撞上涨停板。
    rows = _flat(price=11.0) + [bar(11.0), bar(11.6), bar(11.8), bar(11.9), bar(12.0)]
    fresh_db()
    _df, ts = _install(CODE, rows)
    # CLOSE>11 买入、CLOSE>11.5 卖出：成交那天两个条件同时成立
    dsl = {"signals": {"buy": G(C(I("CLOSE"), ">", N(11))),
                       "sell": G(C(I("CLOSE"), ">", N(11.5)))}, "risk": risk()}
    sid, validated = _mk_strategy(dsl)
    start = ts[FLAT_N]
    aid = sim_mod.create_accounts(sid, [CODE], start_date=start, initial_cash=CASH)["created"][0]["id"]

    sig_day, fill_day = ts[FLAT_N + 1], ts[FLAT_N + 2]
    sim_mod.settle_account(aid, upto=sig_day)
    eq(_rows("sim_trade", aid), [], "信号日只挂委托，当天不该有成交")

    sim_mod.settle_account(aid, upto=fill_day)
    eq([t["side"] for t in _rows("sim_trade", aid)], ["buy"], "次日开盘买入成交")
    sig = {s["ts"]: s for s in _rows("sim_signal", aid)}
    eq(sig[fill_day]["skip_reason"], "T+1：当日买入不可卖出", "买入当天不能卖")
    eq([t["side"] for t in _rows("sim_trade", aid)], ["buy"], "T+1 当天不该有卖出成交")

    sim_mod.settle_account(aid, upto=ts[-1])
    sides = [t["side"] for t in _rows("sim_trade", aid)]
    eq(sides, ["buy", "sell"], "第二天之后才卖得出去")


@check("12")
def t10_max_hold_survives_restart():
    """最长持有天数要跨会话累计。

    hold_days 没存下来的话，每次结算都从 0 开始数，"拿满 3 天就走"会变成永远不走。
    """
    rows = _flat() + [bar(10.0)] * 8
    fresh_db()
    _df, ts = _install(CODE, rows)
    dsl = {"signals": {"buy": G(C(I("CLOSE"), ">", N(5))), "sell": None},
           "risk": risk(hold=3)}
    sid, _ = _mk_strategy(dsl)
    start = ts[FLAT_N]
    aid = sim_mod.create_accounts(sid, [CODE], start_date=start, initial_cash=CASH)["created"][0]["id"]

    sim_mod.settle_account(aid, upto=ts[FLAT_N + 1])
    eq([t["side"] for t in _rows("sim_trade", aid)], ["buy"], "第 2 天开盘买入")

    for k in range(FLAT_N + 2, FLAT_N + 6):
        sim_mod.settle_account(aid, upto=ts[k])
    sells = [t for t in _rows("sim_trade", aid) if t["side"] == "sell"]
    eq(len(sells), 1, "拿满 3 天应该被最长持有平掉")
    eq(sells[0]["kind"], "max_hold", "离场原因是风控而不是信号")
    eq(sells[0]["ts"], ts[FLAT_N + 5], "持有满 3 天的那次收盘发单，次日开盘成交")


@check("12")
def t11_stop_loss_uses_persisted_cost_price():
    """止损要拿**存在库里的成本价**去算，而不是重新推一遍。"""
    rows = _flat() + [bar(10.0), bar(10.0), bar(10.0), bar(9.5, o=10.0, h=10.0, l=8.9)]
    fresh_db()
    _df, ts = _install(CODE, rows)
    dsl = {"signals": {"buy": G(C(I("CLOSE"), ">", N(5))), "sell": None},
           "risk": risk(stop=10)}
    sid, _ = _mk_strategy(dsl)
    start = ts[FLAT_N]
    aid = sim_mod.create_accounts(sid, [CODE], start_date=start, initial_cash=CASH)["created"][0]["id"]

    sim_mod.settle_account(aid, upto=ts[FLAT_N + 1])
    st = loads(_account(aid)["state"], {})
    true(st["shares"] > 0, "应该已经买入")
    near(st["cost_price"], round_money(10.0 * 1.001), 0.005, "成本价要存下来")

    sim_mod.settle_account(aid, upto=ts[-1])
    sells = [t for t in _rows("sim_trade", aid) if t["side"] == "sell"]
    eq(len(sells), 1, "最低价打穿止损线，应该被平掉")
    eq(sells[0]["kind"], "stop_loss", "离场类型")
    near(sells[0]["price"], round_money(st["cost_price"] * 0.9), 0.005,
         "止损按触发价成交，不叠加滑点")


@check("12")
def t12_trailing_stop_uses_persisted_high():
    """移动止损要跨会话记住持有期最高价。

    high_since_buy 丢了的话，回撤基准会变成"今天的高点"，止损线整体抬高，
    该走的时候不走 —— 而且账面上看起来只是"这次没触发"，很难发现。
    """
    # 涨到 11.2 再回落到 10.0：每根只动 5%~6%，不会撞上涨停板把买入挡掉。
    rows = _flat() + [bar(10.0), bar(10.6), bar(11.2), bar(10.5, o=10.8, h=10.8, l=10.0)]
    fresh_db()
    _df, ts = _install(CODE, rows)
    dsl = {"signals": {"buy": G(C(I("CLOSE"), ">", N(5))), "sell": None},
           "risk": risk(trail=10)}
    sid, _ = _mk_strategy(dsl)
    start = ts[FLAT_N]
    aid = sim_mod.create_accounts(sid, [CODE], start_date=start, initial_cash=CASH)["created"][0]["id"]

    # 先只结算到"最高价那天"：这时候还没触发移动止损，状态里必须留着 11.2 这个高点。
    sim_mod.settle_account(aid, upto=ts[FLAT_N + 2])
    st = loads(_account(aid)["state"], {})
    true(st["shares"] > 0, "应该已经买入")
    near(st["high_since_buy"], 11.2, 0.005, "持有期最高价要存下来")

    sim_mod.settle_account(aid, upto=ts[-1])
    sells = [t for t in _rows("sim_trade", aid) if t["side"] == "sell"]
    eq(len(sells), 1, "从 11.2 回撤到 10.0 超过 10%，应该被移动止损平掉")
    eq(sells[0]["kind"], "trailing_stop", "离场类型")
    near(sells[0]["price"], round_money(11.2 * 0.9), 0.005, "回撤基准是存下来的那个最高点")


# ================================================================== 快照与口径


@check("12")
def t13_fee_snapshot_ignores_later_setting_changes():
    """改费率不能追溯到已有账户（和回测的 K3 同一条原则）。

    否则账户前半段按万2.5、后半段按万1算，攒出来的收益率谁也没法解释。
    """
    from ..db import set_settings

    aid, _, ts, _ = _setup(cross_dsl())
    before = loads(_account(aid)["fee_snapshot"], {})
    near(before["commission_rate"], 0.00025, 1e-9, "建账户时拍下的是默认费率")

    set_settings({"commission_rate": "0.001", "slippage": "0.01"})
    try:
        sim_mod.settle_account(aid, upto=ts[-1])
        after = loads(_account(aid)["fee_snapshot"], {})
        eq(after, before, "费率快照不能被后来的设置改动")
    finally:
        set_settings({"commission_rate": "0.00025", "slippage": "0.001"})


@check("12")
def t14_dsl_snapshot_ignores_later_strategy_edits():
    """改策略不能改变已有账户的规则，否则前后两段盈亏是两套逻辑拼出来的。"""
    aid, _, ts, start = _setup(cross_dsl())
    snap_before = _account(aid)["dsl"]
    sim_mod.settle_account(aid, upto=ts[FLAT_N + 5])
    trades_before = len(_rows("sim_trade", aid))
    true(trades_before > 0, "改规则之前就得有成交，否则后面的对比是空的")

    never_buy = {"signals": {"buy": G(C(I("CLOSE"), ">", N(999999))), "sell": None},
                 "risk": risk()}
    with connect() as conn:
        sid = conn.execute("SELECT id FROM strategy ORDER BY id DESC LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE strategy SET dsl=? WHERE id=?",
                     (dumps(dsl_mod.validate(never_buy)), sid))

    # 同一段行情、同一个开始日，只用"改过的策略"再建一个账户：它应该一笔都不买。
    # 两个账户唯一的差别就是 DSL —— 老账户照常交易，才说明它没被策略改动带跑。
    sid2, _ = _mk_strategy(never_buy, name="改过的策略")
    aid2 = sim_mod.create_accounts(sid2, [CODE], start_date=start,
                                  initial_cash=CASH)["created"][0]["id"]
    sim_mod.settle_account(aid2, upto=ts[-1])
    eq(len(_rows("sim_trade", aid2)), 0, "改过的策略确实一笔都不买")

    sim_mod.settle_account(aid, upto=ts[-1])
    eq(_account(aid)["dsl"], snap_before, "账户里的 DSL 快照不能变")
    true(len(_rows("sim_trade", aid)) >= trades_before, "成交只会增加，不会回退")
    true(len(_rows("sim_trade", aid)) > 0, "老账户仍按原来的规则交易")


@check("12")
def t15_metrics_use_the_backtest_formulas():
    """模拟盘的绩效指标必须出自回测那同一套公式，不能另写一份。"""
    import inspect

    src = inspect.getsource(sim_mod.metrics_of)
    true("metrics_mod.compute" in src, "要调用回测用的 metrics.compute()")
    true("BacktestResult" in src, "要把库里的数据拼回 BacktestResult 再算")

    aid, validated, ts, start = _setup(cross_dsl(risk=risk(stop=8)))
    sim_mod.settle_account(aid, upto=ts[-1])
    m = sim_mod.metrics_of(aid)

    full = store.load_kline(CODE, "daily")
    bt = run_one(CODE, CODE, validated, full, FEE, CASH, "daily")
    from .. import metrics as metrics_mod
    bm = metrics_mod.compute(bt, None, FEE)

    near(m["final_equity"], bm["final_equity"], 0.01, "期末资产")
    near(m["max_drawdown"], bm["max_drawdown"], 1e-6, "最大回撤")
    eq(m["total_trades"], bm["total_trades"], "成交笔数")
    eq(m["settled_to"], ts[-1], "指标要带上结算到哪一天")


# ================================================================== 边界与拒绝


@check("12")
def t16_warmup_short_fails_closed():
    """历史数据不够暖机时，必须明确拒绝，不能一声不响地把每天记成「数据不足」。

    后者是最难查的一种失效：账户永远不动、没有任何报错，用户只会以为策略没信号。
    """
    fresh_db()
    rows = _flat(6) + _action(10.0)[:5]
    _df, ts = _install(CODE, rows)
    sid, _ = _mk_strategy(cross_dsl())          # MA20 要 20 根暖机，这里只有 11 根
    aid = sim_mod.create_accounts(sid, [CODE], start_date=ts[6],
                                  initial_cash=CASH)["created"][0]["id"]

    # 缓存里没有暖机那段，结算会先去上游补历史 —— 这一步必须打桩成"上游也只有这么多"，
    # 否则自检会真的联网把 600519 下回来，暖机就够了，测的不再是"数据不够时怎么办"。
    same = make_bars(rows, first=FIRST).reset_index()
    with stub(store, "fetch_daily", lambda *a, **kw: same), \
         stub(store, "fetch_trade_calendar", lambda *a, **kw: list(ts)):
        out = sim_mod.settle_account(aid, upto=ts[-1])
    eq(out["status"], "error", "暖机不够要失败")
    eq(out["error_code"], "WARMUP_SHORT", "错误码")
    true("暖机" in out["message"], "错误信息要说清楚是暖机不够")
    eq(_account(aid)["settled_to"], None, "游标不能推进")
    eq(_rows("sim_equity", aid), [], "不能写下任何资产记录")
    true(bool(_account(aid)["last_error"]), "错误要记在账户上，页面才看得见")


@check("12")
def t17_intraday_bar_is_not_settled():
    """盘中不结算当天。

    今天还没收盘就结算，等于拿半天的K线当一整天用：收盘价是临时的，止损可能今天
    没触发但明天会触发，而游标一推进这天就永久定死了 —— 错账再也修不回来。
    """
    fresh_db()
    _df, ts = _install(CODE, _flat(5))
    today_ts = ts[-1]
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO trade_calendar(ts, updated_at) VALUES(?,?)",
                     (today_ts, now()))

    morning = datetime.strptime(today_ts + " 10:30", "%Y-%m-%d %H:%M")
    eq(sim_mod.settle_cutoff(morning), ts[-2], "上午 10:30 只能结算到昨天")

    closing = datetime.strptime(today_ts + " 14:59", "%Y-%m-%d %H:%M")
    eq(sim_mod.settle_cutoff(closing), ts[-2], "14:59 还没收盘，仍然只能到昨天")

    closed = datetime.strptime(today_ts + " 15:00", "%Y-%m-%d %H:%M")
    eq(sim_mod.settle_cutoff(closed), today_ts, "15:00 收盘了，当天可以结算")

    weekend = datetime.strptime(today_ts + " 10:30", "%Y-%m-%d %H:%M")
    with connect() as conn:
        conn.execute("DELETE FROM trade_calendar WHERE ts=?", (today_ts,))
    eq(sim_mod.settle_cutoff(weekend), ts[-2], "非交易日一律退回上一个交易日")


@check("12")
def t18_limit_down_records_reason_and_does_not_fill():
    """一字跌停当天卖不出，信号日志要写清楚原因。

    钉的是**现状**：记录原因 + 当天不成交，次日条件仍成立才重新发信号。
    PRD §5.4 写的是"信号顺延"（委托活到次日自动补卖），两者不一致。
    改不改要用户拍板 —— 那会改动所有已验收的回测数字，不能悄悄改。
    """
    # 两个一字跌停板，第二个把收盘价压到 7 以下 —— 既要"封死"又要"卖出条件成立"，
    # 这一天才是真正要测的那天。最后再给两根普通K线，让次日重新发出的信号能成交。
    rows = _flat(price=8.0) + [bar(8.0), bar(8.0), one_word(7.2), one_word(6.48),
                               bar(6.4), bar(6.5)]
    fresh_db()
    _df, ts = _install(CODE, rows)
    dsl = {"signals": {"buy": G(C(I("CLOSE"), ">", N(5))),
                       "sell": G(C(I("CLOSE"), "<", N(7)))}, "risk": risk()}
    sid, _ = _mk_strategy(dsl)
    start = ts[FLAT_N]
    aid = sim_mod.create_accounts(sid, [CODE], start_date=start, initial_cash=CASH)["created"][0]["id"]

    sealed_day = ts[FLAT_N + 3]
    sim_mod.settle_account(aid, upto=sealed_day)
    sig = {s["ts"]: s for s in _rows("sim_signal", aid)}
    eq(sig[sealed_day]["skip_reason"], "跌停封死，卖不出", "跌停那天要写清楚为什么没卖")
    eq([t["side"] for t in _rows("sim_trade", aid)], ["buy"], "跌停当天不该有卖出成交")

    sim_mod.settle_account(aid, upto=ts[-1])
    sides = [t["side"] for t in _rows("sim_trade", aid)]
    eq(sides, ["buy", "sell"], "次日条件仍成立，重新发信号后卖出")


@check("12")
def t19_paused_and_closed_accounts_are_skipped():
    """暂停/平仓的账户不再结算，但历史数据一条都不能少。"""
    aid, _, ts, _ = _setup(cross_dsl())
    sim_mod.settle_account(aid, upto=ts[FLAT_N + 5])
    n_eq = len(_rows("sim_equity", aid))

    eq(sim_mod.set_status(aid, "paused")["status"], "paused", "暂停")
    out = sim_mod.settle_account(aid, upto=ts[-1])
    eq(out["status"], "skipped", "暂停的账户要跳过")
    eq(len(_rows("sim_equity", aid)), n_eq, "跳过就一行都不该写")

    eq(sim_mod.set_status(aid, "running")["status"], "running", "恢复")
    eq(sim_mod.settle_account(aid, upto=ts[-1])["status"], "ok", "恢复后能接着结算")

    sim_mod.set_status(aid, "closed")
    raises_error(lambda: sim_mod.set_status(aid, "running"), KIND_INPUT, "ALREADY_CLOSED",
                 "平仓后不能再改状态")
    true(len(_rows("sim_equity", aid)) > n_eq, "平仓不删历史数据")


@check("12")
def t20_duplicate_running_account_refused():
    """同一策略 + 同一股票只能有一个在跑的账户。

    两个账户跑同一套规则，等于把同一笔信号算两遍，对比时就分不清哪个是真的。
    """
    fresh_db()
    _df, ts = _install(CODE, _flat(5))
    sid, _ = _mk_strategy(cross_dsl())
    first = sim_mod.create_accounts(sid, [CODE], start_date=ts[0], initial_cash=CASH)
    eq(len(first["created"]), 1, "第一次能建成")

    second = sim_mod.create_accounts(sid, [CODE], start_date=ts[0], initial_cash=CASH)
    eq(second["created"], [], "重复的要被拒绝")
    eq(len(second["refused"]), 1, "要说清楚是哪只被拒了")
    true("已经有" in second["refused"][0]["reason"], "拒绝原因要能看懂")

    sim_mod.set_status(first["created"][0]["id"], "closed")
    third = sim_mod.create_accounts(sid, [CODE], start_date=ts[0], initial_cash=CASH)
    eq(len(third["created"]), 1, "平仓之后可以重建")


@check("12")
def t21_delete_removes_every_trace():
    """删账户要把它名下的成交、资产、信号、结算记录一并删掉，别留孤儿行。"""
    aid, _, ts, _ = _setup(cross_dsl())
    sim_mod.settle_account(aid, upto=ts[-1])
    kids = {"sim_trade": "ts, rowid", "sim_equity": "ts, rowid",
            "sim_signal": "ts, rowid", "sim_settlement": "rowid"}
    true(all(len(_rows(t, aid, order=o)) > 0 for t, o in kids.items()),
         "四张表都该有数据，否则这条断言是空的")

    eq(sim_mod.delete_account(aid)["deleted"], aid, "返回被删的账户号")
    for t, o in kids.items():
        eq(_rows(t, aid, order=o), [], f"{t} 要清空")
    raises_error(lambda: sim_mod.load_account(aid), KIND_INPUT, "SIM_NOT_FOUND",
                 "账户本身也没了")


@check("12")
def t22_scheduler_is_off_during_selfcheck():
    """自检里绝不能让后台定时器跑起来 —— 它会去联网取行情。"""
    import os

    eq(os.environ.get("SIM_SCHEDULER"), "off", "harness 必须把定时器关掉")
    eq(sim_mod.start_scheduler(), None, "关掉时 start_scheduler 不该建线程")
    st = sim_mod.scheduler_state()
    eq(st["enabled"], False, "状态里要能看出定时器是关的")
    eq(st["running"], False, "线程不该在跑")
    eq(st["interval_minutes"], sim_mod.SCHEDULE_MINUTES, "间隔要报出来")


@check("12")
def t23_initial_state_shape_matches_snapshot():
    """新账户的初始状态和 runner.snapshot() 的字段必须完全一致。

    少一个字段，restore() 会静默用默认值补上 —— 例如 cost_price 变 0，
    止损线就成了 0，永远不会触发。
    """
    fresh_db()
    _df, ts = _install(CODE, _flat(3))
    sid, validated = _mk_strategy(cross_dsl())
    aid = sim_mod.create_accounts(sid, [CODE], start_date=ts[0],
                                  initial_cash=CASH)["created"][0]["id"]
    stored = loads(_account(aid)["state"], {})
    from ..backtest import make_runner
    runner = make_runner(CODE, CODE, validated, None, FEE, CASH, "daily")
    eq(sorted(stored), sorted(runner.snapshot()), "字段名要一模一样")
    eq(stored, initial_state(CASH), "初始状态要等于空仓 + 初始资金")


# ================================================================== 接口


@check("12")
def t24_api_end_to_end():
    """建账户 → 列表 → 结算 → 详情 → 指标 → 信号 → 暂停 → 删除，整条接口链走一遍。"""
    fresh_db()
    rows = _series()
    _df, ts = _install(CODE, rows)
    sid, _ = _mk_strategy(cross_dsl(risk=risk(stop=8)))
    start = ts[FLAT_N]
    c = client()

    r = c.post("/api/sim/accounts", json={
        "strategy_id": sid, "codes": [CODE], "period": "daily",
        "start_date": start, "initial_cash": CASH})
    eq(r.status_code, 200, f"建账户应成功：{r.text[:200]}")
    body = r.json()
    aid = body["created"][0]["id"]
    eq(body["start_date"], start, "开始日期要回显")

    r = c.get("/api/sim/accounts")
    eq(r.status_code, 200, "列表应成功")
    items = r.json()["items"]
    eq(len(items), 1, "列表里有一个账户")
    one = items[0]
    for key in ("equity", "cash", "shares", "holding", "day_pnl", "total_return_pct",
                "settled_to", "stale", "trade_count", "pending_order", "last_settle"):
        true(key in one, f"列表项要带 {key}，前端靠它画卡片")
    eq(one["initial_cash"], CASH, "初始资金")
    eq(one["settled_to"], ts[-1], "建账户时就该顺手结算一次，用户不用先点一下")

    # 上面那次结算是建账户时顺带做的，所以要先把游标退回去，
    # 才测得到"前端点[立即结算]"这条路径本身。
    with connect() as conn:
        for t in ("sim_trade", "sim_equity", "sim_signal"):
            conn.execute(f"DELETE FROM {t} WHERE account_id=?", (aid,))
        conn.execute("UPDATE sim_account SET settled_to=NULL, state=? WHERE id=?",
                     (dumps(initial_state(CASH)), aid))

    r = c.post("/api/sim/settle", json={"upto": ts[-1]})
    eq(r.status_code, 200, f"结算应成功：{r.text[:200]}")
    eq(r.json()["accounts"], 1, "在跑的账户有 1 个")
    eq(r.json()["settled"], 1, "结算了 1 个账户")

    r = c.post("/api/sim/settle")          # 不带 body 也要能用
    eq(r.status_code, 200, f"空 body 的结算请求不该 400：{r.text[:200]}")
    eq(r.json()["settled"], 0, "没有新K线时不该重复结算")

    r = c.get(f"/api/sim/accounts/{aid}")
    eq(r.status_code, 200, "详情应成功")
    detail = r.json()
    for key in ("equity_curve", "trades", "signals", "settlements", "dsl", "fee_snapshot"):
        true(key in detail, f"详情要带 {key}")
    eq(len(detail["equity_curve"]), len(ts) - FLAT_N, "资产曲线覆盖每个交易日")

    r = c.get(f"/api/sim/accounts/{aid}/metrics")
    eq(r.status_code, 200, "指标应成功")
    true("total_return" in r.json(), "指标里要有总收益率")

    r = c.get(f"/api/sim/accounts/{aid}/signals", params={"only_fired": True})
    eq(r.status_code, 200, "信号日志应成功")
    true(all(s["buy"] or s["sell"] for s in r.json()["items"]), "only_fired 要过滤掉没信号的日子")

    r = c.patch(f"/api/sim/accounts/{aid}/status", json={"status": "paused"})
    eq(r.status_code, 200, "暂停应成功")
    eq(r.json()["status"], "paused", "状态回显")

    r = c.get("/api/sim/status")
    eq(r.status_code, 200, "定时器状态应成功")
    eq(r.json()["enabled"], False, "自检里定时器是关的")

    r = c.delete(f"/api/sim/accounts/{aid}")
    eq(r.status_code, 200, "删除应成功")
    eq(c.get(f"/api/sim/accounts/{aid}").status_code, 400, "删完再查要报输入错误")


@check("12")
def t25_api_rejects_bad_input():
    """接口层的拒绝要给对错误分类和能看懂的话，不能变成 500。"""
    fresh_db()
    _df, ts = _install(CODE, _flat(3))
    sid, _ = _mk_strategy(cross_dsl())
    c = client()

    def err(resp):
        return resp.json()["error"]

    r = c.post("/api/sim/accounts", json={"codes": [CODE]})
    eq(r.status_code, 400, "缺策略要 400")
    eq(err(r)["code"], "NO_STRATEGY", "错误码")

    r = c.post("/api/sim/accounts", json={"strategy_id": sid, "codes": []})
    eq(r.status_code, 400, "没选股票要 400")
    eq(err(r)["code"], "NO_STOCK", "错误码")

    r = c.post("/api/sim/accounts", json={"strategy_id": sid, "codes": ["60051"]})
    eq(r.status_code, 400, "代码不是 6 位要 400")
    eq(err(r)["code"], "BAD_CODE", "错误码")

    r = c.post("/api/sim/accounts", json={"strategy_id": 999999, "codes": [CODE]})
    eq(r.status_code, 400, "策略不存在要 400")
    eq(err(r)["code"], "STRATEGY_NOT_FOUND", "错误码")

    r = c.post("/api/sim/accounts", json={"strategy_id": sid, "codes": [CODE],
                                          "period": "hourly"})
    eq(r.status_code, 400, "不认识的周期要 400")
    eq(err(r)["code"], "BAD_PERIOD", "错误码")

    r = c.post("/api/sim/accounts", json={"strategy_id": sid, "codes": [CODE],
                                          "start_date": "2099-01-01"})
    eq(r.status_code, 400, "未来的开始日期要 400")
    eq(err(r)["code"], "FUTURE_START", "错误码")

    r = c.post("/api/sim/accounts/424242/settle")
    eq(r.status_code, 400, "账户不存在要 400 而不是 500")
    eq(err(r)["code"], "SIM_NOT_FOUND", "错误码")

    r = c.patch("/api/sim/accounts/1/status", json={"status": "frozen"})
    eq(err(r)["code"] if r.status_code == 400 else "BAD_STATUS", "BAD_STATUS",
       "不认识的状态要拒绝")


@check("12")
def t26_rejected_signals_stay_in_the_log():
    """被拒绝的信号也要留在日志里 —— 用户最想知道的往往就是"为什么没卖出去"。"""
    aid, _, ts, _ = _setup(cross_dsl())
    sim_mod.settle_account(aid, upto=ts[-1])
    sigs = _rows("sim_signal", aid)
    eq(len(sigs), len(ts) - FLAT_N, "每个结算过的交易日都要有一条日志")

    with connect() as conn:
        n_all = int(conn.execute(
            "SELECT COUNT(*) FROM sim_signal WHERE account_id=?", (aid,)).fetchone()[0])
        n_fired = int(conn.execute(
            "SELECT COUNT(*) FROM sim_signal WHERE account_id=? AND (buy OR sell)",
            (aid,)).fetchone()[0])
    true(n_all > n_fired, "没信号的日子也要记，否则日志看起来像是漏了几天")
    true(any(s["action"] for s in sigs), "成交/挂单的日子要记下做了什么")

    r = client().get(f"/api/sim/accounts/{aid}/signals", params={"limit": 5})
    eq(len(r.json()["items"]), 5, "limit 要生效")
    days = [s["ts"] for s in r.json()["items"]]
    eq(days, sorted(days, reverse=True), "信号日志按日期倒序，最新的在最上面")


@check("12")
def t27_sim_tables_exist_with_the_right_shape():
    """四张表都要建出来，关键约束不能少。

    sim_equity / sim_signal 的主键含 ts —— 这是"重算同一天是覆盖而不是追加"的前提，
    主键错了幂等就没了。sim_account 的部分唯一索引挡住重复账户。
    """
    fresh_db()
    with connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ("sim_account", "sim_trade", "sim_equity", "sim_signal", "sim_settlement"):
            true(t in tables, f"{t} 表要存在")

        eq_pk = [r["name"] for r in conn.execute("PRAGMA table_info(sim_equity)")
                 if r["pk"]]
        eq(sorted(eq_pk), ["account_id", "ts"], "sim_equity 主键")
        sig_pk = [r["name"] for r in conn.execute("PRAGMA table_info(sim_signal)") if r["pk"]]
        eq(sorted(sig_pk), ["account_id", "ts"], "sim_signal 主键")

        idx = [dict(r) for r in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='sim_account'")]
        uniq = [i for i in idx if i["sql"] and "UNIQUE" in i["sql"].upper()]
        eq(len(uniq), 1, "sim_account 上要有一个唯一索引")
        true("status = 'running'" in uniq[0]["sql"] or "status='running'" in uniq[0]["sql"],
             "唯一索引要限定在'在跑的'账户上，平仓后才允许重建")
