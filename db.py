import sqlite3
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
        """)


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
