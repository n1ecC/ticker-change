"""SEC EDGAR Form 8-K Material Event Ingestion & Classification.

Parses unstructured & structured 8-K filings from SEC EDGAR:
- Item 1.01: Entry into Material Definitive Agreement
- Item 2.01: Completion of Acquisition or Disposition of Assets (M&A)
- Item 4.02: Non-Reliance on Previously Issued Financials (Restatements)
- Item 5.02: Departure / Election of Directors or Principal Officers
- Item 7.01 / 8.01: Regulation FD Disclosure & Other Events
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import providers
import db


ITEM_DEFINITIONS = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of Material Definitive Agreement",
    "2.01": "Completion of Acquisition/Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition (Earnings)",
    "2.03": "Creation of Direct Financial Obligation",
    "3.01": "Notice of Delisting / Transfer of Listing",
    "3.02": "Unregistered Sales of Equity Securities",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements (Restatement)",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Principal Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}


@dataclass
class MaterialEvent8K:
    accession_number: str
    cik: str
    ticker: str
    filing_date: str  # YYYY-MM-DD
    report_date: str  # YYYY-MM-DD (Date of period/event)
    items: List[str]  # e.g., ["1.01", "5.02"]
    item_descriptions: List[str]
    primary_doc_url: str
    description: str = ""
    is_high_impact: bool = False


def fetch_and_parse_8k_filings(ticker: str, limit: int = 20) -> List[MaterialEvent8K]:
    """Fetch recent Form 8-K submissions from SEC EDGAR for a given ticker."""
    cik_str = providers.sec_cik_for_ticker(ticker)
    if not cik_str:
        return []

    cache_key = f"sec_8k_{ticker.upper()}"
    cached = db.cache_get("sec_edgar", cache_key, ttl_hours=6.0)
    if cached is not None:
        return [MaterialEvent8K(**item) for item in cached]

    try:
        cik_int = int(cik_str)
    except ValueError:
        return []

    url = providers.SEC_SUBMISSIONS_URL.format(cik=cik_int)
    headers = {"User-Agent": providers.sec_user_agent(), "Accept-Encoding": "gzip, deflate"}
    res = providers._sec_get(url, headers=headers)
    if not res or "filings" not in res:
        return []

    recent = res.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    items_list = recent.get("items", [])
    descriptions = recent.get("primaryDocDescription", [])

    parsed_events: List[MaterialEvent8K] = []

    for i in range(min(len(forms), 500)):
        form_name = forms[i]
        if form_name in ("8-K", "8-K/A"):
            raw_items = items_list[i] if i < len(items_list) else ""
            acc_num = accessions[i].replace("-", "") if i < len(accessions) else ""
            prim_doc = primary_docs[i] if i < len(primary_docs) else ""
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_num}/{prim_doc}" if acc_num and prim_doc else ""

            # Extract distinct items, e.g. "1.01,5.02" or semicolon separated
            found_items = []
            if raw_items:
                tokens = re.split(r"[,;\s]+", str(raw_items))
                for tok in tokens:
                    tok = tok.strip()
                    if tok in ITEM_DEFINITIONS and tok not in found_items:
                        found_items.append(tok)

            item_descs = [f"Item {it}: {ITEM_DEFINITIONS.get(it, 'Special Event')}" for it in found_items]

            # High impact categories: 4.02 (restatements), 4.01 (auditor switch), 5.01 (control change), 2.01 (M&A)
            high_impact = any(it in ("4.01", "4.02", "5.01", "2.01", "3.01") for it in found_items)

            event = MaterialEvent8K(
                accession_number=accessions[i] if i < len(accessions) else "",
                cik=cik_str,
                ticker=ticker.upper(),
                filing_date=filing_dates[i] if i < len(filing_dates) else "",
                report_date=report_dates[i] if i < len(report_dates) and report_dates[i] else filing_dates[i],
                items=found_items,
                item_descriptions=item_descs,
                primary_doc_url=doc_url,
                description=descriptions[i] if i < len(descriptions) else "Form 8-K Current Report",
                is_high_impact=high_impact,
            )
            parsed_events.append(event)
            if len(parsed_events) >= limit:
                break

    # Cache formatted records
    db.cache_set("sec_edgar", cache_key, [e.__dict__ for e in parsed_events])
    return parsed_events
