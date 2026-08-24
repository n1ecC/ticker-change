"""Bi-Temporal Entity & Corporate Action Engine (Graph-Driven Architecture).

Maintains exact point-in-time ticker-to-entity mappings (CIK, FIGI, CUSIP),
normalizes and composites corporate actions (splits, mergers, spinoffs, ticker changes),
and provides deterministic historical replay and cost-basis adjustment logic.
"""
from __future__ import annotations

import enum
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import db

# Sentinel infinity date for active records
INF_DATE = "9999-12-31"


class ActionType(str, enum.Enum):
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    FORWARD_SPLIT = "FORWARD_SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    SPINOFF = "SPINOFF"
    MERGER = "MERGER"
    BANKRUPTCY = "BANKRUPTCY"
    NAME_CHANGE = "NAME_CHANGE"


@dataclass
class CorporateEntity:
    entity_id: str  # Immutable UUID or synthetic ID
    cik: Optional[str] = None
    figi: Optional[str] = None
    cusip: Optional[str] = None
    legal_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class IdentifierTimeline:
    """Bi-temporal identifier mapping.

    valid_from / valid_to: Business time (effective dates in the market).
    tx_from / tx_to: Transaction/system time (when the record was known/recorded).
    """
    entity_id: str
    symbol: str
    valid_from: str  # YYYY-MM-DD
    valid_to: str = INF_DATE  # YYYY-MM-DD
    tx_from: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tx_to: str = INF_DATE
    is_primary: bool = True
    source_feed: str = "SYSTEM"


@dataclass
class CorporateAction:
    """Standardized Corporate Action Event with operational ordering."""
    action_id: str
    entity_id: str
    action_type: ActionType
    effective_date: str  # YYYY-MM-DD
    announcement_date: Optional[str] = None  # YYYY-MM-DD
    ratio: Optional[float] = None  # E.g. 4.0 for 4:1 split, 0.1 for 1:10 reverse
    old_value: Optional[str] = None  # Old symbol, old name, etc.
    new_value: Optional[str] = None  # New symbol, new name, etc.
    target_entity_id: Optional[str] = None  # For mergers / spinoffs
    cash_amount: Optional[float] = None
    raw_notice_hash: str = ""
    status: str = "CONFIRMED"  # PENDING, CONFIRMED, CANCELLED
    metadata: Dict[str, Any] = field(default_factory=dict)
    tx_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def operational_rank(self) -> int:
        """Execution priority when multiple actions occur on the same effective date.

        1. Bankruptcies / Delistings
        2. Splits (Forward / Reverse)
        3. Spinoffs / Mergers
        4. Symbol / Name Changes
        """
        ordering = {
            ActionType.BANKRUPTCY: 10,
            ActionType.FORWARD_SPLIT: 20,
            ActionType.REVERSE_SPLIT: 20,
            ActionType.SPINOFF: 30,
            ActionType.MERGER: 40,
            ActionType.SYMBOL_CHANGE: 50,
            ActionType.NAME_CHANGE: 60,
        }
        return ordering.get(self.action_type, 99)


