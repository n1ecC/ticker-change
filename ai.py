"""AI analyst report — a written take on a stock, via Claude.

Feeds the dashboard's already-computed numbers (price action, volatility, VaR,
Monte-Carlo odds, valuation multiples, dealer GEX positioning, and the ML
signal) to Claude with a seasoned-analyst system prompt, and returns a concise
HTML report for the analytics page. Results are cached in the shared SQLite
api_cache table so a page refresh doesn't re-bill the API.

Requires ANTHROPIC_API_KEY. Degrades gracefully — a missing key or a failed call
returns None and the report section simply doesn't render.
"""
from __future__ import annotations

import json
import os

import db

MODEL = "claude-opus-4-8"
CACHE_TTL_HOURS = 12

SYSTEM = """You are a seasoned sell-side equity research analyst writing a concise \
desk note for an experienced investor. You are given a structured snapshot of one \
stock — price action, volatility and tail-risk statistics, a Monte-Carlo path \
forecast, valuation multiples, dealer gamma-exposure (GEX) positioning, and a \
machine-learning signal. Write a balanced, professional briefing.

Rules:
- Ground every claim in the numbers provided. Do not invent figures, news, \
earnings results, or price targets that aren't in the data. If something isn't \
given, say it's not available rather than guessing.
- Be even-handed: state the bull case and the bear case. Never cheerlead.
- Treat the GEX profile and ML signal as what they are — a delayed end-of-day, \
model-based read, directional context rather than ground truth. Note their limits.
- Calibrate confidence to the data quality; flag where the inputs are thin.
- Output GitHub-flavoured Markdown with these sections, in order, using `###` \
headings: Snapshot, Valuation & Fundamentals, Technical & Volatility Picture, \
Options & Dealer Positioning, Key Risks & Catalysts, Bottom Line. Keep it tight \
(~400-600 words). Use short paragraphs and bullets where they help.
- End with one italic line: *Educational analysis generated from delayed data — \
not financial advice.*"""


def _context(ticker: str, data: dict) -> str:
    """Compact JSON of the fields worth reasoning over (skip chart HTML)."""
    payload = {
        "ticker": ticker,
        "current_price": data.get("current_price"),
        "statistics": data.get("stats"),
        "forward_estimates": data.get("forward_estimates"),
        "fundamentals": data.get("fundamentals"),
        "price_ranges": data.get("price_ranges"),
        "dealer_gex": data.get("gex"),
        "ml_signal": data.get("ml"),
    }
    payload = {k: v for k, v in payload.items() if v}
    return json.dumps(payload, default=str, indent=2)


def _to_html(md: str) -> str:
    try:
        import markdown
        return markdown.markdown(md, extensions=["extra", "sane_lists"])
    except Exception:
        # No markdown lib installed — render as readable preformatted text.
        from html import escape
        return f'<div class="prose-fallback whitespace-pre-wrap">{escape(md)}</div>'


def generate_report(ticker: str, data: dict) -> str | None:
    """Return an HTML analyst report for `ticker`, or None if unavailable."""
    ticker = ticker.upper()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    cached = db.cache_get("ai_report", ticker, CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("html")

    try:
        import anthropic
    except Exception:
        return None

    try:
        client = anthropic.Anthropic()
        # Streaming + get_final_message keeps us safe from HTTP timeouts on the
        # longer reports; adaptive thinking lets Claude reason over the numbers.
        with client.messages.stream(
            model=MODEL,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},  # stable prefix → cache across tickers
            }],
            messages=[{
                "role": "user",
                "content": f"Write the desk note for {ticker} from this snapshot:\n\n{_context(ticker, data)}",
            }],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as e:
        print(f"AI report failed for {ticker}: {e}")
        return None

    if getattr(msg, "stop_reason", None) == "refusal":
        return None
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        return None

    html = _to_html(text)
    db.cache_set("ai_report", ticker, {"html": html})
    return html
