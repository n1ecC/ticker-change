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
machine-learning signal. Write a balanced, professional, and scientifically rigorous briefing.

Rules:
- Ground every claim in the numbers provided. Do not invent figures, news, \
earnings results, or price targets that aren't in the data. If something isn't \
given, say it's not available rather than guessing.
- Be even-handed: state the bull case and the bear case. Never cheerlead.
- Synthesize the indicators into clear "Buyer Signals" using statistical rigor:
  * For GEX: Assess the market micro-structure. Is the GEX regime positive (vol-dampening, supportive of mean reversion/support at walls) or negative (vol-expanding, trend-amplifying)? Analyze the Call Wall, Put Wall, and Zero-Gamma flip relative to the Spot price to explain where dealer hedging could accelerate or cap price action.
  * For Valuation & Fundamentals: Analyze how multiple metrics (P/E, PEG, EV/EBITDA) compare to growth rates and estimates to see if valuation is supported.
  * For Technical & Volatility: Connect realized volatility, Sharpe, Beta, and the ML signal.
  * For Key Risks: Weigh the 1-day Value-at-Risk (VaR) and Expected Shortfall (ES) at 95%/99% confidence to detail potential tail risk.
  * For Monte Carlo: Detail the path projections and the probability of upward movement over time.
- If an `ml_signal` is present, work it into the Technical & Volatility Picture and \
the Bottom Line: state its action and confidence, name the specific feature drivers \
it cites (e.g. momentum, RSI, distance to moving averages, volatility), and say \
whether they agree or conflict with the other evidence. Weight it lightly — it is a \
weak signal that has not beaten buy-and-hold out of sample.
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


def parse_html_sections(html: str) -> dict[str, str]:
    """Parse a compiled HTML report into sections based on <h2/3/4> headers."""
    import re
    sections = {}
    
    # Match headers <h2/3/4>...</h2/3/4> and capture content up to the next header or end of string.
    pattern = re.compile(r'<h[234]>(.*?)</h[234]>(.*?)(?=(?:<h[234]>|$))', re.DOTALL | re.IGNORECASE)
    matches = pattern.findall(html)
    
    header_mapping = {
        "snapshot": "snapshot",
        "valuation": "valuation",
        "fundamentals": "valuation",
        "technical": "technical",
        "volatility": "technical",
        "options": "options",
        "dealer": "options",
        "gex": "options",
        "risks": "risks",
        "catalysts": "risks",
        "bottom line": "bottom_line",
        "verdict": "bottom_line"
    }
    
    for header, content in matches:
        clean_header = header.strip().lower()
        content = content.strip()
        
        found_key = None
        for phrase, key in header_mapping.items():
            if phrase in clean_header:
                found_key = key
                break
                
        if found_key:
            sections[found_key] = content
            
    return sections


def _anthropic_report(key: str, model: str, user_msg: str, system_prompt: str = SYSTEM) -> str | None:
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
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},  # stable prefix → cache across tickers
        }],
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        msg = stream.get_final_message()
    if getattr(msg, "stop_reason", None) == "refusal":
        return None
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    return text or None


