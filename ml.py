"""Chart-pattern ML: a Buy / Hold / Sell signal from price action.

Design (see README → "ML signal"):
  * Labels via the **triple-barrier method** (López de Prado) — for each day, a
    volatility-scaled profit-take and stop-loss plus a time limit; whichever is
    hit first over the next `HORIZON` trading days gives the label. This bakes
    risk/reward into the target and yields balanced Buy(+1)/Hold(0)/Sell(-1)
    classes instead of a noisy "did it go up" guess.
  * Features are engineered from OHLCV only (returns, vol, RSI, MACD, ATR,
    distance-to-moving-averages, volume z-score, momentum) — all computed from
    data available at the close of the prediction day, so there is no lookahead.
  * Model is a gradient-boosted tree (LightGBM if installed, else sklearn's
    HistGradientBoosting). It outputs class *probabilities*, surfaced to the user
    as a confidence-weighted signal — never a hard "BUY".
  * Validation is **walk-forward with an embargo** (train on the past, test on
    the next block, gap = HORIZON to remove label overlap) and is scored against
    buy-and-hold after a cost assumption. Plain shuffled CV would leak.

This is a directional, educational signal on delayed end-of-day data — not advice.

Train offline:   python ml.py train AAPL MSFT NVDA SPY ...
Predict (used by the app):   ml.predict("AAPL")
"""
from __future__ import annotations

import os
import pickle
import sys

import numpy as np
import pandas as pd

import db

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")

HORIZON = 10        # trading days to the time barrier (multi-day "swing" target)
PT_MULT = 1.5       # profit-take barrier = PT_MULT × daily vol
SL_MULT = 1.5       # stop-loss barrier  = SL_MULT × daily vol
COST_BPS = 7.5      # round-trip transaction cost assumption, in basis points

FEATURES = [
    "ret_1", "ret_5", "ret_10", "ret_20",
    "vol_10", "vol_20",
    "rsi_14", "macd_hist",
    "atr_14", "hl_range",
    "dist_sma20", "dist_sma50", "dist_sma200",
    "vol_z", "mom_63",
    # Advanced features
    "gk_vol_10", "gk_vol_20",
    "bb_width", "bb_pct",
    "vol_trend_5_20",
    "dist_ema8", "dist_ema21", "ema_cross_8_21",
    "hl_range_std_10"
]


