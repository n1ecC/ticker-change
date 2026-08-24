"""Comprehensive Unit Test Suite for All 5 Analytical Modules.

Tests:
1. Event Study & CAR Engine (`event_study.py`)
2. SEC EDGAR 8-K Parser & Item Classification (`sec_8k.py`)
3. Microstructure Analytics: VPIN, Corwin-Schultz, Squeeze Risk (`microstructure.py`)
4. Macro-Financial Conditioning & Dual Betas (`macro_engine.py`)
5. Higher-Order Black-Scholes Greeks: Vanna, Charm, Vomma, VRP (`derivatives_alpha.py`)
6. Signal Walk-Forward Backtester (`backtest_engine.py`)
"""
import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from event_study import run_event_study
from sec_8k import ITEM_DEFINITIONS
from microstructure import (
    calculate_corwin_schultz_spread,
    calculate_roll_effective_spread,
    calculate_vpin,
    compute_squeeze_risk_index,
    get_microstructure_analytics,
)
from macro_engine import calculate_regime_conditional_betas, calculate_capture_ratios
from derivatives_alpha import compute_higher_order_greeks, calculate_variance_risk_premium
from backtest_engine import run_signals_backtest


class TestAnalyticalModules(unittest.TestCase):

    def setUp(self):
        # Synthetic daily OHLCV series for 200 trading days
        np.random.seed(42)
        dates = pd.date_range(start="2023-01-01", periods=200, freq="B")
        base_price = 100.0
        returns = np.random.normal(0.0005, 0.015, size=200)
        prices = base_price * np.exp(np.cumsum(returns))

        self.stock_df = pd.DataFrame({
            "close": prices,
            "open": prices * (1.0 + np.random.normal(0, 0.002, 200)),
            "high": prices * 1.01,
            "low": prices * 0.99,
            "volume": np.random.randint(500000, 2000000, 200),
        }, index=dates)

        bench_returns = np.random.normal(0.0003, 0.01, size=200)
        bench_prices = 400.0 * np.exp(np.cumsum(bench_returns))
        self.bench_df = pd.DataFrame({
            "close": bench_prices,
        }, index=dates)

    def test_event_study_car_calculation(self):
        """Verify CAR and statistical significance computation."""
        # Event at day 150
        event_dt = self.stock_df.index[150].strftime("%Y-%m-%d")
        res = run_event_study(
            self.stock_df,
            self.bench_df,
            event_date=event_dt,
            event_type="SPLIT",
            ticker="AAPL",
            estimation_window=(-100, -21),
            event_window=(-5, 10),
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.ticker, "AAPL")
        self.assertIsInstance(res.car, float)
        self.assertIsInstance(res.car_t_stat, float)
        self.assertEqual(len(res.daily_abnormal_returns), 16)  # -5 to +10 = 16 days

    def test_microstructure_spreads_and_vpin(self):
        """Verify Corwin-Schultz, Roll Spread, VPIN, and Squeeze Index."""
        cs = calculate_corwin_schultz_spread(self.stock_df)
        self.assertGreaterEqual(cs, 0.0)
        self.assertLessEqual(cs, 0.20)

        roll = calculate_roll_effective_spread(self.stock_df)
        self.assertGreaterEqual(roll, 0.0)

        trades = [
            {"price": 100.0, "volume": 500, "side": "buy"},
            {"price": 100.5, "volume": 500, "side": "buy"},
            {"price": 99.5, "volume": 200, "side": "sell"},
        ]
        vpin = calculate_vpin(trades, num_buckets=2, bucket_size=500)
        self.assertGreaterEqual(vpin, 0.0)
        self.assertLessEqual(vpin, 1.0)

        sq_score, sq_level = compute_squeeze_risk_index(
            short_pct_float=28.0, days_to_cover=6.5, cost_to_borrow_pct=15.0, is_negative_gex=True
        )
        self.assertGreaterEqual(sq_score, 70.0)
        self.assertIn(sq_level, ["HIGH", "EXTREME"])

    def test_macro_dual_betas(self):
        """Verify dual bull/bear beta and capture ratios."""
        s_ret = self.stock_df["close"].pct_change().dropna()
        b_ret = self.bench_df["close"].pct_change().dropna()

        betas = calculate_regime_conditional_betas(s_ret, b_ret)
        self.assertIn("overall", betas)
        self.assertIn("bull_beta", betas)
        self.assertIn("bear_beta", betas)

        up_cap, down_cap = calculate_capture_ratios(s_ret, b_ret)
        self.assertIsInstance(up_cap, float)
        self.assertIsInstance(down_cap, float)

    def test_higher_order_greeks_and_vrp(self):
        """Verify Vanna, Charm, Vomma, and VRP calculation."""
        greeks = compute_higher_order_greeks(
            spot=100.0,
            strike=100.0,
            time_to_exp=30.0 / 365.0,
            volatility=0.25,
            risk_free_rate=0.05,
            is_call=True,
        )
        self.assertAlmostEqual(greeks.delta, 0.5, delta=0.1)
        self.assertGreater(greeks.gamma, 0.0)
        self.assertIsInstance(greeks.vanna, float)
        self.assertIsInstance(greeks.charm, float)
        self.assertIsInstance(greeks.vomma, float)

        vrp = calculate_variance_risk_premium(atm_implied_vol=0.32, realized_vol_30d=0.20)
        self.assertEqual(vrp["vrp_spread_pct"], 12.0)
        self.assertIn("RICH_VOLATILITY", vrp["regime"])

    def test_walk_forward_backtest_engine(self):
        """Verify backtesting execution and performance attribution."""
        summary, df = run_signals_backtest(self.stock_df)
        self.assertIsNotNone(summary)
        self.assertIsInstance(summary.total_return_pct, float)
        self.assertIsInstance(summary.annualized_sharpe, float)
        self.assertIsInstance(summary.max_drawdown_pct, float)
        self.assertFalse(df.empty)

    def test_institutional_template_rendering(self):
        """Verify templates/_institutional.html and live.html render cleanly with Jinja."""
        from app import app
        from flask import render_template

        with app.test_request_context('/analytics?ticker=AAPL'):
            mock_payload = {
                'ticker': 'AAPL',
                'current_price': 185.50,
                'stats': {
                    'annualised_vol': 24.5,
                    'max_drawdown': -18.2,
                    'skewness': -0.15,
                    'kurtosis': 1.42,
                    'beta': 1.15,
                    'var_95': -2.1,
                    'var_99': -3.4,
                    'mean_daily_return': 0.08,
                    'daily_std': 1.54,
                    'data_points': 504,
                    'poc_price': 180.00,
                },
                'fundamentals': {'Name': 'Apple Inc.', 'Sector': 'Technology'},
                'charts': {},
                'institutional': {
                    'microstructure': {
                        'vpin_toxicity': 0.28,
                        'toxicity_regime': 'MODERATE',
                        'corwin_schultz_spread_pct': 0.0012,
                        'roll_effective_spread_pct': 0.0009,
                        'squeeze_risk_score': 32.5,
                        'squeeze_risk_level': 'LOW',
                        'amihud_illiquidity': 0.000012,
                    },
                    'macro_conditioning': {
                        'regime': 'TIGHTENING_INVERTED',
                        'fed_funds_rate': 5.33,
                        'yield_curve_2s10s': -0.35,
                        'is_inverted': True,
                        'cpi_yoy': 3.1,
                        'betas': {'bull_beta': 1.22, 'bear_beta': 0.88, 'unconditional_beta': 1.05},
                        'upside_capture': 114.5,
                        'downside_capture': 86.2,
                    },
                    'higher_order_greeks': {
                        'delta': 0.52,
                        'gamma': 0.024,
                        'vega': 0.18,
                        'theta': -0.065,
                        'vanna': 0.0035,
                        'charm': -0.0012,
                        'vomma': 0.00045,
                    },
                    'vrp': {
                        'atm_implied_vol': 26.5,
                        'realized_vol_30d': 21.2,
                        'vrp_spread_pct': 5.3,
                        'vrp_ratio': 1.25,
                        'regime': 'NORMAL_PREMIUM (Moderate Variance Spread)',
                    },
                    'sec_8k_events': [
                        {
                            'filing_date': '2026-08-10',
                            'items': ['1.01', '8.01'],
                            'item_descriptions': ['Material Definitive Agreement', 'Other Events'],
                            'description': 'Apple strategic supplier agreement',
                            'is_high_impact': True,
                            'primary_doc_url': 'https://www.sec.gov/edgar/data/320193/000032019326000010/aapl-20260810.htm',
                        }
                    ],
                }
            }

            rendered_html = render_template('analytics.html', data=mock_payload, ticker='AAPL')
            self.assertIn('Institutional Quantitative Analytics', rendered_html)
            self.assertIn('VPIN Toxicity', rendered_html)
            self.assertIn('Bull Beta (Up Market)', rendered_html)
            self.assertIn('Vanna', rendered_html)
            self.assertIn('SEC Form 8-K Material Events', rendered_html)

            live_html = render_template('live.html', ticker='AAPL')
            self.assertIn("switchGreeksChart('vanna')", live_html)
            self.assertIn("switchGreeksChart('charm')", live_html)
            self.assertIn("switchGreeksChart('vomma')", live_html)


if __name__ == "__main__":
    unittest.main()

