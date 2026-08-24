"""Advanced Derivatives & Positioning Analytics.

Implements:
1. Higher-Order Black-Scholes Greeks:
   - Vanna (dDelta / dVol = dVega / dSpot)
   - Charm (dDelta / dTime = delta decay)
   - Vomma / Volga (dVega / dVol)
2. Variance Risk Premium (VRP = ATM Implied Volatility - 30D Realized Historical Volatility)
3. Volatility Term Structure Slope (Front-Month vs Back-Month IV)
4. OPEX Pinning & Max Pain vs Gamma Wall Divergence
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def standard_normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def standard_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class HigherOrderGreeks:
    delta: float
    gamma: float
    vega: float
    theta: float
    vanna: float  # dDelta / dSigma
    charm: float  # dDelta / dTime
    vomma: float  # dVega / dSigma


def compute_higher_order_greeks(
    spot: float,
    strike: float,
    time_to_exp: float,  # in years (e.g. 30/365)
    volatility: float,  # e.g. 0.25
    risk_free_rate: float = 0.05,
    is_call: bool = True,
) -> HigherOrderGreeks:
    """Calculate 1st, 2nd, and 3rd order Black-Scholes analytical Greeks."""
    if spot <= 0 or strike <= 0 or time_to_exp <= 0 or volatility <= 0:
        return HigherOrderGreeks(
            delta=0.5 if is_call else -0.5,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            vanna=0.0,
            charm=0.0,
            vomma=0.0,
        )

    t_sqrt = math.sqrt(time_to_exp)
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * volatility * volatility) * time_to_exp) / (
        volatility * t_sqrt
    )
    d2 = d1 - volatility * t_sqrt

    pdf_d1 = standard_normal_pdf(d1)
    cdf_d1 = standard_normal_cdf(d1)
    cdf_neg_d1 = standard_normal_cdf(-d1)
    cdf_d2 = standard_normal_cdf(d2)
    cdf_neg_d2 = standard_normal_cdf(-d2)

    # 1. Delta
    delta = cdf_d1 if is_call else cdf_d1 - 1.0

    # 2. Gamma
    gamma = pdf_d1 / (spot * volatility * t_sqrt)

    # 3. Vega (per 1.0 vol)
    vega = spot * pdf_d1 * t_sqrt

    # 4. Theta (annualized)
    theta_term1 = -(spot * pdf_d1 * volatility) / (2.0 * t_sqrt)
    if is_call:
        theta = (theta_term1 - risk_free_rate * strike * math.exp(-risk_free_rate * time_to_exp) * cdf_d2) / 365.0
    else:
        theta = (theta_term1 + risk_free_rate * strike * math.exp(-risk_free_rate * time_to_exp) * cdf_neg_d2) / 365.0

    # 5. Vanna: dDelta / dVol = -pdf(d1) * d2 / vol
    vanna = -pdf_d1 * d2 / volatility

    # 6. Charm (Delta decay per year): -pdf(d1) * (2*r*T - d2*vol*sqrt(T)) / (2*T*vol*sqrt(T))
    charm_annual = pdf_d1 * (
        (2.0 * risk_free_rate * time_to_exp - d2 * volatility * t_sqrt) / (2.0 * time_to_exp * volatility * t_sqrt)
    )
    charm = float(-charm_annual / 365.0 if is_call else (1.0 - charm_annual) / 365.0)

    # 7. Vomma / Volga: dVega / dVol = Vega * d1 * d2 / vol
    vomma = vega * d1 * d2 / volatility

    return HigherOrderGreeks(
        delta=round(delta, 4),
        gamma=round(gamma, 6),
        vega=round(vega / 100.0, 4),  # Standard 1% vol change convention
        theta=round(theta, 4),
        vanna=round(vanna / 100.0, 4),
        charm=round(charm, 6),
        vomma=round(vomma / 10000.0, 6),
    )


def calculate_variance_risk_premium(
    atm_implied_vol: float, realized_vol_30d: float
) -> Dict[str, Any]:
    """Variance Risk Premium (VRP) calculation and mispricing regime.

    VRP > 0: Options implied volatility trades at a premium over realized movement (Seller's market).
    VRP < 0: Options underpriced relative to realized price fluctuations (Buyer's market).
    """
    vrp_spread = atm_implied_vol - realized_vol_30d
    vrp_ratio = atm_implied_vol / max(realized_vol_30d, 1e-4)

    if vrp_spread > 0.08:
        regime = "RICH_VOLATILITY (Strong Premium Selling Edge)"
    elif vrp_spread > 0.02:
        regime = "NORMAL_PREMIUM (Moderate Variance Spread)"
    elif vrp_spread > -0.04:
        regime = "FAIR_VALUE (Equilibrium)"
    else:
        regime = "DISCOUNTED_VOLATILITY (Long Vega / Gamma Advantage)"

    return {
        "atm_implied_vol": round(atm_implied_vol * 100, 2),
        "realized_vol_30d": round(realized_vol_30d * 100, 2),
        "vrp_spread_pct": round(vrp_spread * 100, 2),
        "vrp_ratio": round(vrp_ratio, 2),
        "regime": regime,
    }