def init_corporate_action_schema():
    """Idempotently initialize bi-temporal schema in SQLite (stocks.db)."""
    with db.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS corporate_entities (
                entity_id  TEXT PRIMARY KEY,
                cik        TEXT,
                figi       TEXT,
                cusip      TEXT,
                legal_name TEXT NOT NULL,
                created_at DATETIME NOT NULL
            );

            CREATE TABLE IF NOT EXISTS identifier_timeline (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id   TEXT     NOT NULL,
                symbol      TEXT     NOT NULL,
                valid_from  DATE     NOT NULL,
                valid_to    DATE     NOT NULL,
                tx_from     DATETIME NOT NULL,
                tx_to       DATETIME NOT NULL,
                is_primary  BOOLEAN  NOT NULL DEFAULT 1,
                source_feed TEXT     NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES corporate_entities(entity_id)
            );

            CREATE INDEX IF NOT EXISTS idx_timeline_lookup
            ON identifier_timeline (symbol, valid_from, valid_to, tx_from, tx_to);

            CREATE TABLE IF NOT EXISTS corporate_actions (
                action_id          TEXT PRIMARY KEY,
                entity_id          TEXT NOT NULL,
                action_type        TEXT NOT NULL,
                effective_date     DATE NOT NULL,
                announcement_date  DATE,
                ratio              REAL,
                old_value          TEXT,
                new_value          TEXT,
                target_entity_id   TEXT,
                cash_amount        REAL,
                raw_notice_hash    TEXT UNIQUE,
                status             TEXT NOT NULL DEFAULT 'CONFIRMED',
                metadata_json      TEXT,
                tx_time            DATETIME NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES corporate_entities(entity_id)
            );

            CREATE INDEX IF NOT EXISTS idx_ca_entity_effective
            ON corporate_actions (entity_id, effective_date);
        """)


class CorporateActionEngine:
    """Stateful Engine managing temporal identifier resolution and action processing."""

    def __init__(self):
        init_corporate_action_schema()

    def register_entity(self, entity: CorporateEntity) -> str:
        """Upsert a corporate entity maintaining canonical identifiers."""
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO corporate_entities (entity_id, cik, figi, cusip, legal_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    cik = COALESCE(excluded.cik, corporate_entities.cik),
                    figi = COALESCE(excluded.figi, corporate_entities.figi),
                    cusip = COALESCE(excluded.cusip, corporate_entities.cusip),
                    legal_name = excluded.legal_name
                """,
                (entity.entity_id, entity.cik, entity.figi, entity.cusip, entity.legal_name, entity.created_at),
            )
        return entity.entity_id

    def add_identifier_mapping(self, timeline: IdentifierTimeline) -> None:
        """Add bi-temporal symbol-to-entity mapping, handling historical interval closes."""
        with db.get_conn() as conn:
            # Check if there is an open-ended interval for this symbol that needs closing
            cur = conn.execute(
                """
                SELECT id, valid_from, valid_to FROM identifier_timeline
                WHERE symbol = ? AND entity_id = ? AND valid_to = ?
                """,
                (timeline.symbol.upper(), timeline.entity_id, INF_DATE),
            )
            existing = cur.fetchone()
            if existing and existing["valid_from"] < timeline.valid_from:
                conn.execute(
                    "UPDATE identifier_timeline SET valid_to = ? WHERE id = ?",
                    (timeline.valid_from, existing["id"]),
                )

            conn.execute(
                """
                INSERT INTO identifier_timeline
                (entity_id, symbol, valid_from, valid_to, tx_from, tx_to, is_primary, source_feed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timeline.entity_id,
                    timeline.symbol.upper(),
                    timeline.valid_from,
                    timeline.valid_to,
                    timeline.tx_from,
                    timeline.tx_to,
                    1 if timeline.is_primary else 0,
                    timeline.source_feed,
                ),
            )

    def resolve_entity_as_of(
        self,
        symbol: str,
        as_of_date: Optional[str] = None,
        as_of_tx_time: Optional[str] = None,
    ) -> Optional[CorporateEntity]:
        """Deterministic Point-in-Time entity resolution.

        Answers: 'Which corporate entity held symbol S on date D according to knowledge known at T?'
        """
        symbol = symbol.upper()
        d_val = as_of_date or date.today().isoformat()
        tx_val = as_of_tx_time or datetime.now(timezone.utc).isoformat()

        with db.get_conn() as conn:
            row = conn.execute(
                """
                SELECT e.entity_id, e.cik, e.figi, e.cusip, e.legal_name, e.created_at
                FROM identifier_timeline t
                JOIN corporate_entities e ON t.entity_id = e.entity_id
                WHERE t.symbol = ?
                  AND t.valid_from <= ? AND t.valid_to >= ?
                  AND t.tx_from <= ? AND t.tx_to >= ?
                ORDER BY t.is_primary DESC, t.valid_from DESC
                LIMIT 1
                """,
                (symbol, d_val, d_val, tx_val, tx_val),
            ).fetchone()

            if row:
                return CorporateEntity(
                    entity_id=row["entity_id"],
                    cik=row["cik"],
                    figi=row["figi"],
                    cusip=row["cusip"],
                    legal_name=row["legal_name"],
                    created_at=row["created_at"],
                )
        return None

    def get_symbol_history(self, entity_id: str) -> List[Dict[str, Any]]:
        """Return the complete chronological symbol timeline for an entity."""
        with db.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT symbol, valid_from, valid_to, is_primary, source_feed
                FROM identifier_timeline
                WHERE entity_id = ?
                ORDER BY valid_from ASC
                """,
                (entity_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_corporate_action(self, action: CorporateAction) -> bool:
        """Idempotently record and apply a corporate action."""
        if not action.raw_notice_hash:
            # Generate deterministic hash to guarantee idempotency across multiple feed polls
            content = f"{action.entity_id}|{action.action_type.value}|{action.effective_date}|{action.ratio}|{action.old_value}|{action.new_value}"
            action.raw_notice_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        with db.get_conn() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO corporate_actions (
                        action_id, entity_id, action_type, effective_date, announcement_date,
                        ratio, old_value, new_value, target_entity_id, cash_amount,
                        raw_notice_hash, status, metadata_json, tx_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action.action_id,
                        action.entity_id,
                        action.action_type.value,
                        action.effective_date,
                        action.announcement_date,
                        action.ratio,
                        action.old_value,
                        action.new_value,
                        action.target_entity_id,
                        action.cash_amount,
                        action.raw_notice_hash,
                        action.status,
                        json.dumps(action.metadata),
                        action.tx_time,
                    ),
                )
            except sqlite3.IntegrityError:
                # Idempotent skip: Duplicate raw notice detected
                return False

        # Apply state transitions for structural actions
        if action.action_type == ActionType.SYMBOL_CHANGE and action.old_value and action.new_value:
            # Close old symbol interval and open new symbol interval
            with db.get_conn() as conn:
                conn.execute(
                    """
                    UPDATE identifier_timeline
                    SET valid_to = ?
                    WHERE entity_id = ? AND symbol = ? AND valid_to = ?
                    """,
                    (action.effective_date, action.entity_id, action.old_value.upper(), INF_DATE),
                )
            self.add_identifier_mapping(
                IdentifierTimeline(
                    entity_id=action.entity_id,
                    symbol=action.new_value.upper(),
                    valid_from=action.effective_date,
                    valid_to=INF_DATE,
                    source_feed="CORPORATE_ACTION",
                )
            )

        return True

    def get_corporate_actions_for_entity(self, entity_id: str) -> List[CorporateAction]:
        """Fetch all corporate actions ordered by operational execution precedence."""
        with db.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM corporate_actions
                WHERE entity_id = ?
                ORDER BY effective_date ASC
                """,
                (entity_id,),
            ).fetchall()

            actions = []
            for r in rows:
                actions.append(
                    CorporateAction(
                        action_id=r["action_id"],
                        entity_id=r["entity_id"],
                        action_type=ActionType(r["action_type"]),
                        effective_date=r["effective_date"],
                        announcement_date=r["announcement_date"],
                        ratio=r["ratio"],
                        old_value=r["old_value"],
                        new_value=r["new_value"],
                        target_entity_id=r["target_entity_id"],
                        cash_amount=r["cash_amount"],
                        raw_notice_hash=r["raw_notice_hash"],
                        status=r["status"],
                        metadata=json.loads(r["metadata_json"] or "{}"),
                        tx_time=r["tx_time"],
                    )
                )

            # Sort stably by effective date, then by operational execution rank
            actions.sort(key=lambda a: (a.effective_date, a.operational_rank))
            return actions

    @staticmethod
    def adjust_historical_series_for_splits(
        prices: List[Dict[str, Any]], actions: List[CorporateAction]
    ) -> List[Dict[str, Any]]:
        """Apply split factors in reverse-chronological order to maintain cost-basis invariance.

        Guarantees:
          market_value_before == market_value_after (conservation of capital)
        """
        if not prices or not actions:
            return prices

        # Filter active split actions
        split_actions = [
            a for a in actions
            if a.action_type in (ActionType.FORWARD_SPLIT, ActionType.REVERSE_SPLIT)
            and a.ratio and a.ratio > 0
        ]
        if not split_actions:
            return prices

        adjusted = [p.copy() for p in prices]
        for act in sorted(split_actions, key=lambda a: a.effective_date, reverse=True):
            eff_d = act.effective_date
            ratio = act.ratio
            for item in adjusted:
                if item.get("date") < eff_d:
                    if "close" in item:
                        item["close"] = item["close"] / ratio
                    if "open" in item:
                        item["open"] = item["open"] / ratio
                    if "high" in item:
                        item["high"] = item["high"] / ratio
                    if "low" in item:
                        item["low"] = item["low"] / ratio
                    if "volume" in item:
                        item["volume"] = int(item["volume"] * ratio)
        return adjusted


# Module-level singleton
engine = CorporateActionEngine()
