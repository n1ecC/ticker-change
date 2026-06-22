from __future__ import annotations
import sqlite3
import json
import pandas as pd
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = "stocks.db"
FRESHNESS_HOURS = 1


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tickers (
                symbol       TEXT PRIMARY KEY,
                last_fetched DATETIME NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_prices (
                symbol  TEXT    NOT NULL,
                date    DATE    NOT NULL,
                open    REAL    NOT NULL,
                high    REAL    NOT NULL,
                low     REAL    NOT NULL,
                close   REAL    NOT NULL,
                volume  INTEGER NOT NULL,
                PRIMARY KEY (symbol, date)
            );

            CREATE TABLE IF NOT EXISTS api_cache (
                provider   TEXT     NOT NULL,
                key        TEXT     NOT NULL,
                fetched_at DATETIME NOT NULL,
                payload    TEXT     NOT NULL,
                PRIMARY KEY (provider, key)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)


def get_setting(key: str, default: str = "") -> str:
    """Return a stored app setting (e.g. a user-supplied API key), or default."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
    except sqlite3.OperationalError:
        return default  # table not created yet
    return row["value"] if row is not None else default


def set_setting(key: str, value: str):
    """Insert or update an app setting."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value),
        )


def get_all_settings() -> dict:
    """Return every stored app setting as a {key: value} dict."""
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["key"]: r["value"] for r in rows}


def cache_get(provider: str, key: str, ttl_hours: float):
    """Return cached JSON payload if present and younger than ttl_hours, else None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM api_cache WHERE provider = ? AND key = ?",
            (provider, key),
        ).fetchone()
    if row is None:
        return None
    fetched_at = datetime.fromisoformat(row["fetched_at"])
    if datetime.utcnow() - fetched_at > timedelta(hours=ttl_hours):
        return None
    try:
        return json.loads(row["payload"])
    except (ValueError, TypeError):
        return None


def cache_set(provider: str, key: str, payload):
    """Store a JSON-serialisable payload under (provider, key)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO api_cache (provider, key, fetched_at, payload) "
            "VALUES (?, ?, ?, ?)",
            (provider, key, datetime.utcnow().isoformat(), json.dumps(payload)),
        )


def is_fresh(symbol: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_fetched FROM tickers WHERE symbol = ?",
            (symbol.upper(),)
        ).fetchone()
    if row is None:
        return False
    last_fetched = datetime.fromisoformat(row["last_fetched"])
    return datetime.utcnow() - last_fetched < timedelta(hours=FRESHNESS_HOURS)


def get_prices(symbol: str) -> pd.DataFrame | None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM daily_prices WHERE symbol = ? ORDER BY date ASC",
            (symbol.upper(),)
        ).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df


def store_prices(symbol: str, df: pd.DataFrame):
    symbol = symbol.upper()
    df = df.copy()
    # Drop rows with any NaN in essential columns to avoid IntegrityError in SQLite
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df.index).tz_localize(None)

    rows = [
        (
            symbol,
            idx.strftime("%Y-%m-%d"),
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            int(row["Volume"]),
        )
        for idx, row in df.iterrows()
    ]

    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_prices (symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO tickers (symbol, last_fetched) VALUES (?, ?)",
            (symbol, datetime.utcnow().isoformat()),
        )
