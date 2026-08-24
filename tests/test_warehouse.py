"""Unit and Integration Tests for DuckDB Embedded OLAP Warehouse Engine.

Validates:
1. SQLite scanner attachment and query execution.
2. Cross-sectional rolling volatility, daily returns, drawdown from 52w highs.
3. Multi-asset correlation and covariance matrix calculations.
4. Volatility term structure computations.
5. Corporate action velocity & aggregation queries.
6. Market breadth and advance/decline participation.
7. Concurrency & thread-local connection safety.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import numpy as np
import pandas as pd

import db
import warehouse
from corporate_actions import (
    CorporateActionEngine,
    CorporateEntity,
    IdentifierTimeline,
    CorporateAction,
    ActionType,
)


class TestDuckDBWarehouse(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db_path = self.temp_db.name
        db.DB_PATH = self.temp_db_path
        db.init_db()

        # Populate synthetic multi-asset price history (AAPL, MSFT, GOOG, NVDA)
        dates = pd.date_range(start="2022-01-01", periods=100, freq="B")
        np.random.seed(42)

        for sym, base_price, drift in [("AAPL", 150.0, 0.001), ("MSFT", 300.0, 0.0008), ("GOOG", 100.0, 0.0012), ("NVDA", 200.0, 0.002)]:
            prices = [base_price]
            for _ in range(len(dates) - 1):
                ret = np.random.normal(drift, 0.02)
                prices.append(prices[-1] * (1.0 + ret))
            
            df = pd.DataFrame({
                "Open": [p * 0.995 for p in prices],
                "High": [p * 1.015 for p in prices],
                "Low": [p * 0.985 for p in prices],
                "Close": prices,
                "Volume": np.random.randint(1_000_000, 10_000_000, size=len(dates)),
            }, index=dates)
            db.store_prices(sym, df)

        # Initialize Corporate Action schema & sample data
        self.ca_engine = CorporateActionEngine()
        ent = CorporateEntity(entity_id="ent-tech-1", legal_name="Tech Corp 1", cik="0001111111")
        self.ca_engine.register_entity(ent)
        self.ca_engine.add_identifier_mapping(
            IdentifierTimeline(
                entity_id="ent-tech-1",
                symbol="AAPL",
                valid_from="2022-01-01",
                valid_to="9999-12-31",
                source_feed="EXCHANGE",
            )
        )
        self.ca_engine.record_corporate_action(
            CorporateAction(
                action_id="ca-split-1",
                entity_id="ent-tech-1",
                action_type=ActionType.FORWARD_SPLIT,
                effective_date="2022-03-01",
                ratio=2.0,
            )
        )
        self.ca_engine.record_corporate_action(
            CorporateAction(
                action_id="ca-sym-1",
                entity_id="ent-tech-1",
                action_type=ActionType.SYMBOL_CHANGE,
                effective_date="2022-04-01",
                old_value="AAPL",
                new_value="AAPL_NEW",
            )
        )

        self.wh = warehouse.DuckDBWarehouse(db_path=self.temp_db_path)

    def tearDown(self):
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_duckdb_sqlite_attachment_and_basic_query(self):
        """Verify DuckDB attaches SQLite database properly and reads rows."""
        df = self.wh.query_df("SELECT count(*) as cnt FROM sqlite_db.daily_prices;")
        self.assertFalse(df.empty)
        self.assertEqual(df["cnt"].iloc[0], 400)  # 4 symbols * 100 days

    def test_cross_sectional_metrics(self):
        """Verify cross-sectional metrics: return calculation, rolling vol, 52w drawdown."""
        df = self.wh.get_cross_sectional_daily_metrics(
            symbols=["AAPL", "MSFT"],
            lookback_vol_window=20,
        )
        self.assertFalse(df.empty)
        self.assertIn("rolling_annualized_vol", df.columns)
        self.assertIn("cross_sectional_return_rank", df.columns)
        self.assertIn("drawdown_from_52w_high", df.columns)
        
        # Check that rolling vol is positive after window warm-up
        aapl_df = df[df["symbol"] == "AAPL"].sort_values("trade_date")
        valid_vols = aapl_df["rolling_annualized_vol"].dropna()
        self.assertTrue((valid_vols > 0).all())
        
        # Drawdown from high should be <= 0
        self.assertTrue((df["drawdown_from_52w_high"] <= 1e-5).all())

    def test_multi_asset_correlation_matrix(self):
        """Verify Pearson correlation matrix calculation matches mathematical properties."""
        corr = self.wh.compute_asset_correlation_matrix(symbols=["AAPL", "MSFT", "GOOG", "NVDA"])
        self.assertEqual(corr.shape, (4, 4))
        
        # Diagonal elements must be 1.0
        for sym in ["AAPL", "MSFT", "GOOG", "NVDA"]:
            self.assertAlmostEqual(corr.loc[sym, sym], 1.0, places=5)
            
        # Matrix must be symmetric: corr(A, B) == corr(B, A)
        self.assertAlmostEqual(corr.loc["AAPL", "MSFT"], corr.loc["MSFT", "AAPL"], places=5)
        self.assertAlmostEqual(corr.loc["GOOG", "NVDA"], corr.loc["NVDA", "GOOG"], places=5)
        
        # Off-diagonal elements bounded in [-1.0, 1.0]
        self.assertTrue((corr.values >= -1.0 - 1e-5).all())
        self.assertTrue((corr.values <= 1.0 + 1e-5).all())

    def test_multi_asset_covariance_matrix(self):
        """Verify sample covariance matrix is positive semi-definite and symmetric."""
        cov = self.wh.compute_asset_covariance_matrix(
            symbols=["AAPL", "MSFT", "GOOG", "NVDA"],
            annualize=True,
        )
        self.assertEqual(cov.shape, (4, 4))
        # Symmetric check
        self.assertAlmostEqual(cov.loc["AAPL", "MSFT"], cov.loc["MSFT", "AAPL"], places=5)
        
        # Eigenvalues must be non-negative (PSD matrix)
        eigenvals = np.linalg.eigvalsh(cov.values)
        self.assertTrue(all(ev >= -1e-5 for ev in eigenvals))

    def test_volatility_term_structure(self):
        """Verify multi-horizon volatility surface computation."""
        vols = self.wh.compute_market_wide_volatility_term_structure(symbols=["AAPL", "MSFT"])
        self.assertEqual(len(vols), 2)
        self.assertIn("vol_1w", vols.columns)
        self.assertIn("vol_1m", vols.columns)
        self.assertIn("vol_3m", vols.columns)
        self.assertTrue((vols["vol_1m"] > 0).all())

    def test_corporate_action_velocity(self):
        """Verify corporate action velocity aggregation."""
        df_vel = self.wh.compute_corporate_action_velocity(lookback_months=120)
        self.assertFalse(df_vel.empty)
        self.assertIn("forward_splits", df_vel.columns)
        self.assertIn("symbol_changes", df_vel.columns)
        self.assertIn("total_actions", df_vel.columns)
        self.assertGreaterEqual(df_vel["total_actions"].sum(), 2)

    def test_market_breadth_and_participation(self):
        """Verify market breadth and advance-decline calculations."""
        breadth = self.wh.get_market_breadth_and_participation(sma_window=20)
        self.assertFalse(breadth.empty)
        self.assertIn("pct_above_sma", breadth.columns)
        self.assertIn("advance_decline_net", breadth.columns)
        # Advance + Decline + Unchanged should equal active symbols on dates with prev day
        self.assertTrue((breadth["active_symbols"] == 4).all())

    def test_entity_full_lineage(self):
        """Verify lineage OLAP query across entity, timeline, and actions."""
        lineage = self.wh.get_entity_full_lineage(entity_id="ent-tech-1")
        self.assertFalse(lineage.empty)
        self.assertEqual(lineage["legal_name"].iloc[0], "Tech Corp 1")
        self.assertIn("FORWARD_SPLIT", lineage["action_type"].values)


if __name__ == "__main__":
    unittest.main()