def _openai_compatible_report(base_url: str, key: str, model: str, user_msg: str, system_prompt: str = SYSTEM) -> str | None:
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
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": 4000,
            "temperature": 0.4,
        },
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")
    choices = (resp.json() or {}).get("choices") or []
    if not choices:
        raise Exception(f"No choices returned by model API. Response: {resp.text[:300]}")
    content = (choices[0].get("message") or {}).get("content")
    if not content:
        raise Exception(f"Model returned empty content choice. Response: {resp.text[:300]}")
    return content.strip()


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
                text = _anthropic_report(prov["key"], prov["model"], user_msg, SYSTEM)
            else:
                text = _openai_compatible_report(
                    prov["base_url"], prov["key"], prov["model"], user_msg, SYSTEM
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


COMPREHENSIVE_SYSTEM = """You are a world-class quantitative hedge fund strategist and financial research director. You are given a comprehensive, multi-dimensional dataset of a single stock ticker containing daily returns stats, tail risk calculations (VaR, Expected Shortfall), valuation multiples, dealer GEX walls/ regime flip details, insider trading MSP sentiment, top institutional 13F holders, seasonality history, momentum indices, and machine learning model signal/feature weights.

Your task is to write a highly rigorous, comprehensive quantitative analysis and trading strategy report.

Format your response in GitHub-Flavoured Markdown. Use the following structured sections:
1. ### Executive Summary: High-level overview of the name.
2. ### Comprehensive Data Summary (Tables):
   You must compile the input numbers into structured Markdown tables summarizing EVERY single category of data provided:
   - Table A: Price, Realized Volatility & Risk Metrics (drawdown, Sharpe, Beta, VaR, Expected Shortfall, etc.)
   - Table B: Valuation & Consensus Growth Guidance (PE, PEG, revenue growth, sector/industry, etc.)
   - Table C: Options Market & Dealer GEX Profile (flip point, walls, HVL, net regime, etc.)
   - Table D: Insider Transactions & Institutional Ownership (sentiment, recent purchases, top holders, etc.)
   - Table E: Momentum Performance & Backtest Statistics (CAGR, win-rate, profit factor, Jensen's alpha, etc.)
   - Table F: Machine Learning Signal & Features (action, confidence, drivers, etc.)
3. ### Scientific Analysis of Signal Convergence:
   Critically evaluate how these metrics confirm or contradict each other. For example: does positive/negative GEX dealer positioning support or counter the ML signal? Does the risk-adjusted momentum score align with institutional accumulation? Is the tail risk (VaR/ES) justified by growth estimates?
4. ### Tail Risk & Forward Scenario Analysis:
   Analyze the downside risk and Monte Carlo path probabilities. Discuss tail scenarios.
5. ### Trading Strategy Synthesis & Recommendations:
   Synthesize all the above data points into a clear, actionable trading strategy recommendation. Define:
   - Position sizing recommendation (based on volatility, realized ATR, and tail risk)
   - Entry thresholds and catalyst parameters
   - Exit parameters (stop-loss, profit targets, or options hedging overlay)
   - Execution timeframe (short-term options play, medium-term momentum follow, long-term fundamentals build)
6. ### Concluding Verdict: A single clear-cut operational summary.

Rules:
- Be extremely thorough and precise. Cover EVERY data point provided.
- Do not invent any numbers. If a data point is missing or empty, mark it as 'N/A' in the tables and explain that it is not available.
- Treat every indicator with scientific skepticism, detailing the limits of the models (e.g. backtest overfitting, delayed yfinance data, option model approximations).
"""


def generate_comprehensive_report(ticker: str, data: dict) -> tuple[str | None, str | None]:
    """Return an HTML comprehensive quantitative strategy report for `ticker` and any error message as a tuple (html, error)."""
    ticker = ticker.upper()
    configured = providers.ai_providers()
    if not configured:
        return None, "No AI providers configured. Please go to Settings to add an API key."

    cached = db.cache_get("ai_comprehensive_report", ticker, CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("html"), None

    user_msg = (
        f"Write the comprehensive quantitative strategy report for {ticker} from this dataset:\n\n"
        f"{json.dumps(data, default=str, indent=2)}"
    )

    errors = []
    text, used = None, None
    for prov in configured:
        try:
            if prov["id"] == "anthropic":
                text = _anthropic_report(prov["key"], prov["model"], user_msg, COMPREHENSIVE_SYSTEM)
            else:
                text = _openai_compatible_report(
                    prov["base_url"], prov["key"], prov["model"], user_msg, COMPREHENSIVE_SYSTEM
                )
            if not text:
                errors.append(f"{prov['label']} ({prov['model']}) returned empty content.")
        except Exception as e:
            err_msg = f"{prov['label']} ({prov['model']}) failed: {str(e)}"
            print(f"AI comprehensive report via {prov['id']} failed for {ticker}: {e}")
            errors.append(err_msg)
        if text:
            used = prov["id"]
            break

    if not text:
        err_report = "All configured AI providers failed to generate the report:\n- " + "\n- ".join(errors)
        return None, err_report

    html = _to_html(text)
    db.cache_set("ai_comprehensive_report", ticker, {"html": html, "provider": used})
    return html, None
