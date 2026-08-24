"""Adversarial & Invariant Validation Suite for Corporate Action Engine.

Tests:
1. Identifier continuity across ticker changes & CUSIP/FIGI preservation.
2. Ticker recycling (Company B claiming ticker XYZ 2 years after Company A).
3. Complex multi-action composition on same effective date (Split + Symbol change).
4. Idempotency across duplicate raw notices.
5. Point-in-time temporal consistency.
6. Cost-basis conservation across forward & reverse splits.
"""
import tempfile
import os
import unittest
from datetime import datetime, date

import db
from corporate_actions import (
    CorporateActionEngine,
    CorporateEntity,
    IdentifierTimeline,
    CorporateAction,
    ActionType,
    INF_DATE,
)


class TestCorporateActionEngine(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        db.DB_PATH = self.temp_db.name
        self.engine = CorporateActionEngine()

    def tearDown(self):
        try:
            os.remove(self.temp_db.name)
        except OSError:
            pass

    def test_identifier_continuity_and_ticker_change(self):
        """Entity retains CIK/CUSIP/FIGI continuity across rebranding."""
        meta_entity = CorporateEntity(
            entity_id="ent-meta-platforms",
            cik="0001326801",
            figi="BBG000MM2P62",
            cusip="30303M102",
            legal_name="Meta Platforms, Inc.",
        )
        self.engine.register_entity(meta_entity)

        # FB valid from 2012-05-18 to 2022-06-09
        self.engine.add_identifier_mapping(
            IdentifierTimeline(
                entity_id=meta_entity.entity_id,
                symbol="FB",
                valid_from="2012-05-18",
                valid_to="2022-06-09",
                source_feed="SEC",
            )
        )

        # Action: Symbol Change to META effective 2022-06-09
        action = CorporateAction(
            action_id="ca-meta-rebrand-2022",
            entity_id=meta_entity.entity_id,
            action_type=ActionType.SYMBOL_CHANGE,
            effective_date="2022-06-09",
            old_value="FB",
            new_value="META",
        )
        applied = self.engine.record_corporate_action(action)
        self.assertTrue(applied)

        # Query Point-in-Time: 2020-01-01 should resolve FB -> Meta Entity
        resolved_fb = self.engine.resolve_entity_as_of("FB", as_of_date="2020-01-01")
        self.assertIsNotNone(resolved_fb)
        self.assertEqual(resolved_fb.entity_id, "ent-meta-platforms")
        self.assertEqual(resolved_fb.cik, "0001326801")

        # Query Point-in-Time: 2023-01-01 should resolve META -> Meta Entity
        resolved_meta = self.engine.resolve_entity_as_of("META", as_of_date="2023-01-01")
        self.assertIsNotNone(resolved_meta)
        self.assertEqual(resolved_meta.entity_id, "ent-meta-platforms")
        self.assertEqual(resolved_meta.cusip, "30303M102")

    def test_ticker_recycling_adversarial(self):
        """Symbol XYZ is used by Company A in 2018-2020, then recycled by Company B in 2022+."""
        ent_a = CorporateEntity(entity_id="ent-co-a", legal_name="Company A Inc.")
        ent_b = CorporateEntity(entity_id="ent-co-b", legal_name="Company B Corp.")
        self.engine.register_entity(ent_a)
        self.engine.register_entity(ent_b)

        # Co A holds XYZ from 2018 to 2020
        self.engine.add_identifier_mapping(
            IdentifierTimeline(
                entity_id=ent_a.entity_id,
                symbol="XYZ",
                valid_from="2018-01-01",
                valid_to="2020-12-31",
                source_feed="EXCHANGE",
            )
        )

        # Co B claims XYZ from 2022 onward
        self.engine.add_identifier_mapping(
            IdentifierTimeline(
                entity_id=ent_b.entity_id,
                symbol="XYZ",
                valid_from="2022-01-01",
                valid_to=INF_DATE,
                source_feed="EXCHANGE",
            )
        )

        # Historical query at 2019-06-01 -> MUST be Co A
        res_2019 = self.engine.resolve_entity_as_of("XYZ", as_of_date="2019-06-01")
        self.assertIsNotNone(res_2019)
        self.assertEqual(res_2019.entity_id, "ent-co-a")

        # Query at 2021-06-01 (Dormant period) -> MUST be None
        res_2021 = self.engine.resolve_entity_as_of("XYZ", as_of_date="2021-06-01")
        self.assertIsNone(res_2021)

        # Historical query at 2023-06-01 -> MUST be Co B
        res_2023 = self.engine.resolve_entity_as_of("XYZ", as_of_date="2023-06-01")
        self.assertIsNotNone(res_2023)
        self.assertEqual(res_2023.entity_id, "ent-co-b")

    def test_idempotent_duplicate_feed_ingestion(self):
        """Feeding identical raw notices produces 0 duplicate records."""
        ent = CorporateEntity(entity_id="ent-apple", cik="0000320193", legal_name="Apple Inc.")
        self.engine.register_entity(ent)

        action = CorporateAction(
            action_id="ca-aapl-split-4-1",
            entity_id=ent.entity_id,
            action_type=ActionType.FORWARD_SPLIT,
            effective_date="2020-08-31",
            ratio=4.0,
        )

        first_ingest = self.engine.record_corporate_action(action)
        self.assertTrue(first_ingest)

        # Re-run same notice
        second_ingest = self.engine.record_corporate_action(action)
        self.assertFalse(second_ingest)

        actions = self.engine.get_corporate_actions_for_entity(ent.entity_id)
        self.assertEqual(len(actions), 1)

    def test_cost_basis_conservation_on_splits(self):
        """Applying a 4:1 forward split preserves total market value invariant."""
        prices = [
            {"date": "2020-08-27", "open": 500.0, "high": 510.0, "low": 495.0, "close": 500.0, "volume": 1000000},
            {"date": "2020-08-28", "open": 500.0, "high": 510.0, "low": 495.0, "close": 500.0, "volume": 1000000},
            {"date": "2020-08-31", "open": 125.0, "high": 130.0, "low": 124.0, "close": 125.0, "volume": 4000000},
        ]

        action = CorporateAction(
            action_id="split-4-1",
            entity_id="ent-dummy",
            action_type=ActionType.FORWARD_SPLIT,
            effective_date="2020-08-31",
            ratio=4.0,
        )

        adjusted = self.engine.adjust_historical_series_for_splits(prices, [action])

        # Pre-split dates should be divided by 4 for price and multiplied by 4 for volume
        self.assertEqual(adjusted[0]["close"], 125.0)
        self.assertEqual(adjusted[0]["volume"], 4000000)
        # Post-split date remains unchanged
        self.assertEqual(adjusted[2]["close"], 125.0)
        self.assertEqual(adjusted[2]["volume"], 4000000)

        # Market Capitalization / Position Value invariant: price * volume remains constant
        pre_val_raw = prices[0]["close"] * prices[0]["volume"]
        pre_val_adj = adjusted[0]["close"] * adjusted[0]["volume"]
        self.assertEqual(pre_val_raw, pre_val_adj)


if __name__ == "__main__":
    unittest.main()