# --------------------------------------------------------------------------- #
# Feature engineering — every column uses only data up to and including day i. #
# --------------------------------------------------------------------------- #
def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame of FEATURES indexed like `df` (NaNs in the warm-up rows)."""
    close = df["close"]
    ret = close.pct_change()
    feat = pd.DataFrame(index=df.index)

    feat["ret_1"] = ret
    feat["ret_5"] = close.pct_change(5)
    feat["ret_10"] = close.pct_change(10)
    feat["ret_20"] = close.pct_change(20)
    feat["vol_10"] = ret.rolling(10).std()
    feat["vol_20"] = ret.rolling(20).std()
    feat["rsi_14"] = _rsi(close, 14)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    feat["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / close

    feat["atr_14"] = _atr(df, 14) / close
    feat["hl_range"] = (df["high"] - df["low"]) / close
    feat["dist_sma20"] = close / close.rolling(20).mean() - 1
    feat["dist_sma50"] = close / close.rolling(50).mean() - 1
    feat["dist_sma200"] = close / close.rolling(200).mean() - 1
    feat["vol_z"] = (df["volume"] - df["volume"].rolling(20).mean()) / (df["volume"].rolling(20).std() + 1e-8)
    feat["mom_63"] = close.pct_change(63)

    # Garman-Klass Volatility (more efficient intraday volatility estimator)
    log_hl = np.log(df["high"] / df["low"] + 1e-8)
    log_co = np.log(df["close"] / df["open"] + 1e-8)
    gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    feat["gk_vol_10"] = np.sqrt(gk.rolling(10).mean().clip(lower=0))
    feat["gk_vol_20"] = np.sqrt(gk.rolling(20).mean().clip(lower=0))

    # Bollinger Bands
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    upper_bb = sma20 + 2 * std20
    lower_bb = sma20 - 2 * std20
    feat["bb_width"] = (upper_bb - lower_bb) / (sma20 + 1e-8)
    feat["bb_pct"] = (close - lower_bb) / (upper_bb - lower_bb + 1e-8)

    # Volume trend
    feat["vol_trend_5_20"] = df["volume"].rolling(5).mean() / (df["volume"].rolling(20).mean() + 1e-8)

    # EMA crossovers
    ema8 = close.ewm(span=8, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    feat["dist_ema8"] = close / ema8 - 1
    feat["dist_ema21"] = close / ema21 - 1
    feat["ema_cross_8_21"] = ema8 / ema21 - 1

    # Intraday range std
    feat["hl_range_std_10"] = feat["hl_range"].rolling(10).std()

    return feat[FEATURES]


# --------------------------------------------------------------------------- #
# Triple-barrier labelling                                                     #
# --------------------------------------------------------------------------- #
def triple_barrier_labels(df: pd.DataFrame) -> pd.Series:
    """+1 Buy (profit-take hit first), -1 Sell (stop hit first), 0 Hold (time-out)."""
    close = df["close"].to_numpy()
    daily_vol = df["close"].pct_change().rolling(20).std().to_numpy()
    n = len(close)
    labels = np.full(n, np.nan)
    for i in range(n - 1):
        v = daily_vol[i]
        if not np.isfinite(v) or v <= 0:
            continue
        upper = close[i] * (1 + PT_MULT * v)
        lower = close[i] * (1 - SL_MULT * v)
        end = min(i + HORIZON, n - 1)
        label = 0
        for j in range(i + 1, end + 1):
            if close[j] >= upper:
                label = 1
                break
            if close[j] <= lower:
                label = -1
                break
        labels[i] = label
    return pd.Series(labels, index=df.index)


def triple_barrier_returns(df: pd.DataFrame) -> pd.Series:
    """Calculate actual trade returns (holding to exit barrier/timeout)."""
    close = df["close"].to_numpy()
    daily_vol = df["close"].pct_change().rolling(20).std().to_numpy()
    n = len(close)
    returns = np.full(n, np.nan)
    for i in range(n - 1):
        v = daily_vol[i]
        if not np.isfinite(v) or v <= 0:
            continue
        upper = close[i] * (1 + PT_MULT * v)
        lower = close[i] * (1 - SL_MULT * v)
        end = min(i + HORIZON, n - 1)
        ret = close[end] / close[i] - 1
        for j in range(i + 1, end + 1):
            if close[j] >= upper:
                ret = close[j] / close[i] - 1
                break
            if close[j] <= lower:
                ret = close[j] / close[i] - 1
                break
        returns[i] = ret
    return pd.Series(returns, index=df.index)


def _dataset(tickers: list[str]) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """Stack features + labels + dates across tickers, dropping warm-up/label-less rows."""
    X_parts, y_parts, d_parts, r_parts = [], [], [], []
    for t in tickers:
        df = db.get_prices(t)
        if df is None or len(df) < 260:
            print(f"  skip {t}: insufficient history")
            continue
        feat = build_features(df)
        lab = triple_barrier_labels(df)
        ret = triple_barrier_returns(df)
        ok = feat.notna().all(axis=1) & lab.notna() & ret.notna()
        X_parts.append(feat[ok])
        y_parts.append(lab[ok].astype(int))
        d_parts.append(pd.Series(feat.index[ok], index=feat.index[ok]))
        r_parts.append(ret[ok])
        print(f"  {t}: {int(ok.sum())} labelled samples")
    if not X_parts:
        raise SystemExit("No usable data — fetch prices for these tickers first.")
    X = pd.concat(X_parts)
    y = pd.concat(y_parts)
    dates = pd.concat(d_parts)
    returns = pd.concat(r_parts)
    order = np.argsort(dates.to_numpy())
    return (X.iloc[order].reset_index(drop=True),
            y.iloc[order].reset_index(drop=True),
            dates.iloc[order].reset_index(drop=True),
            returns.iloc[order].reset_index(drop=True))


def _new_model():
    """LightGBM if available, else sklearn's HistGradientBoosting, else None."""
    try:
        from lightgbm import LGBMClassifier
        # Regularised model with shallower tree architecture to avoid overfitting to noise
        return LGBMClassifier(n_estimators=120, learning_rate=0.015, num_leaves=7,
                              max_depth=3, subsample=0.7, colsample_bytree=0.7,
                              class_weight="balanced", min_child_samples=50,
                              reg_alpha=0.5, reg_lambda=2.0, verbose=-1)
    except Exception:
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier
            return HistGradientBoostingClassifier(max_iter=120, learning_rate=0.02,
                                                   max_leaf_nodes=7, max_depth=3,
                                                   l2_regularization=5.0)
        except Exception:
            return None


def _sample_weights(y: pd.Series) -> np.ndarray:
    """Inverse-frequency weights so the up-drift doesn't swamp Sell/Hold."""
    counts = y.value_counts()
    w = y.map(lambda c: len(y) / (len(counts) * counts[c]))
    return w.to_numpy()


