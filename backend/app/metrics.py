"""绩效指标：从回测结果算出 11 个数字 + 基准对齐。

指标口径对齐国内行情软件（同花顺 / 东方财富）：
- 年化收益率 = (1 + 总收益率) ^ (252 / 实际交易天数) - 1
- 最大回撤 = max(1 - equity / peak)  在整段曲线上找
- 夏普 = (年化收益 - 无风险利率) / 年化波动率
- 基准对齐：把基准的起点和回测起点对齐，算基准的同期收益率

F10 硬指标：所有交易盈亏加总 + 期末持仓市值 - 初始资金 == 总收益率对应金额，
误差 < 0.01 元。用 round_money 保证。
"""

import math
from typing import Any

import numpy as np
import pandas as pd

from .config import round_money


def compute(result, bench_df: pd.DataFrame | None = None,
            fee: dict | None = None,
            shanghai_df: pd.DataFrame | None = None,
            stock_first_close: float | None = None,
            stock_last_close: float | None = None) -> dict[str, Any]:
    """从 BacktestResult 算出全部指标。

    bench_df: 基准指数收盘价序列（索引=ts，列=close），可选。
    返回 dict 包含 11 个指标 + 附加字段。
    """
    equity = result.equity
    trades = result.trades
    initial_cash = result.initial_cash
    final_equity = result.final_equity

    if not equity:
        return _empty_metrics(initial_cash)

    eq_values = [e["equity"] for e in equity]
    ts_list = [e["ts"] for e in equity]
    n_days = len(eq_values)

    total_return = (final_equity - initial_cash) / initial_cash if initial_cash > 0 else 0.0
    annual_days = 252
    if fee:
        annual_days = fee.get("annual_days", 252)

    years = n_days / annual_days if n_days > 0 else 0.0
    annual_return = ((1.0 + total_return) ** (1.0 / years) - 1.0) if years > 0 else 0.0

    daily_returns = _daily_returns(eq_values)
    volatility = _annual_vol(daily_returns, annual_days)

    max_dd, max_dd_start, max_dd_end = _max_drawdown(eq_values, ts_list)

    risk_free = fee.get("risk_free_rate", 0.02) if fee else 0.02
    sharpe = (annual_return - risk_free) / volatility if volatility > 0 else 0.0

    win_trades, loss_trades, win_amount, loss_amount = _win_loss_stats(trades)
    total_trades = win_trades + loss_trades
    win_rate = win_trades / total_trades if total_trades > 0 else 0.0
    profit_loss_ratio = (win_amount / win_trades) / (loss_amount / loss_trades) \
        if win_trades > 0 and loss_trades > 0 else 0.0

    total_fees = sum(t.get("fees", {}).get("total", 0.0) for t in trades)

    # 基准对齐
    bench_return = None
    bench_aligned = None
    if bench_df is not None and len(bench_df) > 0:
        bench_aligned = _align_benchmark(bench_df, ts_list)
        if bench_aligned and len(bench_aligned) >= 2:
            bench_return = (bench_aligned[-1] - bench_aligned[0]) / bench_aligned[0]

    # 上证指数对齐
    shanghai_return = None
    shanghai_aligned = None
    if shanghai_df is not None and len(shanghai_df) > 0:
        shanghai_aligned = _align_benchmark(shanghai_df, ts_list)
        if shanghai_aligned and len(shanghai_aligned) >= 2:
            shanghai_return = (shanghai_aligned[-1] - shanghai_aligned[0]) / shanghai_aligned[0]

    # 买入持有收益率：第一天买入 → 最后一天不动
    buy_hold_return = None
    if stock_first_close and stock_last_close and stock_first_close > 0:
        buy_hold_return = (stock_last_close - stock_first_close) / stock_first_close

    # 基准对比曲线：归一化为百分比收益率
    bench_series = _to_return_series(bench_aligned, ts_list)
    shanghai_series = _to_return_series(shanghai_aligned, ts_list)

    # F10 会计恒等式校验：从交易现金流重建终值，和引擎报告的终值比对
    last_eq = eq_values[-1] if eq_values else initial_cash
    reconstructed_cash = initial_cash
    reconstructed_shares = 0
    for t in trades:
        if t.get("side") == "buy":
            reconstructed_cash -= t.get("amount", 0.0) + t.get("fees", {}).get("total", 0.0)
            reconstructed_shares += t.get("shares", 0)
        elif t.get("side") == "sell":
            reconstructed_cash += t.get("amount", 0.0) - t.get("fees", {}).get("total", 0.0)
            reconstructed_shares -= t.get("shares", 0)
    reconstructed_cash = round_money(reconstructed_cash)
    reported_cash = getattr(result, "final_cash", reconstructed_cash)
    accounting_ok = abs(reconstructed_cash - reported_cash) < 0.01

    return {
        "total_return": round_money(total_return, 6),
        "annual_return": round_money(annual_return, 6),
        "max_drawdown": round_money(max_dd, 6),
        "max_drawdown_start": max_dd_start,
        "max_drawdown_end": max_dd_end,
        "volatility": round_money(volatility, 6),
        "sharpe": round_money(sharpe, 4),
        "total_trades": total_trades,
        "win_rate": round_money(win_rate, 4),
        "profit_loss_ratio": round_money(profit_loss_ratio, 4),
        "total_fees": round_money(total_fees),
        "bench_return": round_money(bench_return, 6) if bench_return is not None else None,
        "shanghai_return": round_money(shanghai_return, 6) if shanghai_return is not None else None,
        "buy_hold_return": round_money(buy_hold_return, 6) if buy_hold_return is not None else None,
        "bench_series": bench_series,
        "shanghai_series": shanghai_series,
        "final_equity": round_money(last_eq),
        "initial_cash": initial_cash,
        "n_days": n_days,
        "accounting_ok": accounting_ok,
    }


