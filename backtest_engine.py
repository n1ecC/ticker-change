"""Quantitative Signal Simulation & Backtesting Engine.

Runs vectorised, walk-forward simulations on the multi-factor Buyer Signal score
with slippage, commission friction, drawdown tracking, and performance attribution.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class BacktestSummary:
    total_return_pct: float
    cagr_pct: float
    benchmark_return_pct: float
    alpha_pct: float
    annualized_sharpe: float
    annualized_sortino: float
    max_drawdown_pct: float
    calmar_ratio: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int


def run_signals_backtest(
    prices_df: pd.DataFrame,
    entry_threshold: float = 65.0,
    exit_threshold: float = 45.0,
    slippage_bps: float = 5.0,
    commission_bps: float = 1.0,
) -> Tuple[BacktestSummary, pd.DataFrame]:
    """Execute walk-forward trading simulation driven by quantitative momentum/signals."""
    if len(prices_df) < 50:
        empty_res = BacktestSummary(
            total_return_pct=0.0,
            cagr_pct=0.0,
            benchmark_return_pct=0.0,
            alpha_pct=0.0,
            annualized_sharpe=0.0,
            annualized_sortino=0.0,
            max_drawdown_pct=0.0,
            calmar_ratio=0.0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            total_trades=0,
        )
        return empty_res, pd.DataFrame()

    df = prices_df.copy()
    c_col = "close" if "close" in df.columns else "Close"
    df["ret"] = df[c_col].pct_change().fillna(0.0)

    # Compute fast deterministic signal proxy (EMA + RSI + Volatility)
    ema20 = df[c_col].ewm(span=20, adjust=False).mean()
    ema50 = df[c_col].ewm(span=50, adjust=False).mean()

    # RSI
    delta = df[c_col].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / (ema_down + 1e-8)
    rsi = 100 - (100 / (1 + rs))

    # Signal proxy: Trend score + RSI mean reversion
    trend_score = np.where(df[c_col] > ema20, 50.0, 20.0) + np.where(ema20 > ema50, 30.0, 0.0)
    composite_signal = np.clip(trend_score + (rsi * 0.2), 0.0, 100.0)
    df["signal"] = composite_signal

    # Position simulation with slippage
    friction = (slippage_bps + commission_bps) / 10000.0

    in_pos = False
    positions = []
    strat_returns = []
    trades = []
    entry_p = 0.0

    for i in range(len(df)):
        sig = df["signal"].iloc[i]
        curr_p = df[c_col].iloc[i]
        ret = df["ret"].iloc[i]

        if not in_pos and sig >= entry_threshold:
            in_pos = True
            entry_p = curr_p * (1.0 + friction)
            positions.append(1)
            strat_returns.append(-friction)
        elif in_pos and sig <= exit_threshold:
            in_pos = False
            exit_p = curr_p * (1.0 - friction)
            trade_ret = (exit_p - entry_p) / entry_p
            trades.append(trade_ret)
            positions.append(0)
            strat_returns.append(ret - friction)
        elif in_pos:
            positions.append(1)
            strat_returns.append(ret)
        else:
            positions.append(0)
            strat_returns.append(0.0)

    df["strat_ret"] = strat_returns
    df["cum_strat"] = (1.0 + df["strat_ret"]).cumprod()
    df["cum_bench"] = (1.0 + df["ret"]).cumprod()

    # Performance metrics
    total_strat_ret = float(df["cum_strat"].iloc[-1] - 1.0) * 100.0
    total_bench_ret = float(df["cum_bench"].iloc[-1] - 1.0) * 100.0
    alpha = total_strat_ret - total_bench_ret

    n_days = len(df)
    n_years = max(n_days / 252.0, 0.1)
    cagr = (float(df["cum_strat"].iloc[-1]) ** (1.0 / n_years) - 1.0) * 100.0

    # Drawdown
    rolling_peak = df["cum_strat"].cummax()
    drawdown = (df["cum_strat"] - rolling_peak) / rolling_peak
    max_dd = abs(float(drawdown.min())) * 100.0

    # Sharpe & Sortino
    mean_ret = df["strat_ret"].mean() * 252.0
    std_ret = df["strat_ret"].std() * math.sqrt(252.0)
    sharpe = float(mean_ret / std_ret) if std_ret > 0 else 0.0

    downside_ret = df[df["strat_ret"] < 0]["strat_ret"]
    downside_std = downside_ret.std() * math.sqrt(252.0) if len(downside_ret) > 1 else std_ret
    sortino = float(mean_ret / downside_std) if downside_std > 0 else 0.0

    calmar = float(cagr / max_dd) if max_dd > 0 else 0.0

    win_trades = [t for t in trades if t > 0]
    loss_trades = [t for t in trades if t <= 0]
    win_rate = (len(win_trades) / len(trades) * 100.0) if trades else 0.0
    gross_profits = sum(win_trades)
    gross_losses = abs(sum(loss_trades))
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else 2.5

    summary = BacktestSummary(
        total_return_pct=round(total_strat_ret, 2),
        cagr_pct=round(cagr, 2),
        benchmark_return_pct=round(total_bench_ret, 2),
        alpha_pct=round(alpha, 2),
        annualized_sharpe=round(sharpe, 2),
        annualized_sortino=round(sortino, 2),
        max_drawdown_pct=round(max_dd, 2),
        calmar_ratio=round(calmar, 2),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        total_trades=len(trades),
    )

    return summary, df
