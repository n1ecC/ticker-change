"""Cumulative Abnormal Return (CAR) Event Study Analytics.

Calculates abnormal and cumulative abnormal returns around corporate events
(splits, mergers, ticker changes, SEC 8-K filings) using CAPM / Market Model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class EventStudyResult:
    ticker: str
    event_date: str
    event_type: str
    estimation_window: Tuple[int, int]  # e.g., (-120, -21)
    event_window: Tuple[int, int]  # e.g., (-10, 30)
    alpha: float
    beta: float
    r_squared: float
    residual_variance: float
    car: float  # Cumulative Abnormal Return over event window
    car_t_stat: float
    car_p_value: float
    is_significant_95: bool
    daily_abnormal_returns: List[Dict[str, Any]] = field(default_factory=list)


def run_event_study(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    event_date: str,
    event_type: str = "CORPORATE_ACTION",
    ticker: str = "UNKNOWN",
    estimation_window: Tuple[int, int] = (-120, -21),
    event_window: Tuple[int, int] = (-10, 30),
) -> Optional[EventStudyResult]:
    """Execute a single-firm Event Study on daily return series using Market Model.

    stock_df: DataFrame with DatetimeIndex and 'close' (or 'Close') column
    benchmark_df: Market proxy DataFrame (e.g. SPY) with DatetimeIndex and 'close'
    event_date: 'YYYY-MM-DD'
    estimation_window: Trading days before event (start, end) e.g. (-120, -21)
    event_window: Relative trading days around event (start, end) e.g. (-10, 30)
    """
    # Normalize column names
    s_col = "close" if "close" in stock_df.columns else "Close"
    b_col = "close" if "close" in benchmark_df.columns else "Close"

    # Merge aligned return series
    s_ret = stock_df[s_col].pct_change().dropna().rename("stock_ret")
    b_ret = benchmark_df[b_col].pct_change().dropna().rename("bench_ret")

    df = pd.concat([s_ret, b_ret], axis=1).dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)

    target_dt = pd.to_datetime(event_date)
    # Find closest trading day on or before target_dt
    available_dates = df.index
    if target_dt not in available_dates:
        valid_dates = available_dates[available_dates <= target_dt]
        if valid_dates.empty:
            return None
        target_dt = valid_dates[-1]

    event_idx = df.index.get_loc(target_dt)

    est_start_idx = event_idx + estimation_window[0]
    est_end_idx = event_idx + estimation_window[1]

    ev_start_idx = event_idx + event_window[0]
    ev_end_idx = event_idx + event_window[1]

    # Validate window boundaries
    if est_start_idx < 0 or ev_end_idx >= len(df):
        return None
    if est_end_idx >= ev_start_idx:
        return None

    est_data = df.iloc[est_start_idx : est_end_idx + 1]
    if len(est_data) < 30:
        return None

    # Fit Market Model: R_it = alpha_i + beta_i * R_mt + eps_it
    x = est_data["bench_ret"].values
    y = est_data["stock_ret"].values

    x_mean = np.mean(x)
    y_mean = np.mean(y)
    cov_xy = np.sum((x - x_mean) * (y - y_mean))
    var_x = np.sum((x - x_mean) ** 2)

    if var_x == 0:
        beta = 1.0
        alpha = 0.0
    else:
        beta = float(cov_xy / var_x)
        alpha = float(y_mean - beta * x_mean)

    est_pred = alpha + beta * x
    est_resid = y - est_pred
    n_est = len(est_data)
    sigma_eps_sq = float(np.sum(est_resid ** 2) / max(n_est - 2, 1))

    corr = np.corrcoef(x, y)[0, 1] if var_x > 0 else 0.0
    r_squared = float(corr ** 2) if not np.isnan(corr) else 0.0

    # Compute Abnormal Returns over event window
    ev_data = df.iloc[ev_start_idx : ev_end_idx + 1]
    daily_abnormal = []
    cumulative_ar = 0.0
    var_car_sum = 0.0

    for i, (dt, row) in enumerate(ev_data.iterrows()):
        rel_day = event_window[0] + i
        actual_ret = float(row["stock_ret"])
        bench_ret = float(row["bench_ret"])
        expected_ret = alpha + beta * bench_ret
        ar = actual_ret - expected_ret
        cumulative_ar += ar

        # Variance adjustment per Salinger (1992)
        var_ar = sigma_eps_sq * (1.0 + 1.0 / n_est + ((bench_ret - x_mean) ** 2) / max(var_x, 1e-8))
        var_car_sum += var_ar

        daily_abnormal.append({
            "relative_day": int(rel_day),
            "date": dt.strftime("%Y-%m-%d"),
            "actual_return": round(actual_ret, 6),
            "expected_return": round(expected_ret, 6),
            "abnormal_return": round(ar, 6),
            "cumulative_abnormal_return": round(cumulative_ar, 6),
        })

    car_std = math.sqrt(max(var_car_sum, 1e-12))
    car_t_stat = float(cumulative_ar / car_std)

    # Two-tailed p-value approximation via standard normal (for large N)
    from scipy import stats
    try:
        p_val = float(2 * (1 - stats.norm.cdf(abs(car_t_stat))))
    except Exception:
        # Fallback approximation if scipy is not present
        p_val = float(math.erfc(abs(car_t_stat) / math.sqrt(2)))

    return EventStudyResult(
        ticker=ticker.upper(),
        event_date=target_dt.strftime("%Y-%m-%d"),
        event_type=event_type,
        estimation_window=estimation_window,
        event_window=event_window,
        alpha=round(alpha, 6),
        beta=round(beta, 4),
        r_squared=round(r_squared, 4),
        residual_variance=round(sigma_eps_sq, 8),
        car=round(cumulative_ar, 6),
        car_t_stat=round(car_t_stat, 4),
        car_p_value=round(p_val, 5),
        is_significant_95=p_val < 0.05,
        daily_abnormal_returns=daily_abnormal,
    )