def _empty_metrics(initial_cash: float) -> dict:
    return {
        "total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0,
        "max_drawdown_start": None, "max_drawdown_end": None,
        "volatility": 0.0, "sharpe": 0.0,
        "total_trades": 0, "win_rate": 0.0, "profit_loss_ratio": 0.0,
        "total_fees": 0.0, "bench_return": None, "shanghai_return": None,
        "buy_hold_return": None, "bench_series": None, "shanghai_series": None,
        "final_equity": initial_cash, "initial_cash": initial_cash,
        "n_days": 0, "accounting_ok": True,
    }


def _daily_returns(eq_values: list[float]) -> list[float]:
    if len(eq_values) < 2:
        return []
    return [(eq_values[i] - eq_values[i - 1]) / eq_values[i - 1]
            for i in range(1, len(eq_values)) if eq_values[i - 1] > 0]


def _annual_vol(daily_returns: list[float], annual_days: int = 252) -> float:
    if len(daily_returns) < 2:
        return 0.0
    arr = np.array(daily_returns)
    return float(np.std(arr, ddof=1) * math.sqrt(annual_days))


def _max_drawdown(eq_values: list[float], ts_list: list[str]) -> tuple[float, str | None, str | None]:
    if not eq_values:
        return 0.0, None, None
    peak = eq_values[0]
    peak_ts = ts_list[0]
    max_dd = 0.0
    dd_start = peak_ts
    dd_end = peak_ts
    for i, v in enumerate(eq_values):
        if v > peak:
            peak = v
            peak_ts = ts_list[i]
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            dd_start = peak_ts
            dd_end = ts_list[i]
    return max_dd, dd_start, dd_end


def _win_loss_stats(trades: list[dict]) -> tuple[int, int, float, float]:
    win_n = loss_n = 0
    win_sum = loss_sum = 0.0
    for t in trades:
        if t.get("side") != "sell":
            continue
        pnl = t.get("pnl", 0.0)
        if pnl > 0:
            win_n += 1
            win_sum += pnl
        elif pnl < 0:
            loss_n += 1
            loss_sum += abs(pnl)
    return win_n, loss_n, win_sum, loss_sum


def _align_benchmark(bench_df: pd.DataFrame, ts_list: list[str]) -> list[float] | None:
    """把基准指数的收盘价按回测的日期列表对齐。

    基准可能比回测区间长或短，只取重叠部分。缺失日期用前值填充（交易日才有数据）。
    """
    if bench_df is None or not len(bench_df):
        return None
    bench = bench_df["close"] if "close" in bench_df.columns else bench_df.iloc[:, 0]
    bench = bench.sort_index()
    aligned = []
    last_val = None
    bench_dict = {str(k)[:10]: float(v) for k, v in bench.items()}
    for ts in ts_list:
        if ts in bench_dict:
            last_val = bench_dict[ts]
        if last_val is not None:
            aligned.append(last_val)
    return aligned if len(aligned) >= 2 else None


def _to_return_series(aligned: list[float] | None, ts_list: list[str]) -> list[dict] | None:
    """把对齐后的价格序列转为 [{ts, pct}] 的收益率百分比序列（起点=0）。"""
    if not aligned or len(aligned) < 2:
        return None
    base = aligned[0]
    if base <= 0:
        return None
    offset = len(ts_list) - len(aligned)
    out = []
    for i, v in enumerate(aligned):
        pct = round_money((v / base - 1) * 100, 2)
        out.append({"ts": ts_list[offset + i], "pct": pct})
    return out
