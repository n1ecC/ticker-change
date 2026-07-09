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
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import db

# Shared session: reuses TCP connections (faster) and retries automatically on
# transient failures (429 rate-limits and 5xx), honouring Retry-After headers.
_SESSION = requests.Session()
_RETRY = Retry(
    total=3,
    backoff_factor=0.6,                       # 0.6s, 1.2s, 2.4s between retries
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
    respect_retry_after_header=True,
    raise_on_status=False,
)
_ADAPTER = HTTPAdapter(max_retries=_RETRY, pool_connections=10, pool_maxsize=10)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)

# No built-in dev key — set FINNHUB_API_KEY in the environment (or via the
# /settings page). When its free-tier quota is exhausted, calls automatically
# roll over to any user-supplied keys saved there (see _finnhub_get / _fmp_get).
_DEV_FINNHUB_KEY = ""
_DEFAULT_SEC_UA = "anonymous@example.com"

FINNHUB_BASE = "https://finnhub.io/api/v1"
FMP_BASE = "https://financialmodelingprep.com/api/v3"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

CACHE_TTL_HOURS = 24
HTTP_TIMEOUT = 12

# HTTP statuses that mean "this key is exhausted/invalid" — roll over to the next.
_ROLLOVER_STATUSES = (401, 402, 403, 429)


def _split_keys(raw: str) -> list:
    """Parse a comma/newline/whitespace-separated key blob into a clean list."""
    return [k.strip() for k in re.split(r"[\s,]+", raw or "") if k.strip()]


def _ordered_keys(env_var: str, setting_key: str, dev_default: str = "") -> list:
    """Build the ordered, de-duplicated key list for a provider.

    Order = env var (or built-in dev default) first, then any user keys saved on
    the settings page. Trying the shared/default key first preserves the existing
    behaviour; the user keys act as quota fallbacks behind it.
    """
    primary = os.environ.get(env_var, dev_default).strip()
    user = _split_keys(db.get_setting(setting_key))
    ordered = ([primary] if primary else []) + user
    seen, out = set(), []
    for k in ordered:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def finnhub_keys() -> list:
    """Ordered Finnhub keys: env/dev default first, then user fallbacks."""
    return _ordered_keys("FINNHUB_API_KEY", "finnhub_api_key", _DEV_FINNHUB_KEY)


def fmp_keys() -> list:
    """Ordered FMP keys: env first, then user fallbacks."""
    return _ordered_keys("FMP_API_KEY", "fmp_api_key")


def active_finnhub_key() -> str:
    """The first Finnhub key — used by the browser for the live WebSocket feed."""
    keys = finnhub_keys()
    return keys[0] if keys else ""


def sec_user_agent() -> str:
    """SEC contact UA: user setting overrides env, which overrides the default."""
    setting = (db.get_setting("sec_user_agent") or "").strip()
    env = (os.environ.get("SEC_USER_AGENT") or "").strip()
    return setting or env or _DEFAULT_SEC_UA


# --------------------------------------------------------------------------- #
# AI / LLM provider keys (power the AI analyst report — see ai.py)             #
# --------------------------------------------------------------------------- #

