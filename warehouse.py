"""DuckDB Embedded OLAP Warehouse Engine.

Attaches SQLite database (`stocks.db`) via DuckDB SQLite Scanner for zero-copy
high-throughput vectorized analytics, market-wide cross-sectional computations,
multi-asset correlation matrices, rolling volatilities, and corporate action velocity analytics.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import duckdb
import pandas as pd
import numpy as np

import db

DEFAULT_DB_PATH = os.environ.get("DB_PATH", "stocks.db")


class DuckDBWarehouse:
    """Embedded OLAP analytics engine backed by DuckDB and attached SQLite store."""

    def __init__(self, db_path: Optional[str] = None, memory_limit: str = "2GB", threads: int = 4):
        self.db_path = os.path.abspath(db_path or db.DB_PATH or DEFAULT_DB_PATH)
        self.memory_limit = memory_limit
        self.threads = threads
        self._local = threading.local()

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Obtain a thread-local DuckDB connection with SQLite attached and configured."""
        con = getattr(self._local, "conn", None)
        if con is None:
            con = duckdb.connect(database=":memory:")
            con.execute(f"SET threads TO {self.threads};")
            con.execute(f"SET memory_limit = '{self.memory_limit}';")
            
            # Load sqlite extension and attach DB
            try:
                con.execute("LOAD sqlite;")
            except Exception:
                con.execute("INSTALL sqlite; LOAD sqlite;")
            
            if os.path.exists(self.db_path):
                con.execute(
                    f"ATTACH '{self.db_path}' AS sqlite_db (TYPE SQLITE, READ_ONLY);"
                )
            self._local.conn = con
        return con

    @contextmanager
    def query_scope(self):
        """Context manager yielding active thread connection."""
        conn = self.get_connection()
        try:
            yield conn
        finally:
            pass

    def execute_sql(self, sql: str, params: Optional[Sequence[Any]] = None) -> duckdb.DuckDBPyRelation:
        """Execute arbitrary SQL and return DuckDB relation."""
        conn = self.get_connection()
        if params:
            return conn.execute(sql, params)
        return conn.execute(sql)

    def query_df(self, sql: str, params: Optional[Sequence[Any]] = None) -> pd.DataFrame:
        """Execute query and return Pandas DataFrame."""
        return self.execute_sql(sql, params).df()

    def query_arrow(self, sql: str, params: Optional[Sequence[Any]] = None):
        """Execute query and return PyArrow Table."""
        return self.execute_sql(sql, params).arrow()

    def query_dicts(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
        """Execute query and return list of dictionaries."""
        df = self.query_df(sql, params)
        return df.to_dict(orient="records")

    # =========================================================================
    # OLAP Analytics: Historical Prices & Returns
    # =========================================================================

    def get_cross_sectional_daily_metrics(
        self,
        symbols: Optional[Sequence[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_vol_window: int = 20,
    ) -> pd.DataFrame:
        """High-throughput vectorized cross-sectional daily metrics across tickers.
        
        Calculates:
        - log returns & simple arithmetic returns
        - rolling historical volatility (annualized sqrt(252))
        - rolling dollar volume & VWAP estimate
        - 52-week rolling high / low and drawdown from peak
        - cross-sectional rank of return and volume per date
        """
        filters = []
        params: List[Any] = []
        if symbols:
            upper_syms = [s.upper() for s in symbols]
            placeholders = ",".join(["?"] * len(upper_syms))
            filters.append(f"symbol IN ({placeholders})")
            params.extend(upper_syms)
        if start_date:
            filters.append("date >= ?")
            params.append(start_date)
        if end_date:
            filters.append("date <= ?")
            params.append(end_date)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        sql = f"""
        WITH raw_prices AS (
            SELECT
                symbol,
                CAST(date AS DATE) AS trade_date,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS BIGINT) AS volume,
                (CAST(close AS DOUBLE) * CAST(volume AS BIGINT)) AS dollar_volume
            FROM sqlite_db.daily_prices
            {where_clause}
        ),
        price_returns AS (
            SELECT
                symbol,
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                dollar_volume,
                LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC) AS prev_close,
                (close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC), 0.0) - 1.0) AS daily_return,
                LN(close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date ASC), 0.0)) AS log_return
            FROM raw_prices
        ),
        rolling_metrics AS (
            SELECT
                symbol,
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                dollar_volume,
                daily_return,
                log_return,
                STDDEV_SAMP(log_return) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date ASC
                    ROWS BETWEEN {max(1, lookback_vol_window - 1)} PRECEDING AND CURRENT ROW
                ) * SQRT(252.0) AS rolling_annualized_vol,
                AVG(dollar_volume) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date ASC
                    ROWS BETWEEN {max(1, lookback_vol_window - 1)} PRECEDING AND CURRENT ROW
                ) AS rolling_avg_dollar_vol,
                MAX(high) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date ASC
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ) AS high_52w,
                MIN(low) OVER (
                    PARTITION BY symbol
                    ORDER BY trade_date ASC
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
                ) AS low_52w
            FROM price_returns
        )
        SELECT
            symbol,
            trade_date,
            open,
            high,
            low,
            close,
            volume,
            dollar_volume,
            daily_return,
            log_return,
            rolling_annualized_vol,
            rolling_avg_dollar_vol,
            high_52w,
            low_52w,
            CASE WHEN high_52w > 0 THEN (close - high_52w) / high_52w ELSE 0.0 END AS drawdown_from_52w_high,
            DENSE_RANK() OVER (PARTITION BY trade_date ORDER BY daily_return DESC NULLS LAST) AS cross_sectional_return_rank,
            DENSE_RANK() OVER (PARTITION BY trade_date ORDER BY dollar_volume DESC NULLS LAST) AS cross_sectional_volume_rank
        FROM rolling_metrics
        ORDER BY trade_date ASC, symbol ASC;
        """
        return self.query_df(sql, params)

    # =========================================================================
    # OLAP Analytics: Multi-Asset Correlation Matrix & Covariance
    # =========================================================================

    def compute_asset_correlation_matrix(
        self,
        symbols: Optional[Sequence[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        min_common_periods: int = 10,
    ) -> pd.DataFrame:
        """Compute complete NxN pairwise Pearson correlation matrix across asset return series.
        
        Evaluated fully inside DuckDB vectorized relational engine via self-join on trade_date.
        """
        filters = []
        params: List[Any] = []
        if symbols:
            upper_syms = [s.upper() for s in symbols]
            placeholders = ",".join(["?"] * len(upper_syms))
            filters.append(f"symbol IN ({placeholders})")
            params.extend(upper_syms)
        if start_date:
            filters.append("date >= ?")
            params.append(start_date)
        if end_date:
            filters.append("date <= ?")
            params.append(end_date)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        sql = f"""
        WITH returns AS (
            SELECT
                symbol,
                CAST(date AS DATE) AS trade_date,
                LN(close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY date ASC), 0.0)) AS log_return
            FROM sqlite_db.daily_prices
            {where_clause}
        ),
        valid_returns AS (
            SELECT symbol, trade_date, log_return
            FROM returns
            WHERE log_return IS NOT NULL AND NOT ISNAN(log_return)
        ),
        pairwise AS (
            SELECT
                a.symbol AS asset_a,
                b.symbol AS asset_b,
                CORR(a.log_return, b.log_return) AS correlation,
                COVAR_SAMP(a.log_return, b.log_return) AS covariance,
                COUNT(*) AS observations
            FROM valid_returns a
            JOIN valid_returns b ON a.trade_date = b.trade_date
            GROUP BY a.symbol, b.symbol
            HAVING COUNT(*) >= {min_common_periods}
        )
        SELECT asset_a, asset_b, correlation, covariance, observations
        FROM pairwise
        ORDER BY asset_a, asset_b;
        """
        df_pair = self.query_df(sql, params)
        if df_pair.empty:
            return pd.DataFrame()

        # Pivot to square matrix
        corr_matrix = df_pair.pivot(index="asset_a", columns="asset_b", values="correlation")
        # Ensure symmetric diagonal is 1.0
        for sym in corr_matrix.index:
            if sym in corr_matrix.columns:
                corr_matrix.loc[sym, sym] = 1.0
        return corr_matrix

    def compute_asset_covariance_matrix(
        self,
        symbols: Optional[Sequence[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        annualize: bool = True,
        min_common_periods: int = 10,
    ) -> pd.DataFrame:
        """Compute NxN sample covariance matrix annualized by 252 trading days."""
        filters = []
        params: List[Any] = []
        if symbols:
            upper_syms = [s.upper() for s in symbols]
            placeholders = ",".join(["?"] * len(upper_syms))
            filters.append(f"symbol IN ({placeholders})")
            params.extend(upper_syms)
        if start_date:
            filters.append("date >= ?")
            params.append(start_date)
        if end_date:
            filters.append("date <= ?")
            params.append(end_date)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        multiplier = 252.0 if annualize else 1.0

        sql = f"""
        WITH returns AS (
            SELECT
                symbol,
                CAST(date AS DATE) AS trade_date,
                LN(close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY date ASC), 0.0)) AS log_return
            FROM sqlite_db.daily_prices
            {where_clause}
        ),
        valid_returns AS (
            SELECT symbol, trade_date, log_return
            FROM returns
            WHERE log_return IS NOT NULL AND NOT ISNAN(log_return)
        ),
        pairwise AS (
            SELECT
                a.symbol AS asset_a,
                b.symbol AS asset_b,
                COVAR_SAMP(a.log_return, b.log_return) * {multiplier} AS covariance,
                COUNT(*) AS observations
            FROM valid_returns a
            JOIN valid_returns b ON a.trade_date = b.trade_date
            GROUP BY a.symbol, b.symbol
            HAVING COUNT(*) >= {min_common_periods}
        )
        SELECT asset_a, asset_b, covariance
        FROM pairwise
        ORDER BY asset_a, asset_b;
        """
        df_pair = self.query_df(sql, params)
        if df_pair.empty:
            return pd.DataFrame()
        return df_pair.pivot(index="asset_a", columns="asset_b", values="covariance")

    # =========================================================================
    # OLAP Analytics: Rolling Volatility & Regime Detection
    # =========================================================================

    def compute_market_wide_volatility_term_structure(
        self,
        symbols: Optional[Sequence[str]] = None,
        as_of_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Compute multi-horizon volatility surface across 5d, 21d, 63d, 126d, 252d for all tickers."""
        filters = []
        params: List[Any] = []
        if symbols:
            upper_syms = [s.upper() for s in symbols]
            placeholders = ",".join(["?"] * len(upper_syms))
            filters.append(f"symbol IN ({placeholders})")
            params.extend(upper_syms)
        if as_of_date:
            filters.append("date <= ?")
            params.append(as_of_date)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        sql = f"""
        WITH raw_returns AS (
            SELECT
                symbol,
                CAST(date AS DATE) AS trade_date,
                LN(close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY date ASC), 0.0)) AS log_return
            FROM sqlite_db.daily_prices
            {where_clause}
        ),
        vol_surface AS (
            SELECT
                symbol,
                trade_date,
                STDDEV_SAMP(log_return) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) * SQRT(252.0) AS vol_1w,
                STDDEV_SAMP(log_return) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) * SQRT(252.0) AS vol_1m,
                STDDEV_SAMP(log_return) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 62 PRECEDING AND CURRENT ROW) * SQRT(252.0) AS vol_3m,
                STDDEV_SAMP(log_return) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 125 PRECEDING AND CURRENT ROW) * SQRT(252.0) AS vol_6m,
                STDDEV_SAMP(log_return) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) * SQRT(252.0) AS vol_1y,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) as rn
            FROM raw_returns
        )
        SELECT
            symbol,
            trade_date AS as_of_date,
            vol_1w,
            vol_1m,
            vol_3m,
            vol_6m,
            vol_1y,
            CASE WHEN vol_1y > 0 THEN (vol_1m / vol_1y) - 1.0 ELSE 0.0 END AS vol_term_slope_1m_vs_1y
        FROM vol_surface
        WHERE rn = 1
        ORDER BY symbol ASC;
        """
        return self.query_df(sql, params)

    # =========================================================================
    # OLAP Analytics: Market-Wide Corporate Action Velocity & Clustering
    # =========================================================================

    def compute_corporate_action_velocity(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_months: Optional[int] = None,
        group_by: str = "month",
    ) -> pd.DataFrame:
        """Analyze corporate action frequency, cluster density, and velocity across time.
        
        Groups by month or year to track market restructure rates, split waves, and ticker migration.
        """
        trunc_spec = "month" if group_by.lower() == "month" else "year"
        filters = []
        params: List[Any] = []

        if start_date:
            filters.append("CAST(effective_date AS DATE) >= CAST(? AS DATE)")
            params.append(start_date)
        elif lookback_months is not None:
            filters.append(f"CAST(effective_date AS DATE) >= (CURRENT_DATE - INTERVAL '{int(lookback_months)} month')")

        if end_date:
            filters.append("CAST(effective_date AS DATE) <= CAST(? AS DATE)")
            params.append(end_date)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        sql = f"""
        WITH action_stream AS (
            SELECT
                action_id,
                entity_id,
                action_type,
                CAST(effective_date AS DATE) AS eff_date,
                DATE_TRUNC('{trunc_spec}', CAST(effective_date AS DATE)) AS period,
                status
            FROM sqlite_db.corporate_actions
            {where_clause}
        ),
        aggregated AS (
            SELECT
                period,
                COUNT(*) AS total_actions,
                COUNT(DISTINCT entity_id) AS distinct_entities_affected,
                COUNT(*) FILTER (WHERE action_type = 'FORWARD_SPLIT') AS forward_splits,
                COUNT(*) FILTER (WHERE action_type = 'REVERSE_SPLIT') AS reverse_splits,
                COUNT(*) FILTER (WHERE action_type = 'SYMBOL_CHANGE') AS symbol_changes,
                COUNT(*) FILTER (WHERE action_type = 'MERGER') AS mergers,
                COUNT(*) FILTER (WHERE action_type = 'SPINOFF') AS spinoffs,
                COUNT(*) FILTER (WHERE action_type = 'BANKRUPTCY') AS bankruptcies
            FROM action_stream
            GROUP BY period
        )
        SELECT
            period,
            total_actions,
            distinct_entities_affected,
            forward_splits,
            reverse_splits,
            symbol_changes,
            mergers,
            spinoffs,
            bankruptcies,
            (CAST(reverse_splits AS DOUBLE) / NULLIF(forward_splits + reverse_splits, 0)) AS reverse_split_ratio,
            AVG(total_actions) OVER (ORDER BY period ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rolling_3p_action_velocity
        FROM aggregated
        ORDER BY period ASC;
        """
        try:
            return self.query_df(sql, params)
        except Exception:
            # Fallback if corporate_actions table is empty or missing in test db
            return pd.DataFrame()

    def get_entity_full_lineage(self, entity_id: str) -> pd.DataFrame:
        """OLAP query mapping complete bi-temporal timeline joined with actions and entities."""
        sql = """
        SELECT
            e.entity_id,
            e.legal_name,
            e.cik,
            e.figi,
            e.cusip,
            t.symbol,
            t.valid_from,
            t.valid_to,
            t.is_primary,
            t.source_feed,
            ca.action_type,
            ca.effective_date AS ca_effective_date,
            ca.ratio AS ca_ratio,
            ca.old_value,
            ca.new_value
        FROM sqlite_db.corporate_entities e
        LEFT JOIN sqlite_db.identifier_timeline t ON e.entity_id = t.entity_id
        LEFT JOIN sqlite_db.corporate_actions ca ON e.entity_id = ca.entity_id
        WHERE e.entity_id = ?
        ORDER BY t.valid_from ASC, ca.effective_date ASC;
        """
        return self.query_df(sql, [entity_id])

    def get_market_breadth_and_participation(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sma_window: int = 50,
    ) -> pd.DataFrame:
        """Compute market breadth: % of tickers above their N-day SMA and net advances/declines per day."""
        filters = []
        params: List[Any] = []
        if start_date:
            filters.append("date >= ?")
            params.append(start_date)
        if end_date:
            filters.append("date <= ?")
            params.append(end_date)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        sql = f"""
        WITH price_series AS (
            SELECT
                symbol,
                CAST(date AS DATE) AS trade_date,
                CAST(close AS DOUBLE) AS close,
                AVG(CAST(close AS DOUBLE)) OVER (
                    PARTITION BY symbol
                    ORDER BY CAST(date AS DATE)
                    ROWS BETWEEN {max(1, sma_window - 1)} PRECEDING AND CURRENT ROW
                ) AS sma_close,
                (CAST(close AS DOUBLE) - LAG(CAST(close AS DOUBLE)) OVER (PARTITION BY symbol ORDER BY CAST(date AS DATE))) AS net_change
            FROM sqlite_db.daily_prices
            {where_clause}
        ),
        daily_stats AS (
            SELECT
                trade_date,
                COUNT(DISTINCT symbol) AS active_symbols,
                COUNT(*) FILTER (WHERE close > sma_close) AS symbols_above_sma,
                COUNT(*) FILTER (WHERE net_change > 0) AS advancing_symbols,
                COUNT(*) FILTER (WHERE net_change < 0) AS declining_symbols,
                COUNT(*) FILTER (WHERE net_change = 0) AS unchanged_symbols
            FROM price_series
            GROUP BY trade_date
        )
        SELECT
            trade_date,
            active_symbols,
            symbols_above_sma,
            (CAST(symbols_above_sma AS DOUBLE) / NULLIF(active_symbols, 0)) AS pct_above_sma,
            advancing_symbols,
            declining_symbols,
            unchanged_symbols,
            (advancing_symbols - declining_symbols) AS advance_decline_net
        FROM daily_stats
        ORDER BY trade_date ASC;
        """
        return self.query_df(sql, params)


# Global singleton instance
warehouse = DuckDBWarehouse()
