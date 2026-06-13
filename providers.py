"""Free-tier financial data providers.

Each provider is gated behind an env var (except SEC EDGAR, which is keyless)
and degrades gracefully — a missing key or a failed request returns None rather
than raising, so the dashboard always renders. All responses are cached in the
shared SQLite api_cache table to respect tight free-tier rate limits.

Env vars:
    FINNHUB_API_KEY   free key from https://finnhub.io       (60 req/min)
    FMP_API_KEY       free key from https://financialmodelingprep.com (250 req/day)
    SEC_USER_AGENT    "Your Name your@email.com" — SEC requires a contact UA
"""
import os
import requests

import db

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
FMP_KEY = os.environ.get("FMP_API_KEY", "").strip()
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "ticker-change-dashboard contact@example.com"
).strip()

FINNHUB_BASE = "https://finnhub.io/api/v1"
FMP_BASE = "https://financialmodelingprep.com/api/v3"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

CACHE_TTL_HOURS = 24
HTTP_TIMEOUT = 12


def configured() -> dict:
    """Which providers have usable credentials."""
    return {
        "finnhub": bool(FINNHUB_KEY),
        "fmp": bool(FMP_KEY),
        "sec": True,  # keyless
    }


def _get_json(url, headers=None, params=None):
    """GET returning parsed JSON, or None on any failure."""
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            print(f"[providers] {url} -> HTTP {resp.status_code}")
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[providers] {url} failed: {e}")
        return None


def _cached(provider, key, fetch_fn, ttl_hours=CACHE_TTL_HOURS):
    """Return cached payload or fetch, cache, and return it."""
    hit = db.cache_get(provider, key, ttl_hours)
    if hit is not None:
        return hit
    fresh = fetch_fn()
    if fresh is not None:
        db.cache_set(provider, key, fresh)
    return fresh


