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
