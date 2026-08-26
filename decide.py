"""Decide layer: morning watchlist scan and pre-trade checklist.

Repackages existing app metrics into pass/warn/fail flags — no new indicators.
"""
from __future__ import annotations

from datetime import datetime

import db
import ml
import pandas as pd

MOM_MIN_BARS = 253
_SEVERITY_WEIGHT = {"fail": 3, "warn": 2, "info": 1}


def _cached_universe_symbols(min_bars: int = MOM_MIN_BARS) -> list[str]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT symbol FROM daily_prices GROUP BY symbol HAVING COUNT(*) >= ?",
            (min_bars,),
        ).fetchall()
    return [r["symbol"] for r in rows]


def compute_universe_momentum_ranks() -> tuple[dict[str, int], int]:
    """12-1 momentum rank for each cached symbol (1 = highest)."""
    scores: dict[str, float] = {}
    for symbol in _cached_universe_symbols():
        df = db.get_prices(symbol)
        if df is None or len(df) < MOM_MIN_BARS:
            continue
        p_past = float(df["close"].iloc[-253])
        p_recent = float(df["close"].iloc[-22])
        if p_past > 0:
            scores[symbol] = (p_recent - p_past) / p_past
    sorted_univ = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ranks = {s: idx + 1 for idx, (s, _) in enumerate(sorted_univ)}
    return ranks, len(sorted_univ)


def days_until_earnings(fundamentals: dict | None) -> int | None:
    if not fundamentals:
        return None
    raw = fundamentals.get("Next Earnings")
    if not raw:
        return None
    try:
        dt = pd.to_datetime(raw).date()
        return (dt - datetime.now().date()).days
    except Exception:
        return None


def price_changes(ticker: str, *, fast: bool = False) -> dict:
    """Lightweight 1D / 5D % change without chart generation."""
    from app import (
        _close_n_sessions_ago,
        _is_weekend,
        get_current_price_yfinance,
        get_or_fetch_prices,
    )

    ticker = ticker.upper()
    df = db.get_prices(ticker) if fast else get_or_fetch_prices(ticker)
    if df is None or df.empty:
        return {"pct_1d": None, "pct_5d": None}

    live = None if fast else get_current_price_yfinance(ticker)
    live_mode = live is not None and not _is_weekend()
    current = live if live_mode else float(df["close"].iloc[-1])

    out = {"pct_1d": None, "pct_5d": None}
    if _is_weekend():
        return out

    for label, n in (("pct_1d", 1), ("pct_5d", 5)):
        hist = _close_n_sessions_ago(df, n, live_mode=live_mode)
        if hist and hist > 0:
            out[label] = round((current - hist) / hist * 100, 2)
    return out


def _sma20_and_extension(ticker: str, current_price: float) -> tuple[float | None, bool]:
    df = db.get_prices(ticker)
    if df is None or len(df) < 20:
        return None, False
    sma20 = float(df["close"].tail(20).mean())
    from app import get_price_ranges

    ranges = get_price_ranges(ticker, current_price=current_price)
    atr = ranges.get("atr_30d") if ranges else None
    if not atr or atr <= 0:
        return sma20, False
    return sma20, abs(current_price - sma20) > 2 * atr


def _build_flags(
    ticker: str,
    ranks: dict[str, int],
    universe_size: int,
    current_price: float | None,
    *,
    fast: bool = False,
) -> list[dict]:
    flags: list[dict] = []

    if not fast:
        from app import compute_options_analysis, get_fundamentals, get_insider_summary

        fundamentals = get_fundamentals(ticker)
        earn_days = days_until_earnings(fundamentals)
        if earn_days is not None and earn_days <= 5:
            sev = "fail" if earn_days <= 2 else "warn"
            flags.append({
                "code": "EARNINGS",
                "label": f"Earnings in {earn_days}d",
                "severity": sev,
            })

        try:
            opts = compute_options_analysis(ticker)
            iv_rank = opts.get("iv_rank") if opts else None
            if iv_rank is not None and iv_rank > 70:
                flags.append({
                    "code": "IV_HIGH",
                    "label": f"IV rank {iv_rank:.0f}",
                    "severity": "warn",
                })
        except Exception:
            pass

        try:
            insider = get_insider_summary(ticker)
            if insider and insider.get("n_sells", 0) > insider.get("n_buys", 0):
                flags.append({
                    "code": "INSIDER_SELL",
                    "label": "Insider net sell",
                    "severity": "warn",
                })
        except Exception:
            pass

    rank = ranks.get(ticker)
    if rank is not None and universe_size > 0:
        pct = rank / universe_size
        if pct <= 0.10:
            flags.append({
                "code": "MOM_TOP",
                "label": f"Mom #{rank}/{universe_size}",
                "severity": "info",
            })
        elif pct >= 0.90:
            flags.append({
                "code": "MOM_BOTTOM",
                "label": f"Mom #{rank}/{universe_size}",
                "severity": "warn",
            })

    if current_price is not None:
        _, extended = _sma20_and_extension(ticker, current_price)
        if extended:
            flags.append({
                "code": "EXTENDED",
                "label": ">2 ATR from 20d",
                "severity": "warn",
            })

    return flags