# --------------------------------------------------------------------------- #
# Walk-forward validation                                                      #
# --------------------------------------------------------------------------- #
def walk_forward(X: pd.DataFrame, y: pd.Series, dates: pd.Series, returns: pd.Series, n_folds: int = 5) -> None:
    """Chronological date-based walk-forward split (prevents cross-ticker temporal leakage)."""
    unique_dates = pd.Series(sorted(dates.unique()))
    n_days = len(unique_dates)
    fold_days = n_days // (n_folds + 1)
    
    print("\nWalk-forward (after %.1f bps cost):" % COST_BPS)
    accs, edges = [], []
    
    for k in range(1, n_folds + 1):
        tr_end_idx = fold_days * k
        te_start_idx = tr_end_idx + HORIZON
        te_end_idx = fold_days * (k + 1)
        
        if te_start_idx >= n_days:
            break
            
        train_cutoff = unique_dates.iloc[tr_end_idx]
        test_start_date = unique_dates.iloc[te_start_idx]
        test_end_date = unique_dates.iloc[min(te_end_idx, n_days - 1)]
        
        train_mask = dates <= train_cutoff
        test_mask = (dates >= test_start_date) & (dates <= test_end_date)
        
        Xtr, ytr = X[train_mask], y[train_mask]
        Xte, yte = X[test_mask], y[test_mask]
        yte_ret = returns[test_mask]
        
        if len(Xtr) == 0 or len(Xte) == 0:
            continue
            
        m = _new_model()
        if m is None:
            print("  no ML backend installed (pip install lightgbm or scikit-learn)")
            return
            
        try:
            m.fit(Xtr, ytr, sample_weight=_sample_weights(ytr))
        except TypeError:
            m.fit(Xtr, ytr)
            
        pred = m.predict(Xte)
        acc = float((pred == yte.to_numpy()).mean())
        
        cost = COST_BPS / 1e4
        strat = np.where(pred == 1, yte_ret.to_numpy() - cost, 0.0).sum()
        hold = yte_ret.to_numpy().sum()
        
        accs.append(acc)
        edges.append(strat - hold)
        print(f"  fold {k} ({train_cutoff.strftime('%Y-%m-%d')} cutoff): acc={acc:.3f}  signal_edge_vs_hold={strat - hold:+.4f}")
        
    if accs:
        print(f"  mean acc={np.mean(accs):.3f}  mean edge={np.mean(edges):+.4f}  "
              f"(edge>0 means the signal beat buy-and-hold out-of-sample)")


def train(tickers: list[str]) -> None:
    print(f"Building dataset from {len(tickers)} tickers...")
    X, y, dates, returns = _dataset(tickers)
    print(f"Total samples: {len(X)}  class balance: {dict(y.value_counts())}")
    
    walk_forward(X, y, dates, returns)

    model = _new_model()
    if model is None:
        raise SystemExit("Install a backend first: pip install lightgbm  (or scikit-learn)")
    try:
        model.fit(X, y, sample_weight=_sample_weights(y))
    except TypeError:
        model.fit(X, y)
        
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "features": FEATURES, "horizon": HORIZON}, f)
    print(f"\nSaved model → {MODEL_PATH}")


# --------------------------------------------------------------------------- #
# Serving                                                                      #
# --------------------------------------------------------------------------- #
_CACHE_TTL_HOURS = 6


def _load():
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def predict(ticker: str) -> dict | None:
    """Return {action, proba:{buy,hold,sell}, confidence, top_features} or None.

    Cached per ticker for a few hours — the signal only moves with the daily bar.
    Returns None when no model has been trained yet (run `python ml.py train ...`).
    """
    ticker = ticker.upper()
    hit = db.cache_get("ml", ticker, _CACHE_TTL_HOURS)
    if hit is not None:
        return hit

    bundle = _load()
    if bundle is None:
        return None
    df = db.get_prices(ticker)
    if df is None or len(df) < 220:
        return None

    feat = build_features(df).iloc[[-1]]
    if feat.isna().any(axis=1).iloc[0]:
        return None

    model = bundle["model"]
    classes = list(model.classes_)
    proba = model.predict_proba(feat)[0]
    p = {int(c): float(pr) for c, pr in zip(classes, proba)}
    buy, hold, sell = p.get(1, 0.0), p.get(0, 0.0), p.get(-1, 0.0)
    top = int(max(p, key=p.get))
    action = {1: "Buy", 0: "Hold", -1: "Sell"}[top]

    result = {
        "action": action,
        "proba": {"buy": round(buy, 3), "hold": round(hold, 3), "sell": round(sell, 3)},
        "confidence": round(max(buy, hold, sell), 3),
        "horizon_days": bundle.get("horizon", HORIZON),
    }
    db.cache_set("ml", ticker, result)
    return result


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "train":
        train([t.upper() for t in sys.argv[2:]])
    else:
        print(__doc__)
        print("\nUsage: python ml.py train TICKER [TICKER ...]")
