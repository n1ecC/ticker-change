# Historical Change Stocks

Simple Flask app that compares a stock's current price to historical prices across multiple periods.

- Live demo: https://ticker-change.onrender.com

## Quick start (local)

1. Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the app (development):

```bash
python3 app.py
# then open http://127.0.0.1:5001
```

For a production-like run using Gunicorn:

```bash
gunicorn app:app --bind 0.0.0.0:5001 --workers 2
```

## Deploy notes (Render)

- Repo contains a `Dockerfile` and `Procfile` suitable for Render.
- Recommended Health Check path: `/health` (already implemented).
- Ensure `Auto-Deploy` is enabled for the connected branch or trigger a manual deploy from the Render dashboard.
- Use environment variables for secrets; Render provides a UI for those.

## Files of interest

- `app.py` — Flask application
- `requirements.txt` — Python dependencies
- `templates/` — Jinja2 templates for UI
- `Dockerfile`, `Procfile` — deployment helpers

## Notes

- The app uses `yfinance` to fetch historical prices — consider adding caching to avoid rate-limits.
- Do not commit secrets; store them as environment variables on your host.

## Market positioning data (free-tier APIs)

The `/positioning` page layers institutional, insider, and valuation data on top of
the price analytics. It is powered entirely by free-tier providers, cached in SQLite
(24h TTL) to stay within rate limits. Each panel degrades gracefully — the page
renders with whatever credentials are present.

| Env var | Provider | Free tier | Powers |
| --- | --- | --- | --- |
| _(none)_ | [SEC EDGAR](https://www.sec.gov/edgar) | Unlimited | Form 3/4/5 insider filings, 13F filings — **works out of the box** |
| `FINNHUB_API_KEY` | [Finnhub](https://finnhub.io) | 60 req/min | Valuation metrics, insider sentiment (MSPR), insider transactions, analyst recommendations |
| `FMP_API_KEY` | [Financial Modeling Prep](https://financialmodelingprep.com) | 250 req/day | Top institutional (13F) holders |
| `SEC_USER_AGENT` | — | — | Contact string SEC requires, e.g. `"Your Name you@email.com"` |

Set them before running, for example:

```bash
export FINNHUB_API_KEY="your_key"
export FMP_API_KEY="your_key"
export SEC_USER_AGENT="Your Name you@email.com"
python3 app.py
```

Without any keys, the SEC EDGAR panels still populate; the Finnhub/FMP panels show a
prompt explaining which key to set.
