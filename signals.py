"""Deterministic multi-factor "Buyer Signals" model.

Turns the analytics page's raw numbers into a transparent, reproducible read on
each signal-bearing indicator, then aggregates them into one composite score.
Every factor is computed from the data with explicit thresholds — no model, no
LLM, no hidden state — so the same inputs always give the same output and each
read can be audited against the number it cites.

Scope and honesty:
  * This is a *descriptive factor tally*, not a validated alpha model. It encodes
    well-known technical/positioning heuristics (trend, momentum, dealer gamma,
    path odds, valuation) with documented weights — useful as a structured
    summary, not a forecast. The composite is a weighted average of per-factor
    directional scores in [-1, +1], mapped to 0-100.
  * Risk factors (volatility, tail VaR) are context, not direction; they carry
    small weight and mainly temper conviction.
  * Each `read` states the actual figure and its caveat. The LLM layer (ai.py)
    narrates these — it does not invent its own numbers.

`compute(ticker, data)` returns {factors: [...], composite: {...}, caveat: str}.
"""
from __future__ import annotations

import db
import ml

# Factor weights (directional factors sum to ~1.0 before renormalisation over the
# factors actually present). Tuned to favour positioning/trend over lagging
# fundamentals; the ML factor is deliberately small given its weak OOS edge.
WEIGHTS = {
    "trend": 0.20,
    "momentum": 0.16,
    "gex": 0.18,
    "monte_carlo": 0.15,
    "volatility": 0.08,
    "tail_risk": 0.05,
    "valuation": 0.10,
    "ml": 0.08,
}

CAVEAT = (
    "Buyer Signals is a transparent factor tally of technical, positioning, and "
    "path-odds heuristics — a structured summary, not a validated forecast. Risk "
    "factors temper conviction rather than pick direction."
)


def _stance(score: float) -> tuple[str, str]:
    """(label, tone) from a directional score in [-1, 1]."""
    if score >= 0.5:
        return "Strongly bullish", "bull"
    if score >= 0.2:
        return "Bullish", "bull"
    if score <= -0.5:
        return "Strongly bearish", "bear"
    if score <= -0.2:
        return "Bearish", "bear"
    return "Neutral", "neutral"


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _factor(key, name, score, read, tone=None):
    label, auto_tone = _stance(score)
    return {
        "key": key, "name": name,
        "score": round(float(score), 3),
        "stance": label, "tone": tone or auto_tone,
        "weight": WEIGHTS.get(key, 0.0),
        "read": read,
    }


def _pct(x: float) -> str:
    return f"{x:+.1%}"


# --------------------------------------------------------------------------- #
# Per-indicator factors                                                        #
# --------------------------------------------------------------------------- #
def _trend(feat) -> dict | None:
    try:
        d20, d50, d200 = feat["dist_sma20"], feat["dist_sma50"], feat["dist_sma200"]
        cross = feat["ema_cross_8_21"]
    except Exception:
        return None
    n_above = int(d20 > 0) + int(d50 > 0) + int(d200 > 0)
    score = _clip((n_above - 1.5) / 1.5 + (0.1 if cross > 0 else -0.1))
    word = "up-trend" if score > 0.2 else "down-trend" if score < -0.2 else "mixed"
    read = (f"Price is {_pct(d200)} vs its 200-day average, {_pct(d50)} vs 50-day, "
            f"{_pct(d20)} vs 20-day — {n_above}/3 major averages reclaimed and the "
            f"8/21-EMA cross is {'bullish' if cross > 0 else 'bearish'} ({word}).")
    return _factor("trend", "Trend (moving averages)", score, read)


def _momentum(feat) -> dict | None:
    try:
        rsi, mom, macd = feat["rsi_14"], feat["mom_63"], feat["macd_hist"]
    except Exception:
        return None
    score = _clip(0.7 * _clip(mom * 3) + 0.3 * (1 if macd > 0 else -1))
    if rsi > 75:
        score -= 0.2          # overbought → fade risk
    elif rsi < 25:
        score += 0.2          # oversold → mean-reversion pop
    score = _clip(score)
    rsi_word = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    read = (f"3-month momentum {_pct(mom)}, MACD histogram "
            f"{'positive' if macd > 0 else 'negative'}, RSI(14) at {rsi:.0f} ({rsi_word}).")
    return _factor("momentum", "Momentum (RSI / MACD)", score, read)


def _volatility(feat, stats) -> dict | None:
    av = stats.get("annualised_vol")
    if av is None:
        return None
    # Context, not direction: calm regimes mildly support, turbulent ones caution.
    if av < 15:
        score, regime = 0.2, "low"
    elif av < 25:
        score, regime = 0.05, "normal"
    elif av < 40:
        score, regime = -0.15, "elevated"
    else:
        score, regime = -0.35, "high"
    read = (f"Annualised volatility ≈ {av:.0f}% — a {regime} regime; "
            f"{'supportive of trend continuation' if score > 0 else 'a headwind to conviction' if score < 0 else 'neutral'}.")
    f = _factor("volatility", "Volatility regime", score, read)
    if -0.2 < score < 0.2:
        f["tone"] = "neutral"
    return f


