"""Microstructure, Liquidity & Squeeze Vulnerability Engine.

Implements:
1. Easley-Kiefer-O'Hara VPIN (Volume-Synchronized Probability of Toxicity)
2. Corwin-Schultz (2012) High-Low Bid-Ask Spread Estimator
3. Roll (1984) Effective Spread from Serial Covariance of Price Changes
4. Squeeze Risk Index (Multi-factor: DTC, Short Interest % Float, CTB, Negative GEX)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class MicrostructureAnalytics:
    corwin_schultz_spread_pct: float
    roll_effective_spread_pct: float
    amihud_illiquidity: float
    vpin_toxicity: float  # [0.0, 1.0]
    toxicity_regime: str  # LOW, MODERATE, HIGH, CRITICAL
    squeeze_risk_score: float  # [0, 100]
    squeeze_risk_level: str  # LOW, ELEVATED, HIGH, EXTREME


def calculate_corwin_schultz_spread(df: pd.DataFrame, window: int = 2) -> float:
    """Calculate the Corwin & Schultz (2012) Bid-Ask Spread Estimator from High/Low prices.

    S = 2 * (e^alpha - 1) / (1 + e^alpha)
    where alpha = (sqrt(2 * beta) - sqrt(beta)) / (3 - 2 * sqrt(2)) - sqrt(gamma / (3 - 2 * sqrt(2)))
    """
    if len(df) < window + 1:
        return 0.0

    high = df["high"].values if "high" in df.columns else df["High"].values
    low = df["low"].values if "low" in df.columns else df["Low"].values

    # Clean zero / invalid entries
    valid_mask = (high > 0) & (low > 0) & (high >= low)
    if np.sum(valid_mask) < 2:
        return 0.0

    h = high[valid_mask]
    l = low[valid_mask]

    spreads = []
    const_sqrt2 = math.sqrt(2.0)
    denom = 3.0 - 2.0 * const_sqrt2

    for t in range(1, len(h)):
        h1, l1 = h[t - 1], l[t - 1]
        h2, l2 = h[t], l[t]

        # 2-day high and low
        h2d = max(h1, h2)
        l2d = min(l1, l2)

        beta = (math.log(h1 / l1) ** 2) + (math.log(h2 / l2) ** 2)
        gamma = math.log(h2d / l2d) ** 2

        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / denom - math.sqrt(gamma / denom)
        if alpha < 0:
            s = 0.0
        else:
            e_alpha = math.exp(alpha)
            s = 2.0 * (e_alpha - 1.0) / (1.0 + e_alpha)
        spreads.append(s)

    if not spreads:
        return 0.0
    return float(np.clip(np.nanmean(spreads), 0.0, 0.20))


def calculate_roll_effective_spread(df: pd.DataFrame) -> float:
    """Calculate Roll (1984) Effective Spread from serial covariance of price changes.

    Roll Spread = 2 * sqrt(-Cov(Delta P_t, Delta P_{t-1})) / P_mean
    If covariance is positive, return 0.0.
    """
    if len(df) < 5:
        return 0.0

    close = df["close"].values if "close" in df.columns else df["Close"].values
    delta_p = np.diff(close)
    if len(delta_p) < 2:
        return 0.0

    cov_matrix = np.cov(delta_p[:-1], delta_p[1:])
    cov = cov_matrix[0, 1]

    if cov >= 0:
        return 0.0

    mean_price = np.mean(close)
    if mean_price <= 0:
        return 0.0

    roll_spread = 2.0 * math.sqrt(-cov) / mean_price
    return float(np.clip(roll_spread, 0.0, 0.20))


def calculate_amihud_illiquidity(df: pd.DataFrame) -> float:
    """Amihud (2002) Illiquidity Measure: Mean(|R_t| / Dollar_Volume_t) * 1e6."""
    if len(df) < 5:
        return 0.0

    close = df["close"].values if "close" in df.columns else df["Close"].values
    volume = df["volume"].values if "volume" in df.columns else df["Volume"].values

    returns = np.abs(np.diff(close) / close[:-1])
    dollar_vol = close[1:] * volume[1:]

    valid = dollar_vol > 0
    if not np.any(valid):
        return 0.0

    illiq = np.mean(returns[valid] / dollar_vol[valid]) * 1e6
    return float(illiq)


def calculate_vpin(trades: List[Dict[str, Any]], num_buckets: int = 50, bucket_size: Optional[int] = None) -> float:
    """Calculate Easley-Kiefer-O'Hara VPIN from trade ticks or volume bars.

    trades: list of dicts with {'price': float, 'volume': int, 'side': 'buy'|'sell' (or inferred via tick rule)}
    """
    if not trades:
        return 0.20  # Neutral baseline

    total_vol = sum(t.get("volume", 0) for t in trades)
    if total_vol <= 0:
        return 0.20

    if not bucket_size:
        bucket_size = max(int(total_vol / max(num_buckets, 1)), 100)

    bucket_imbalances = []
    curr_buy_vol = 0
    curr_sell_vol = 0
    curr_bucket_vol = 0

    last_p = None
    for t in trades:
        p = t.get("price", 0.0)
        v = t.get("volume", 0)
        side = t.get("side")

        # Inferred side via Tick Rule if missing
        if not side:
            if last_p is None or p >= last_p:
                side = "buy"
            else:
                side = "sell"
        last_p = p

        while v > 0:
            space = bucket_size - curr_bucket_vol
            fill = min(v, space)
            if side == "buy":
                curr_buy_vol += fill
            else:
                curr_sell_vol += fill
            curr_bucket_vol += fill
            v -= fill

            if curr_bucket_vol >= bucket_size:
                bucket_imbalances.append(abs(curr_buy_vol - curr_sell_vol))
                curr_buy_vol = 0
                curr_sell_vol = 0
                curr_bucket_vol = 0

    if not bucket_imbalances:
        return 0.20

    vpin = sum(bucket_imbalances) / (len(bucket_imbalances) * bucket_size)
    return float(np.clip(vpin, 0.0, 1.0))


def compute_squeeze_risk_index(
    short_pct_float: float,
    days_to_cover: float,
    cost_to_borrow_pct: float = 2.5,
    is_negative_gex: bool = False,
    realized_vol_30d: float = 0.35,
) -> Tuple[float, str]:
    """Multi-factor Short Squeeze Risk Composite (0 - 100).

    Components:
    - Short Interest % Float (0-40 pts): >20% heavily penalized
    - Days to Cover (0-30 pts): >5 days heavily penalized
    - Cost to Borrow (0-15 pts): >10% annualized fee
    - Negative Gamma Dealer Exposure (0-15 pts): Accelerates price upside cascades
    """
    # 1. Short Float Score
    si = max(short_pct_float, 0.0)
    si_score = min(si / 25.0, 1.0) * 40.0

    # 2. Days to Cover Score
    dtc = max(days_to_cover, 0.0)
    dtc_score = min(dtc / 7.0, 1.0) * 30.0

    # 3. Cost to Borrow Score
    ctb = max(cost_to_borrow_pct, 0.0)
    ctb_score = min(ctb / 20.0, 1.0) * 15.0

    # 4. Negative GEX Accelerator
    gex_score = 15.0 if is_negative_gex else 5.0

    raw_score = si_score + dtc_score + ctb_score + gex_score
    score = round(float(np.clip(raw_score, 0.0, 100.0)), 1)

    if score >= 75.0:
        level = "EXTREME"
    elif score >= 55.0:
        level = "HIGH"
    elif score >= 35.0:
        level = "ELEVATED"
    else:
        level = "LOW"

    return score, level


def get_microstructure_analytics(
    df: pd.DataFrame,
    short_pct_float: float = 3.0,
    days_to_cover: float = 2.0,
    is_negative_gex: bool = False,
) -> MicrostructureAnalytics:
    """Unified entry-point for quantitative microstructure analysis."""
    cs_spread = calculate_corwin_schultz_spread(df)
    roll_spread = calculate_roll_effective_spread(df)
    amihud = calculate_amihud_illiquidity(df)

    # Convert OHLCV bars into simulated volume buckets for VPIN
    pseudo_trades = []
    if not df.empty:
        closes = df["close"].values if "close" in df.columns else df["Close"].values
        vols = df["volume"].values if "volume" in df.columns else df["Volume"].values
        for i in range(1, len(closes)):
            side = "buy" if closes[i] >= closes[i - 1] else "sell"
            pseudo_trades.append({"price": float(closes[i]), "volume": int(vols[i]), "side": side})

    vpin = calculate_vpin(pseudo_trades)
    if vpin >= 0.65:
        vpin_regime = "CRITICAL"
    elif vpin >= 0.45:
        vpin_regime = "HIGH"
    elif vpin >= 0.25:
        vpin_regime = "MODERATE"
    else:
        vpin_regime = "LOW"

    sq_score, sq_level = compute_squeeze_risk_index(
        short_pct_float=short_pct_float,
        days_to_cover=days_to_cover,
        is_negative_gex=is_negative_gex,
    )

    return MicrostructureAnalytics(
        corwin_schultz_spread_pct=round(cs_spread * 100, 3),
        roll_effective_spread_pct=round(roll_spread * 100, 3),
        amihud_illiquidity=round(amihud, 4),
        vpin_toxicity=round(vpin, 3),
        toxicity_regime=vpin_regime,
        squeeze_risk_score=sq_score,
        squeeze_risk_level=sq_level,
    )