def snapshot_ticker(
    symbol: str,
    ranks: dict[str, int],
    universe_size: int,
    trade_type: str = "long_stock",
    note: str = "",
    *,
    fast: bool = False,
) -> dict:
    symbol = symbol.upper()
    changes = price_changes(symbol, fast=fast)
    current = None
    df = db.get_prices(symbol)
    if df is not None and not df.empty:
        if fast:
            current = float(df["close"].iloc[-1])
        else:
            from app import get_current_price_yfinance

            live = get_current_price_yfinance(symbol)
            current = live if live is not None else float(df["close"].iloc[-1])

    flags = _build_flags(symbol, ranks, universe_size, current, fast=fast)
    fail_n = sum(1 for f in flags if f["severity"] == "fail")
    warn_n = sum(1 for f in flags if f["severity"] == "warn")

    return {
        "symbol": symbol,
        "trade_type": trade_type,
        "note": note or "",
        "pct_1d": changes.get("pct_1d"),
        "pct_5d": changes.get("pct_5d"),
        "mom_rank": ranks.get(symbol),
        "mom_universe": universe_size,
        "flags": flags,
        "flag_score": fail_n * 10 + warn_n + len(flags) * 0.1,
    }


def build_morning_scan() -> list[dict]:
    rows = db.watchlist_list()
    if not rows:
        return []
    ranks, universe_size = compute_universe_momentum_ranks()
    items = [
        {
            "symbol": r["symbol"],
            "trade_type": r["trade_type"],
            "note": r["note"] or "",
        }
        for r in rows
    ]

    snapshots: list[dict] = []
    for item in items:
        try:
            snapshots.append(
                snapshot_ticker(
                    item["symbol"],
                    ranks,
                    universe_size,
                    item["trade_type"],
                    item["note"],
                    fast=True,
                )
            )
        except Exception as e:
            print(f"Morning snapshot failed for {item['symbol']}: {e}")

    snapshots.sort(
        key=lambda s: (-s["flag_score"], s["mom_rank"] if s["mom_rank"] else 9999)
    )
    return snapshots


def _check_row(key: str, label: str, status: str, reason: str) -> dict:
    return {"key": key, "label": label, "status": status, "reason": reason}