def _gex(data) -> dict | None:
    gex = data.get("gex")
    spot = data.get("current_price")
    if not gex or not spot or not gex.get("gamma_flip"):
        return None
    flip = gex["gamma_flip"]
    above = spot >= flip
    score = 0.3 if above else -0.4
    parts = [
        f"Spot ${spot:.2f} is {'above' if above else 'below'} the γ-flip ${flip:.2f} → "
        f"dealers net-{'long' if above else 'short'} gamma "
        f"({'vol-dampening, mean-reverting' if above else 'trend-amplifying, fragile'})."
    ]
    cw, pw = gex.get("call_wall"), gex.get("put_wall")
    if cw:
        parts.append(f"Call wall ${cw:g} ({_pct((cw - spot) / spot)}) tends to cap.")
    if pw:
        parts.append(f"Put wall ${pw:g} ({_pct((pw - spot) / spot)}) tends to cushion.")
    return _factor("gex", "Dealer positioning (GEX)", score, " ".join(parts))


def _monte_carlo(stats) -> dict | None:
    pg = stats.get("mc_prob_gain")
    if pg is None:
        return None
    if pg > 1.5:           # tolerate either 0-1 or 0-100 storage
        pg, up, dn = pg / 100, (stats.get("mc_prob_up20") or 0) / 100, (stats.get("mc_prob_dn20") or 0) / 100
    else:
        up, dn = stats.get("mc_prob_up20") or 0, stats.get("mc_prob_dn20") or 0
    score = _clip((pg - 0.5) * 2 * 0.7 + (up - dn) * 1.5)
    read = (f"Monte-Carlo path simulation puts the odds of finishing higher over the "
            f"horizon at {pg * 100:.0f}%, with {up * 100:.0f}% odds of a +20% move vs "
            f"{dn * 100:.0f}% for −20% (assumes returns stay statistically like history).")
    return _factor("monte_carlo", "Monte-Carlo path odds", score, read)


def _tail_risk(stats) -> dict | None:
    v95, v99 = stats.get("var_95"), stats.get("var_99")
    if v95 is None:
        return None
    score = _clip(-(abs(v95) - 2.0) / 6.0, -0.4, 0.0)   # only ever a mild drag
    extra = f"; 99% VaR ≈ {v99:.1f}%" if v99 is not None else ""
    read = (f"1-day 95% VaR ≈ {v95:.1f}%: on roughly 1 trading day in 20 you'd expect a "
            f"loss worse than that{extra}. Sizing/risk context, not direction.")
    f = _factor("tail_risk", "Tail risk (VaR)", score, read)
    f["tone"] = "neutral" if score > -0.2 else "bear"
    return f


def _valuation(data) -> dict | None:
    fund = data.get("fundamentals") or {}
    tpe, fpe, peg = fund.get("Trailing P/E"), fund.get("Forward P/E"), fund.get("PEG Ratio")
    if peg is not None:
        score = 0.3 if peg < 1 else (-0.3 if peg > 2 else 0.0)
    elif fpe is not None:
        score = 0.2 if fpe < 15 else (-0.2 if fpe > 30 else 0.0)
    else:
        return None
    word = "cheap" if score > 0 else "rich" if score < 0 else "fairly priced"
    bits = []
    if tpe is not None: bits.append(f"trailing P/E {tpe:.1f}")
    if fpe is not None: bits.append(f"forward P/E {fpe:.1f}")
    if peg is not None: bits.append(f"PEG {peg:.2f}")
    read = (f"Valuation: {', '.join(bits)} — {word} on these multiples "
            f"(a weak short-term timing tool, a longer-term context).")
    return _factor("valuation", "Valuation", score, read)


def _ml(data) -> dict | None:
    sig = data.get("ml")
    if not sig:
        return None
    action, conf = sig.get("action"), sig.get("confidence") or 0
    score = {"Buy": conf, "Sell": -conf, "Hold": 0.0}.get(action, 0.0) * 0.6
    read = (f"The ML model leans {action} at {conf * 100:.0f}% confidence over "
            f"~{sig.get('horizon_days', 10)} trading days — weighted lightly here, as it "
            f"has not beaten buy-and-hold out of sample.")
    return _factor("ml", "ML signal", score, read)


# --------------------------------------------------------------------------- #
# Aggregation                                                                  #
# --------------------------------------------------------------------------- #
def compute(ticker: str, data: dict) -> dict | None:
    """Build the factor list + composite score for a ticker's analytics `data`."""
    stats = data.get("stats") or {}

    feat = None
    try:
        df = db.get_prices(ticker)
        if df is not None and len(df) >= 220:
            row = ml.build_features(df).iloc[-1]
            if not row.isna().any():
                feat = row
    except Exception:
        feat = None

    builders = []
    if feat is not None:
        builders += [_trend(feat), _momentum(feat), _volatility(feat, stats)]
    builders += [_gex(data), _monte_carlo(stats), _tail_risk(stats),
                 _valuation(data), _ml(data)]
    factors = [f for f in builders if f]
    if not factors:
        return None

    # Composite: weight-normalised average of directional scores → 0-100.
    wsum = sum(f["weight"] for f in factors) or 1.0
    blended = sum(f["score"] * f["weight"] for f in factors) / wsum
    composite = round(50 + 50 * blended)

    if composite >= 62:
        label, tone = "Bullish lean", "bull"
    elif composite >= 55:
        label, tone = "Mildly bullish", "bull"
    elif composite > 45:
        label, tone = "Neutral / mixed", "neutral"
    elif composite > 38:
        label, tone = "Mildly bearish", "bear"
    else:
        label, tone = "Bearish lean", "bear"

    return {
        "factors": factors,
        "composite": {
            "score": composite,
            "label": label,
            "tone": tone,
            "bull": sum(1 for f in factors if f["score"] > 0.15),
            "bear": sum(1 for f in factors if f["score"] < -0.15),
            "neutral": sum(1 for f in factors if -0.15 <= f["score"] <= 0.15),
        },
        "caveat": CAVEAT,
    }