def _first(d: dict, *keys, default=None):
    """Return the first present, non-None value among candidate keys."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


# --------------------------------------------------------------------------- #
# Finnhub                                                                      #
# --------------------------------------------------------------------------- #

def finnhub_metrics(symbol: str):
    """Valuation & profitability metrics. Returns dict of label -> value or None."""
    if not FINNHUB_KEY:
        return None

    def fetch():
        return _get_json(
            f"{FINNHUB_BASE}/stock/metric",
            headers={"X-Finnhub-Token": FINNHUB_KEY},
            params={"symbol": symbol.upper(), "metric": "all"},
        )

    raw = _cached("finnhub", f"metric:{symbol.upper()}", fetch)
    if not raw or not isinstance(raw.get("metric"), dict):
        return None
    m = raw["metric"]

    # (label, [candidate keys], number of decimals)
    spec = [
        ("P/E (TTM)",        ["peTTM", "peNormalizedAnnual", "peBasicExclExtraTTM"], 2),
        ("P/B",              ["pbAnnual", "pbQuarterly"],                            2),
        ("P/S (TTM)",        ["psTTM", "psAnnual"],                                  2),
        ("EPS (TTM)",        ["epsTTM", "epsBasicExclExtraItemsTTM"],                2),
        ("ROE (TTM)",        ["roeTTM", "roeRfy"],                                   2),
        ("Net Margin (TTM)", ["netProfitMarginTTM", "netProfitMarginAnnual"],       2),
        ("Debt / Equity",    ["totalDebt/totalEquityQuarterly",
                              "totalDebt/totalEquityAnnual", "longTermDebt/equityQuarterly"], 2),
        ("Current Ratio",    ["currentRatioQuarterly", "currentRatioAnnual"],        2),
        ("Beta",             ["beta"],                                               2),
        ("52W High",         ["52WeekHigh"],                                         2),
        ("52W Low",          ["52WeekLow"],                                          2),
    ]
    out = []
    for label, keys, dp in spec:
        val = _first(m, *keys)
        if isinstance(val, (int, float)):
            out.append({"label": label, "value": round(val, dp)})
    return out or None


def finnhub_insider_sentiment(symbol: str):
    """Monthly insider sentiment (MSPR). Returns list of {year, month, mspr, change}."""
    if not FINNHUB_KEY:
        return None

    def fetch():
        return _get_json(
            f"{FINNHUB_BASE}/stock/insider-sentiment",
            headers={"X-Finnhub-Token": FINNHUB_KEY},
            params={"symbol": symbol.upper(), "from": "2022-01-01", "to": "2030-01-01"},
        )

    raw = _cached("finnhub", f"insider-sentiment:{symbol.upper()}", fetch)
    if not raw or not isinstance(raw.get("data"), list):
        return None
    rows = []
    for r in raw["data"]:
        rows.append({
            "year": r.get("year"),
            "month": r.get("month"),
            "mspr": r.get("mspr"),
            "change": r.get("change"),
        })
    return rows or None


def finnhub_insider_transactions(symbol: str, limit: int = 15):
    """Recent insider transactions. Returns list of normalized trade dicts."""
    if not FINNHUB_KEY:
        return None

    def fetch():
        return _get_json(
            f"{FINNHUB_BASE}/stock/insider-transactions",
            headers={"X-Finnhub-Token": FINNHUB_KEY},
            params={"symbol": symbol.upper()},
        )

    raw = _cached("finnhub", f"insider-tx:{symbol.upper()}", fetch)
    if not raw or not isinstance(raw.get("data"), list):
        return None
    rows = []
    for r in raw["data"][:limit]:
        change = r.get("change")
        rows.append({
            "name": r.get("name"),
            "shares": change,
            "is_buy": isinstance(change, (int, float)) and change > 0,
            "price": r.get("transactionPrice"),
            "code": r.get("transactionCode"),
            "filing_date": r.get("filingDate"),
            "transaction_date": r.get("transactionDate"),
        })
    return rows or None


def finnhub_price_target(symbol: str):
    """Consensus analyst price target (low / mean / median / high). Returns dict or None."""
    if not FINNHUB_KEY:
        return None

    def fetch():
        return _get_json(
            f"{FINNHUB_BASE}/stock/price-target",
            headers={"X-Finnhub-Token": FINNHUB_KEY},
            params={"symbol": symbol.upper()},
        )

    raw = _cached("finnhub", f"price-target:{symbol.upper()}", fetch)
    if not raw or not isinstance(raw.get("targetMean"), (int, float)):
        return None
    return {
        "low":    raw.get("targetLow"),
        "mean":   raw.get("targetMean"),
        "median": raw.get("targetMedian"),
        "high":   raw.get("targetHigh"),
        "updated": raw.get("lastUpdated"),
    }


def finnhub_recommendations(symbol: str):
    """Latest analyst recommendation breakdown. Returns dict or None."""
    if not FINNHUB_KEY:
        return None

    def fetch():
        return _get_json(
            f"{FINNHUB_BASE}/stock/recommendation",
            headers={"X-Finnhub-Token": FINNHUB_KEY},
            params={"symbol": symbol.upper()},
        )

    raw = _cached("finnhub", f"reco:{symbol.upper()}", fetch)
    if not isinstance(raw, list) or not raw:
        return None
    latest = raw[0]
    return {
        "period": latest.get("period"),
        "strong_buy": latest.get("strongBuy", 0),
        "buy": latest.get("buy", 0),
        "hold": latest.get("hold", 0),
        "sell": latest.get("sell", 0),
        "strong_sell": latest.get("strongSell", 0),
    }


# --------------------------------------------------------------------------- #
# Financial Modeling Prep                                                      #
# --------------------------------------------------------------------------- #

def fmp_institutional_holders(symbol: str, limit: int = 10):
    """Top institutional (13F) holders. Returns list of {holder, shares, change, date}."""
    if not FMP_KEY:
        return None

    def fetch():
        return _get_json(
            f"{FMP_BASE}/institutional-holder/{symbol.upper()}",
            params={"apikey": FMP_KEY},
        )

    raw = _cached("fmp", f"inst-holders:{symbol.upper()}", fetch)
    if not isinstance(raw, list) or not raw:
        return None
    rows = []
    for r in raw:
        shares = _first(r, "shares")
        if not isinstance(shares, (int, float)):
            continue
        rows.append({
            "holder": _first(r, "holder", "investorName", default="Unknown"),
            "shares": int(shares),
            "change": _first(r, "change", default=0),
            "date": _first(r, "dateReported", "date"),
        })
    rows.sort(key=lambda x: x["shares"], reverse=True)
    return rows[:limit] or None


# --------------------------------------------------------------------------- #
# SEC EDGAR (keyless)                                                          #
# --------------------------------------------------------------------------- #

def _sec_headers():
    return {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def sec_cik_for_ticker(symbol: str):
    """Resolve a ticker to its zero-padded CIK using SEC's master map."""
    def fetch():
        return _get_json(SEC_TICKERS_URL, headers=_sec_headers())

    # The map is identical for every ticker, so cache it under a single key.
    raw = _cached("sec", "ticker-map", fetch, ttl_hours=24 * 7)
    if not isinstance(raw, dict):
        return None
    target = symbol.upper()
    for entry in raw.values():
        if str(entry.get("ticker", "")).upper() == target:
            return int(entry["cik_str"])
    return None


def sec_recent_filings(symbol: str, forms=("3", "4", "5"), limit: int = 15):
    """Recent insider/ownership filings from SEC submissions. Returns list of dicts."""
    cik = sec_cik_for_ticker(symbol)
    if cik is None:
        return None

    def fetch():
        return _get_json(SEC_SUBMISSIONS_URL.format(cik=cik), headers=_sec_headers())

    raw = _cached("sec", f"submissions:{cik}", fetch, ttl_hours=12)
    if not raw:
        return None
    recent = raw.get("filings", {}).get("recent", {})
    form_list = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary = recent.get("primaryDocument", [])

    wanted = set(forms)
    rows = []
    for i, form in enumerate(form_list):
        if form not in wanted:
            continue
        accession = accessions[i] if i < len(accessions) else ""
        doc = primary[i] if i < len(primary) else ""
        accession_nodash = accession.replace("-", "")
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{doc}"
            if accession and doc else None
        )
        rows.append({
            "form": form,
            "date": dates[i] if i < len(dates) else None,
            "url": url,
        })
        if len(rows) >= limit:
            break
    return rows or None