def build_checklist(
    ticker: str,
    data: dict,
    trade_type: str = "long_stock",
) -> dict:
    """Pre-trade checklist from analytics page data + lightweight fetches."""
    from app import compute_options_analysis, get_fundamentals

    ticker = ticker.upper()
    checks: list[dict] = []
    ranks, universe_size = compute_universe_momentum_ranks()
    rank = ranks.get(ticker)
    fundamentals = data.get("fundamentals") or get_fundamentals(ticker)
    current = data.get("current_price")
    gex = data.get("gex") or {}
    df = db.get_prices(ticker)

    # Trend
    feat = None
    try:
        if df is not None and len(df) >= 220:
            row = ml.build_features(df).iloc[-1]
            if not row.isna().any():
                feat = row
    except Exception:
        feat = None

    if feat is not None:
        d20, d50 = float(feat["dist_sma20"]), float(feat["dist_sma50"])
        if d50 > 0:
            st, reason = "pass", f"Above 50d MA ({d50:+.1%})"
        elif d50 <= 0 and d20 > 0:
            st, reason = "warn", "Below 50d MA but above 20d — mixed trend"
        else:
            st, reason = "fail", f"Below 50d MA ({d50:+.1%})"
        checks.append(_check_row("trend", "Trend", st, reason))
    else:
        checks.append(_check_row("trend", "Trend", "skip", "Insufficient price history"))

    # Momentum rank
    if rank is not None and universe_size > 0:
        pct = rank / universe_size
        if pct <= 0.50:
            st, reason = "pass", f"Rank #{rank} of {universe_size} (top half)"
        elif pct >= 0.90:
            st, reason = "fail", f"Rank #{rank} of {universe_size} (bottom decile)"
        elif pct >= 0.75:
            st, reason = "warn", f"Rank #{rank} of {universe_size} (bottom quartile)"
        else:
            st, reason = "pass", f"Rank #{rank} of {universe_size}"
        checks.append(_check_row("momentum", "Momentum", st, reason))
    else:
        checks.append(_check_row("momentum", "Momentum", "skip", "Not in cached universe"))

    # IV / premium
    opts = None
    try:
        opts = compute_options_analysis(ticker)
    except Exception:
        pass
    iv_rank = opts.get("iv_rank") if opts else None
    if iv_rank is not None:
        if iv_rank <= 50:
            st, reason = "pass", f"IV rank {iv_rank:.0f} — moderate"
        elif iv_rank <= 70:
            st, reason = "warn", f"IV rank {iv_rank:.0f} — elevated premium"
        else:
            st, reason = "fail", f"IV rank {iv_rank:.0f} — expensive options"
        checks.append(_check_row("iv", "IV / premium", st, reason))
    else:
        checks.append(_check_row("iv", "IV / premium", "skip", "Options data unavailable"))

    # Earnings
    earn_days = days_until_earnings(fundamentals)
    if earn_days is not None:
        if earn_days > 5:
            st, reason = "pass", f"Earnings in {earn_days}d"
        elif earn_days >= 3:
            st, reason = "warn", f"Earnings in {earn_days}d — event risk"
        else:
            st, reason = "fail", f"Earnings in {earn_days}d — too close"
        checks.append(_check_row("earnings", "Earnings", st, reason))
    else:
        checks.append(_check_row("earnings", "Earnings", "skip", "No earnings date on file"))

    # GEX regime
    flip = gex.get("gamma_flip")
    if flip is not None and current:
        if current >= flip:
            st, reason = "pass", f"Spot ${current:.2f} ≥ γ-flip ${flip:.2f}"
        else:
            st, reason = "fail", f"Spot ${current:.2f} below γ-flip ${flip:.2f}"
        checks.append(_check_row("gex", "GEX regime", st, reason))
    else:
        checks.append(_check_row("gex", "GEX regime", "warn", "GEX levels unavailable"))

    # Extension
    if current and feat is not None:
        from app import get_price_ranges

        ranges = get_price_ranges(ticker, current_price=current)
        atr = ranges.get("atr_30d") if ranges else None
        sma20 = float(df["close"].tail(20).mean()) if df is not None and len(df) >= 20 else None
        if atr and sma20 and atr > 0:
            dist = abs(current - sma20)
            ratio = dist / atr
            if ratio <= 2.0:
                st, reason = "pass", f"{ratio:.1f}× ATR from 20d mean"
            elif ratio <= 2.5:
                st, reason = "warn", f"{ratio:.1f}× ATR from 20d — stretched"
            else:
                st, reason = "fail", f"{ratio:.1f}× ATR from 20d — extended"
            checks.append(_check_row("extension", "Extension", st, reason))
        else:
            checks.append(_check_row("extension", "Extension", "skip", "ATR unavailable"))
    else:
        checks.append(_check_row("extension", "Extension", "skip", "Insufficient data"))

    passed = sum(1 for c in checks if c["status"] == "pass")
    warned = sum(1 for c in checks if c["status"] == "warn")
    failed = sum(1 for c in checks if c["status"] == "fail")
    actionable = [c for c in checks if c["status"] in ("warn", "fail")]
    risk_bits = [c["label"].lower() for c in actionable[:3]]
    summary = f"{passed}/{len(checks)} pass"
    if risk_bits:
        summary += f" — main risks: {', '.join(risk_bits)}"
    elif passed == len([c for c in checks if c["status"] != "skip"]):
        summary += " — no major flags"

    trade_labels = {
        "long_stock": "Long stock swing",
        "long_call": "Long call",
        "short_put": "Short put / premium",
        "other": "Other",
    }

    return {
        "ticker": ticker,
        "trade_type": trade_type,
        "trade_label": trade_labels.get(trade_type, trade_type),
        "checks": checks,
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "total": len(checks),
        "summary": summary,
    }