# label, env var, settings key, OpenAI-compatible base URL (None = native SDK),
# default model. OpenRouter / OpenAI / Gemini all expose an OpenAI-style
# /chat/completions endpoint, so they share one client path in ai.py; Anthropic
# uses its own SDK. Order here is also the fallback order for the report.
AI_PROVIDERS = (
    {"id": "anthropic",  "label": "Anthropic (Claude)", "env": "ANTHROPIC_API_KEY",
     "setting": "anthropic_api_key", "base_url": None,
     "model": "claude-opus-4-8"},
    {"id": "openai",     "label": "OpenAI",             "env": "OPENAI_API_KEY",
     "setting": "openai_api_key", "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o"},
    {"id": "gemini",     "label": "Google Gemini",      "env": "GEMINI_API_KEY",
     "setting": "gemini_api_key",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-3.1-flash-lite"},
    {"id": "openrouter", "label": "OpenRouter",         "env": "OPENROUTER_API_KEY",
     "setting": "openrouter_api_key", "base_url": "https://openrouter.ai/api/v1",
     "model": "anthropic/claude-opus-4-8"},
)

AI_SETTING_KEYS = tuple(p["setting"] for p in AI_PROVIDERS)


def ai_key(setting_key: str, env_var: str) -> str:
    """Resolve one AI provider key: user setting overrides env."""
    return (db.get_setting(setting_key) or os.environ.get(env_var, "")).strip()


def ai_providers() -> list:
    """Configured AI providers, in fallback order, each with its resolved key."""
    out = []
    for p in AI_PROVIDERS:
        key = ai_key(p["setting"], p["env"])
        if key:
            out.append({**p, "key": key})
    return out


def configured() -> dict:
    """Which providers have usable credentials, and how many keys back each."""
    finnhub, fmp = finnhub_keys(), fmp_keys()
    ai = {p["id"] for p in ai_providers()}
    return {
        "finnhub": bool(finnhub),
        "fmp": bool(fmp),
        "sec": True,  # keyless
        "finnhub_key_count": len(finnhub),
        "fmp_key_count": len(fmp),
        "ai": {p["id"]: (p["id"] in ai) for p in AI_PROVIDERS},
        "ai_count": len(ai),
    }


def _get_json(url, headers=None, params=None):
    """GET returning parsed JSON, or None on any failure."""
    try:
        resp = _SESSION.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
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


def _finnhub_get(path: str, params: dict):
    """GET a Finnhub endpoint, rolling over to the next key on quota/auth errors.

    Returns parsed JSON from the first key that succeeds, or None if every key is
    exhausted/failing. This is what lets a user-supplied key take over once the
    primary key hits its 60 req/min cap.
    """
    keys = finnhub_keys()
    if not keys:
        return None
    last = len(keys) - 1
    for i, key in enumerate(keys):
        try:
            resp = _SESSION.get(
                f"{FINNHUB_BASE}{path}",
                headers={"X-Finnhub-Token": key},
                params=params,
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as e:
            print(f"[providers] finnhub {path} (key {i + 1}/{len(keys)}) failed: {e}")
            continue
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return None
        if resp.status_code in _ROLLOVER_STATUSES and i < last:
            print(f"[providers] finnhub key {i + 1}/{len(keys)} -> HTTP "
                  f"{resp.status_code}, rolling over to next key")
            continue
        print(f"[providers] finnhub {path} -> HTTP {resp.status_code}")
        return None
    return None


def _fmp_get(path: str):
    """GET an FMP endpoint, rolling over to the next key on quota/auth errors.

    FMP signals an exhausted free tier both via 401/403/429 and via a 200 body of
    {"Error Message": ...}; both trigger rollover to the next key.
    """
    keys = fmp_keys()
    if not keys:
        return None
    last = len(keys) - 1
    for i, key in enumerate(keys):
        try:
            resp = _SESSION.get(
                f"{FMP_BASE}{path}", params={"apikey": key}, timeout=HTTP_TIMEOUT
            )
        except requests.RequestException as e:
            print(f"[providers] fmp {path} (key {i + 1}/{len(keys)}) failed: {e}")
            continue
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return None
            if isinstance(data, dict) and "Error Message" in data:
                if i < last:
                    print(f"[providers] fmp key {i + 1}/{len(keys)} quota error, "
                          f"rolling over to next key")
                    continue
                return None
            return data
        if resp.status_code in _ROLLOVER_STATUSES and i < last:
            print(f"[providers] fmp key {i + 1}/{len(keys)} -> HTTP "
                  f"{resp.status_code}, rolling over to next key")
            continue
        print(f"[providers] fmp {path} -> HTTP {resp.status_code}")
        return None
    return None


# --------------------------------------------------------------------------- #
# Finnhub                                                                      #
# --------------------------------------------------------------------------- #

def finnhub_metrics(symbol: str):
    """Valuation & profitability metrics. Returns dict of label -> value or None."""
    if not finnhub_keys():
        return None

    def fetch():
        return _finnhub_get(
            "/stock/metric",
            {"symbol": symbol.upper(), "metric": "all"},
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
    if not finnhub_keys():
        return None

    def fetch():
        return _finnhub_get(
            "/stock/insider-sentiment",
            {"symbol": symbol.upper(), "from": "2022-01-01", "to": "2030-01-01"},
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
    if not finnhub_keys():
        return None

    def fetch():
        return _finnhub_get(
            "/stock/insider-transactions",
            {"symbol": symbol.upper()},
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
    if not finnhub_keys():
        return None

    def fetch():
        return _finnhub_get(
            "/stock/price-target",
            {"symbol": symbol.upper()},
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
    if not finnhub_keys():
        return None

    def fetch():
        return _finnhub_get(
            "/stock/recommendation",
            {"symbol": symbol.upper()},
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
    if not fmp_keys():
        return None

    def fetch():
        return _fmp_get(f"/institutional-holder/{symbol.upper()}")

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
    return {"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip, deflate"}


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

    wanted = set(forms) if forms is not None else None
    rows = []
    for i, form in enumerate(form_list):
        if wanted is not None and form not in wanted:
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
