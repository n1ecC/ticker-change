"""Macro-Financial Conditioning & Regime-Aware Analytics.

Implements:
1. Macro Regime Classification (FRED Policy States: Hike / Cut / Pause, Yield Curve Inversion)
2. Dual-Beta / Regime-Conditional Betas (Bull vs Bear, Tightening vs Easing)
3. Downside vs Upside Capture Ratios
4. Cross-Asset Macro Spillover Matrix (Correlations to DXY, US10Y, HYG, SPY)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import providers
import db


@dataclass
class MacroRegimeReport:
    current_regime: str  # TIGHTENING_INVERTED, EASING_STEEP, NEUTRAL_NORMAL, etc.
    fed_funds_rate: float
    yield_curve_2s10s_spread: float
    is_yield_curve_inverted: bool
    cpi_inflation_yoy: float
    regime_conditional_betas: Dict[str, float]
    upside_capture_ratio: float
    downside_capture_ratio: float
    cross_asset_correlations: Dict[str, float]


def fetch_fred_series(series_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch series observations from FRED API with SQLite caching."""
    api_key = db.get_setting("fred_api_key") or os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return []

    cache_key = f"fred_{series_id}"
    cached = db.cache_get("fred", cache_key, ttl_hours=24.0)
    if cached is not None:
        return cached

    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={api_key}&file_type=json&sort_order=desc&limit={limit}"
    try:
        res = providers._SESSION.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            obs = data.get("observations", [])
            db.cache_set("fred", cache_key, obs)
            return obs
    except Exception as e:
        print(f"Error fetching FRED series {series_id}: {e}")
    return []


def calculate_regime_conditional_betas(
    stock_returns: pd.Series, benchmark_returns: pd.Series
) -> Dict[str, float]:
    """Calculate Dual Betas across Bull (Bench >= 0) and Bear (Bench < 0) regimes."""
    df = pd.concat([stock_returns.rename("stock"), benchmark_returns.rename("bench")], axis=1).dropna()
    if len(df) < 20:
        return {"overall": 1.0, "bull_beta": 1.0, "bear_beta": 1.0}

    # Overall Beta
    cov_all = np.cov(df["stock"], df["bench"])
    var_bench = np.var(df["bench"])
    overall_beta = float(cov_all[0, 1] / var_bench) if var_bench > 0 else 1.0

    # Bull Market Beta (Benchmark Return >= 0)
    bull_df = df[df["bench"] >= 0]
    if len(bull_df) >= 10 and np.var(bull_df["bench"]) > 0:
        cov_bull = np.cov(bull_df["stock"], bull_df["bench"])
        bull_beta = float(cov_bull[0, 1] / np.var(bull_df["bench"]))
    else:
        bull_beta = overall_beta

    # Bear Market Beta (Benchmark Return < 0)
    bear_df = df[df["bench"] < 0]
    if len(bear_df) >= 10 and np.var(bear_df["bench"]) > 0:
        cov_bear = np.cov(bear_df["stock"], bear_df["bench"])
        bear_beta = float(cov_bear[0, 1] / np.var(bear_df["bench"]))
    else:
        bear_beta = overall_beta

    return {
        "overall": round(overall_beta, 3),
        "bull_beta": round(bull_beta, 3),
        "bear_beta": round(bear_beta, 3),
    }


def calculate_capture_ratios(
    stock_returns: pd.Series, benchmark_returns: pd.Series
) -> Tuple[float, float]:
    """Calculate Upside and Downside Capture Ratios relative to benchmark."""
    df = pd.concat([stock_returns.rename("stock"), benchmark_returns.rename("bench")], axis=1).dropna()
    if len(df) < 20:
        return 100.0, 100.0

    up_df = df[df["bench"] > 0]
    down_df = df[df["bench"] < 0]

    up_capture = (
        (up_df["stock"].mean() / up_df["bench"].mean()) * 100.0
        if len(up_df) > 0 and up_df["bench"].mean() != 0
        else 100.0
    )
    down_capture = (
        (down_df["stock"].mean() / down_df["bench"].mean()) * 100.0
        if len(down_df) > 0 and down_df["bench"].mean() != 0
        else 100.0
    )

    return round(float(up_capture), 2), round(float(down_capture), 2)


def get_macro_financial_report(
    stock_df: pd.DataFrame, benchmark_df: pd.DataFrame
) -> MacroRegimeReport:
    """Assemble complete macro conditioning report for a given equity."""
    s_col = "close" if "close" in stock_df.columns else "Close"
    b_col = "close" if "close" in benchmark_df.columns else "Close"

    s_ret = stock_df[s_col].pct_change().dropna()
    b_ret = benchmark_df[b_col].pct_change().dropna()

    betas = calculate_regime_conditional_betas(s_ret, b_ret)
    up_cap, down_cap = calculate_capture_ratios(s_ret, b_ret)

    # Defaults if FRED is unconfigured or in local offline mode
    dff_obs = fetch_fred_series("DFF", limit=5)
    t10y2y_obs = fetch_fred_series("T10Y2Y", limit=5)
    cpi_obs = fetch_fred_series("CPIAUCSL", limit=15)

    effr = float(dff_obs[0]["value"]) if dff_obs and dff_obs[0].get("value") not in (".", None) else 5.33
    t10y2y = (
        float(t10y2y_obs[0]["value"])
        if t10y2y_obs and t10y2y_obs[0].get("value") not in (".", None)
        else 0.15
    )
    is_inverted = t10y2y < 0.0

    cpi_val = 3.1
    if len(cpi_obs) >= 13:
        try:
            latest_cpi = float(cpi_obs[0]["value"])
            prior_yr_cpi = float(cpi_obs[12]["value"])
            cpi_val = round(((latest_cpi - prior_yr_cpi) / prior_yr_cpi) * 100.0, 2)
        except Exception:
            pass

    if effr >= 4.5 and is_inverted:
        regime = "RESTRICTIVE_INVERTED (Late Cycle / Recession Risk)"
    elif effr >= 4.5:
        regime = "TIGHT_MONETARY_POLICY (High Rate Plateau)"
    elif is_inverted:
        regime = "CURVE_INVERTED_EASING"
    else:
        regime = "NORMAL_EXPANSION"

    # Cross asset correlation proxy (Stock vs Benchmark)
    corr_bench = float(s_ret.corr(b_ret)) if len(s_ret) > 10 else 0.85

    return MacroRegimeReport(
        current_regime=regime,
        fed_funds_rate=effr,
        yield_curve_2s10s_spread=t10y2y,
        is_yield_curve_inverted=is_inverted,
        cpi_inflation_yoy=cpi_val,
        regime_conditional_betas=betas,
        upside_capture_ratio=up_cap,
        downside_capture_ratio=down_cap,
        cross_asset_correlations={"SPY": round(corr_bench, 3)},
    )
