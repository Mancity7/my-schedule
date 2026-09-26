"""回测撮合内核：把"策略条件 + K线 + 费率"变成"交易记录 + 资产曲线 + 绩效指标"。

四条硬规矩（都对应 PRD 验收）：
1. **撮合顺序**（PRD §5.4）：① 上一根收盘产生的委托 → ② 本根开盘成交
   （涨跌停/T+1/整手检查）→ ③ 盘中风控（止损/止盈/移动止损）→ ④ 收盘估值 + 决策。
   顺序不能乱：先成交旧委托再评估新信号，才不会"今天刚买今天就卖"（T+1，F4）。
2. **风控成交不再叠加滑点**（F8/F9）：止损/止盈/移动止损的触发价就是成交价；
   只有策略信号（买卖条件）的成交按开盘价 ± 滑点。
3. **封死判定**：一字涨停（low >= up - eps）当天买不进；一字跌停（high <= down + eps）
   当天卖不出。成交量不为 0 也算封死——看的是价格，不是量。
4. **费用处处走 `round_money`**（F10）：全账误差 < 0.01 元。

暖机段和 NaN 一律记「数据不足·跳过」，不算条件不成立（F17）。
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import dsl as dsl_mod
from . import indicators as ind
from . import explain
from .config import board_of, limit_price, limit_rate, round_money
from .errors import integrity_error

LOT = 100  # 1 手 = 100 股
_EPS = 1e-6  # 浮点比较容差，避免 10.000000001 != 10.0 的误判


# ------------------------------------------------------------------ 费用


def fee_split(price: float, shares: int, side: str, fee: dict) -> dict:
    """一笔成交的费用拆分。side='buy' 或 'sell'。

    返回 {commission, stamp, transfer, total}，单位都是元。
    印花税只在卖出时收；其他双向。
    """
    amount = round_money(price * shares)
    commission = max(round_money(amount * fee["commission_rate"]), fee["commission_min"])
    transfer = round_money(amount * fee["transfer_fee_rate"])
    stamp = round_money(amount * fee["stamp_duty_rate"]) if side == "sell" else 0.0
    return {
        "commission": commission,
        "stamp": stamp,
        "transfer": transfer,
        "total": round_money(commission + stamp + transfer),
    }


def round_lot(cash: float, price: float) -> int:
    """全仓买入：按当前现金能买到的最大整手股数。买不到 1 手就 0。"""
    if price <= 0 or cash <= 0:
        return 0
    return int(cash // price // LOT) * LOT


# ------------------------------------------------------------------ 涨跌停 / 封死


def limit_levels(prev_close: float, code: str, name: str = "") -> tuple[float, float]:
    """返回 (涨停价, 跌停价)。"""
    rate = limit_rate(code, name)
    return limit_price(prev_close, rate, +1), limit_price(prev_close, rate, -1)


def sealed_side(open_: float, high: float, low: float, close: float,
                up: float, down: float) -> str | None:
    """判断这根K线是否封死涨停/跌停。返回 'up' / 'down' / None。

    封死 = 最低价 >= 涨停价（全天都在涨停价或之上，没下来过）
         或 最高价 <= 跌停价（全天都在跌停价或之下）。
    成交量不为 0 也算封死——封板当天也有成交，看的是价格是否被"钉"在板上。
    """
    if low >= up - _EPS:
        return "up"
    if high <= down + _EPS:
        return "down"
    return None


def _dead(bar: dict) -> bool:
    """停牌日：没有一笔成交（量 = 0 且价格不动）。"""
    return bool(bar.get("suspended")) or (bar.get("volume", 0) == 0
                                          and bar["open"] == bar["close"]
                                          and bar["high"] == bar["low"])


# ------------------------------------------------------------------ 账户 / 结果


@dataclass
class Account:
    cash: float
    shares: int = 0
    cost_price: float = 0.0  # 不含费的每股成本，用于风控百分比
    cost: float = 0.0  # 总成本（含费），用于算单笔盈亏
    buy_idx: int = -1  # 买入那根的索引（T+1 用）
    buy_ts: str = ""
    high_since_buy: float = 0.0  # 持有期间最高价（移动止损用）

    def equity(self, price: float) -> float:
        return round_money(self.cash + self.shares * price)

    def position_value(self, price: float) -> float:
        return round_money(self.shares * price)


@dataclass
class BacktestResult:
    code: str
    name: str
    period: str
    initial_cash: float
    dsl: dict
    fee: dict
    trades: list[dict] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)  # [{ts, equity, cash, position, bench}]
    signal_log: list[dict] = field(default_factory=list)  # 每根的信号 + 未成交原因
    quality: dict = field(default_factory=dict)  # 数据质量：warmup/gaps/insufficient
    trace: dict = field(default_factory=dict)  # 溯源：ts, lines, nodes, signals
    final_equity: float = 0.0
    final_cash: float = 0.0
    final_shares: int = 0
    status: str = "done"
    error: str = ""
    error_code: str = ""

    def payload(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "period": self.period,
            "initial_cash": self.initial_cash,
            "final_equity": self.final_equity,
            "status": self.status,
            "error": self.error,
            "error_code": self.error_code,
            "trades": self.trades,
            "equity": self.equity,
            "signal_log": self.signal_log,
            "quality": self.quality,
            "trace": self.trace,
        }


# ------------------------------------------------------------------ 撮合器


class _Runner:
    """单只股票的回测撮合器。"""

    def __init__(self, code: str, name: str, validated: dict, df: pd.DataFrame,
                 fee: dict, initial_cash: float, period: str):
        self.code = code
        self.name = name
        self.validated = validated
        self.df = df
        self.fee = fee
        self.initial_cash = initial_cash
        self.period = period
        self.account = Account(cash=initial_cash)
        self.trades: list[dict] = []
        self.equity: list[dict] = []
        self.signal_log: list[dict] = []
        self.pending: dict | None = None  # 上一根收盘产生的委托
        self._eval_result = None
        self.board = board_of(code, name)
        self.rate = limit_rate(code, name)

    def run(self) -> BacktestResult:
        df = self.df
        if df is None or not len(df):
            return BacktestResult(
                code=self.code, name=self.name, period=self.period,
                initial_cash=self.initial_cash, dsl=self.validated, fee=self.fee,
                status="error", error="K线为空", error_code="EMPTY_KLINE",
            )

        eval_result = dsl_mod.evaluate(self.validated, df)
        self._eval_result = eval_result
        buy_sig = eval_result.buy
        sell_sig = eval_result.sell
        insuf = eval_result.insufficient
        warmup = eval_result.warmup

        bars = df.reset_index()
        ts_list = [str(t)[:10] for t in bars["ts" if "ts" in bars.columns else bars.index]]
        if "ts" not in bars.columns:
            bars = bars.reset_index()
        n = len(bars)

        skipped_warmup = 0
        skipped_gap = 0
        buy_indices: list[int] = []
        sell_indices: list[int] = []

        for i in range(n):
            row = bars.iloc[i]
            ts = ts_list[i]
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            prev_close = float(bars.iloc[i - 1]["close"]) if i > 0 else o
            up, down = limit_levels(prev_close, self.code, self.name)
            sealed = sealed_side(o, h, l, c, up, down)
            dead = _dead({"open": o, "high": h, "low": l, "close": c, "volume": float(row.get("volume", 0))})

            buy_fire = bool(buy_sig.iloc[i]) if i < len(buy_sig) else False
            sell_fire = bool(sell_sig.iloc[i]) if i < len(sell_sig) else False
            is_insuf = bool(insuf.iloc[i]) if i < len(insuf) else False

            if is_insuf:
                if i < warmup:
                    skipped_warmup += 1
                else:
                    skipped_gap += 1

            signal_entry = {"ts": ts, "idx": i, "buy": buy_fire, "sell": sell_fire,
                            "insufficient": is_insuf, "skip_reason": None}

            # ① 上一根收盘产生的委托 → ② 本根开盘成交
            if self.pending is not None:
                self._fill_pending(i, ts, o, h, l, c, up, down, sealed, dead, signal_entry)

            # ③ 盘中风控（止损/止盈/移动止损）
            if self.account.shares > 0 and not dead:
                self._check_risk(i, ts, o, h, l, c, signal_entry)

            # ④ 收盘估值 + 决策
            if self.account.shares > 0:
                self.account.high_since_buy = max(self.account.high_since_buy, h)

            price_for_equity = c
            eq = self.account.equity(price_for_equity)
            self.equity.append({"ts": ts, "equity": eq, "cash": self.account.cash,
                                "position": self.account.position_value(price_for_equity)})

            # 收盘决策：买入 / 卖出委托（下一根开盘成交）
            if self.account.shares == 0 and buy_fire and not is_insuf:
                if sealed == "up":
                    signal_entry["skip_reason"] = "涨停封死，买不进"
                else:
                    self.pending = {"side": "buy", "ts": ts, "idx": i, "bar_idx": i}
                    signal_entry["action"] = "pending_buy"

            if self.account.shares > 0 and sell_fire and not is_insuf:
                if i == self.account.buy_idx:
                    signal_entry["skip_reason"] = "T+1：当日买入不可卖出"
                elif sealed == "down":
                    signal_entry["skip_reason"] = "跌停封死，卖不出"
                else:
                    self.pending = {"side": "sell", "ts": ts, "idx": i, "bar_idx": i}
                    signal_entry["action"] = "pending_sell"

            # 最长持有：持有天数达到上限 → 收盘决策卖出，次根开盘成交
            if self.pending is None and self.account.shares > 0 and not is_insuf:
                max_hold = (self.validated.get("risk") or {}).get("max_hold_days")
                if max_hold is not None and self.account.buy_idx >= 0:
                    held = i - self.account.buy_idx
                    if held >= max_hold:
                        if i == self.account.buy_idx:
                            signal_entry["skip_reason"] = signal_entry.get("skip_reason") or \
                                "T+1：当日买入不可卖出"
                        elif sealed == "down":
                            signal_entry["skip_reason"] = signal_entry.get("skip_reason") or \
                                "跌停封死，卖不出"
                        else:
                            self.pending = {"side": "sell", "ts": ts, "idx": i,
                                            "reason": f"最长持有 {max_hold} 天"}
                            signal_entry["action"] = "pending_max_hold_sell"

            if buy_fire:
                buy_indices.append(i)
            if sell_fire:
                sell_indices.append(i)

            self.signal_log.append(signal_entry)

        # 构建 trace
        trace = self._build_trace(ts_list, eval_result, buy_indices, sell_indices)

        # 数据质量
        total_bars = n
        valid_bars = total_bars - skipped_warmup - skipped_gap
        quality = {
            "total_bars": total_bars,
            "warmup_bars": warmup,
            "skipped_warmup": skipped_warmup,
            "skipped_gap": skipped_gap,
            "valid_bars": valid_bars,
            "gap_pct": round_money(skipped_gap / total_bars * 100.0, 2) if total_bars else 0.0,
        }

        result = BacktestResult(
            code=self.code, name=self.name, period=self.period,
            initial_cash=self.initial_cash, dsl=self.validated, fee=self.fee,
            trades=self.trades, equity=self.equity, signal_log=self.signal_log,
            quality=quality, trace=trace,
            final_equity=self.account.equity(float(bars.iloc[-1]["close"])),
            final_cash=self.account.cash,
            final_shares=self.account.shares,
        )
        return result

    def _fill_pending(self, i: int, ts: str, o: float, h: float, l: float, c: float,
                      up: float, down: float, sealed: str | None, dead: bool,
                      signal_entry: dict) -> None:
        """处理上一根收盘产生的委托：本根开盘成交。"""
        p = self.pending
        if p is None:
            return
        self.pending = None

        if dead:
            signal_entry["skip_reason"] = signal_entry.get("skip_reason") or "停牌，委托取消"
            return

        if p["side"] == "buy":
            if sealed == "up":
                signal_entry["skip_reason"] = "开盘涨停封死，买不进"
                return
            price = round_money(o * (1.0 + self.fee["slippage"]))
            shares = round_lot(self.account.cash, price)
            if shares <= 0:
                signal_entry["skip_reason"] = "资金不足一手"
                return
            cost = round_money(price * shares)
            fees = fee_split(price, shares, "buy", self.fee)
            total_cost = round_money(cost + fees["total"])
            if total_cost > self.account.cash:
                shares -= LOT
                if shares <= 0:
                    signal_entry["skip_reason"] = "资金不足一手（含费用）"
                    return
                cost = round_money(price * shares)
                fees = fee_split(price, shares, "buy", self.fee)
                total_cost = round_money(cost + fees["total"])
            self.account.cash = round_money(self.account.cash - total_cost)
            self.account.shares = shares
            self.account.cost_price = price
            self.account.cost = cost
            self.account.buy_idx = i
            self.account.buy_ts = p["ts"]
            self.account.high_since_buy = h
            bar_idx = p.get("bar_idx", 0)
            reason = dsl_mod.describe_trigger(self.validated, "buy",
                                               self._eval_result.nodes, bar_idx)
            self.trades.append({
                "side": "buy", "ts": ts, "idx": i, "price": price, "shares": shares,
                "amount": cost, "fees": fees, "reason": reason,
                "from_ts": p["ts"],
            })
            signal_entry["action"] = "filled_buy"

        elif p["side"] == "sell":
            if sealed == "down":
                signal_entry["skip_reason"] = "开盘跌停封死，卖不出"
                return
            price = round_money(o * (1.0 - self.fee["slippage"]))
            shares = self.account.shares
            amount = round_money(price * shares)
            fees = fee_split(price, shares, "sell", self.fee)
            net = round_money(amount - fees["total"])
            pnl = round_money(net - self.account.cost)
            self.account.cash = round_money(self.account.cash + net)
            self.account.shares = 0
            self.account.cost = 0.0
            self.account.cost_price = 0.0
            self.account.buy_idx = -1
            self.account.buy_ts = ""
            self.account.high_since_buy = 0.0
            bar_idx = p.get("bar_idx", 0)
            reason = p.get("reason") or dsl_mod.describe_trigger(
                self.validated, "sell", self._eval_result.nodes, bar_idx)
            self.trades.append({
                "side": "sell", "ts": ts, "idx": i, "price": price, "shares": shares,
                "amount": amount, "fees": fees, "pnl": pnl, "reason": reason,
                "from_ts": p["ts"],
            })
            signal_entry["action"] = "filled_sell"

    def _check_risk(self, i: int, ts: str, o: float, h: float, l: float, c: float,
                    signal_entry: dict) -> None:
        """盘中风控：止损/止盈/移动止损 = 触发价成交（无滑点）。"""
        risk = self.validated.get("risk") or {}
        if self.account.shares <= 0:
            return

        cost_price = self.account.cost_price
        stop_loss = risk.get("stop_loss_pct")
        take_profit = risk.get("take_profit_pct")
        trailing = risk.get("trailing_stop_pct")

        trigger_price = None
        reason = ""

        if stop_loss is not None:
            stop_price = round_money(cost_price * (1.0 - stop_loss / 100.0))
            if l <= stop_price:
                trigger_price = stop_price
                reason = f"止损 {stop_loss}%"

        if trigger_price is None and take_profit is not None:
            tp_price = round_money(cost_price * (1.0 + take_profit / 100.0))
            if h >= tp_price:
                trigger_price = tp_price
                reason = f"止盈 {take_profit}%"

        if trigger_price is None and trailing is not None:
            high_since = self.account.high_since_buy
            trail_price = round_money(high_since * (1.0 - trailing / 100.0))
            if l <= trail_price:
                trigger_price = trail_price
                reason = f"移动止损 {trailing}%"

        if trigger_price is not None:
            shares = self.account.shares
            amount = round_money(trigger_price * shares)
            fees = fee_split(trigger_price, shares, "sell", self.fee)
            net = round_money(amount - fees["total"])
            pnl = round_money(net - self.account.cost)
            self.account.cash = round_money(self.account.cash + net)
            self.account.shares = 0
            self.account.cost = 0.0
            self.account.cost_price = 0.0
            self.account.buy_idx = -1
            self.account.buy_ts = ""
            self.account.high_since_buy = 0.0
            self.trades.append({
                "side": "sell", "ts": ts, "idx": i, "price": trigger_price,
                "shares": shares, "amount": amount, "fees": fees, "pnl": pnl,
                "reason": reason, "from_ts": self.account.buy_ts,
            })
            signal_entry["action"] = f"risk_sell:{reason}"

    def _build_trace(self, ts_list: list[str], eval_result, buy_indices: list[int],
                     sell_indices: list[int]) -> dict:
        """溯源数据：每根K线上各条操作数的取值 + 条件满足情况。"""
        lines_df = eval_result.lines
        lines_dict = {}
        units = {}
        for col in lines_df.columns:
            series = lines_df[col]
            lines_dict[col] = [None if pd.isna(v) else round_money(float(v), 4) for v in series]
            name = col.split("(")[0] if "(" in col else col
            if name in ind.INDICATORS:
                units[col] = ind.INDICATORS[name].get("unit", "")

        nodes = []
        for node in eval_result.nodes:
            entry = {"kind": node["kind"], "path": node["path"]}
            if node["kind"] == "cond":
                entry["cmp"] = node["cmp"]
                entry["not"] = node.get("not", False)
                entry["left"] = node["left"]
                entry["right"] = node["right"]
                series = node["series"]
                entry["state"] = [int(v) for v in series]
                entry["say"] = explain.say_condition({
                    "t": "cond", "left": {"t": "ind", "name": node["left"].split("(")[0],
                                          "args": []},
                    "cmp": node["cmp"], "right": {"t": "num", "value": 0},
                    "not": node.get("not", False),
                })
            else:
                entry["logic"] = node["logic"]
                entry["children"] = node["children"]
            nodes.append(entry)

        return {
            "ts": ts_list,
            "lines": lines_dict,
            "nodes": nodes,
            "units": units,
            "signals": {"buy": buy_indices, "sell": sell_indices},
        }


# ------------------------------------------------------------------ 数据缺口检查


def gap_report(df: pd.DataFrame, start: str, end: str, period: str,
               tolerance_pct: float) -> dict:
    """检查K线数据缺口率。返回 {total, missing, gap_pct, ok, reason}。

    缺口率 = 缺失交易日数 / 应有交易日数 × 100%。
    超过容忍上限 → fail-closed 拒绝回测（G1/G2）。
    """
    from .store import count_trading_days

    total = count_trading_days(start, end)
    if total <= 0:
        return {"total": 0, "missing": 0, "gap_pct": 0.0, "ok": True, "reason": "no_trading_days"}

    have = len(df) if df is not None else 0
    missing = max(0, total - have)
    gap_pct = round_money(missing / total * 100.0, 2)
    ok = gap_pct <= tolerance_pct
    reason = "ok" if ok else f"gap {gap_pct}% > tolerance {tolerance_pct}%"
    return {"total": total, "missing": missing, "gap_pct": gap_pct, "ok": ok, "reason": reason}


# ------------------------------------------------------------------ 对外入口


def run_one(code: str, name: str, validated: dict, df: pd.DataFrame,
            fee: dict, initial_cash: float, period: str) -> BacktestResult:
    """单只股票回测。"""
    runner = _Runner(code, name, validated, df, fee, initial_cash, period)
    return runner.run()
