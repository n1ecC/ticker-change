"""AI analyst report — a written take on a stock, via an LLM.

Feeds the dashboard's already-computed numbers (price action, volatility, VaR,
Monte-Carlo odds, valuation multiples, dealer GEX positioning, and the ML
signal) to an LLM with a seasoned-analyst system prompt, and returns a concise
HTML report for the analytics page. Results are cached in the shared SQLite
api_cache table so a page refresh doesn't re-bill the API.

Works with any configured AI provider — Anthropic (Claude), OpenAI, Google
Gemini, or OpenRouter — resolved from the /settings page or environment (see
providers.ai_providers). They're tried in order, so the report falls back to the
next provider if one is missing, errored, or out of quota. Degrades gracefully:
with no key configured (or every provider failing) it returns None and the
report section simply doesn't render.
"""
from __future__ import annotations

import json

import requests

import db
import providers

CACHE_TTL_HOURS = 12
HTTP_TIMEOUT = 90

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


def _anthropic_report(key: str, model: str, user_msg: str) -> str | None:
    """Generate the report via the Anthropic SDK (native Claude API)."""
    try:
        import anthropic
    except Exception:
        return None
    client = anthropic.Anthropic(api_key=key)
    # Streaming + get_final_message keeps us safe from HTTP timeouts on the
    # longer reports; adaptive thinking lets Claude reason over the numbers.
    with client.messages.stream(
        model=model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        system=[{
            "type": "text",
            "text": SYSTEM,
            "cache_control": {"type": "ephemeral"},  # stable prefix → cache across tickers
        }],
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        msg = stream.get_final_message()
    if getattr(msg, "stop_reason", None) == "refusal":
        return None
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    return text or None


def _openai_compatible_report(base_url: str, key: str, model: str, user_msg: str) -> str | None:
    """Generate the report via an OpenAI-compatible /chat/completions endpoint.

    Shared by OpenAI, Google Gemini (OpenAI-compat base), and OpenRouter — they
    all speak the same request/response shape, so one path covers all three.
    """
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 4000,
            "temperature": 0.4,
        },
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        print(f"[ai] {base_url} -> HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    choices = (resp.json() or {}).get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content")
    return content.strip() if content else None


def generate_report(ticker: str, data: dict) -> str | None:
    """Return an HTML analyst report for `ticker`, or None if unavailable.

    Tries each configured AI provider in order and uses the first that returns a
    report, so a missing/exhausted provider transparently rolls over to the next.
    """
    ticker = ticker.upper()
    configured = providers.ai_providers()
    if not configured:
        return None

    cached = db.cache_get("ai_report", ticker, CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("html")

    user_msg = (
        f"Write the desk note for {ticker} from this snapshot:\n\n{_context(ticker, data)}"
    )

    text, used = None, None
    for prov in configured:
        try:
            if prov["id"] == "anthropic":
                text = _anthropic_report(prov["key"], prov["model"], user_msg)
            else:
                text = _openai_compatible_report(
                    prov["base_url"], prov["key"], prov["model"], user_msg
                )
        except Exception as e:
            print(f"AI report via {prov['id']} failed for {ticker}: {e}")
            text = None
        if text:
            used = prov["id"]
            break

    if not text:
        return None

    html = _to_html(text)
    db.cache_set("ai_report", ticker, {"html": html, "provider": used})
    return html
