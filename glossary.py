"""Single source of truth for metric definitions.

Each entry powers both the inline ``ⓘ`` tooltips (via the ``metric`` Jinja macro)
and the full ``/glossary`` reference page. Keep ``short`` to one or two sentences
in plain English (it is the tooltip); put the longer explanation, interpretation
and caveats in ``long``; ``formula`` is optional and rendered monospaced.

The dict is registered as a Jinja global in app.py, so templates reference it as
``GLOSSARY[key]`` and the glossary page groups entries by ``section`` preserving
insertion order — so order entries the way you want them to read on the page.
"""
from __future__ import annotations

GLOSSARY: dict[str, dict] = {
    # ----------------------------------------------------------------- Risk & Return
    "annualised_vol": {
        "term": "Annualised Volatility",
        "section": "Risk & Return",
        "short": "How much the price swings day to day, scaled to a yearly figure. Higher means a bumpier, riskier ride.",
        "long": "The standard deviation of daily returns scaled to a one-year horizon. It measures dispersion, not direction — a high number just means large moves in either direction. Roughly, a 30% annualised vol implies a typical daily move near 1.9%.",
        "formula": "σ_annual = σ_daily × √252",
    },
    "max_drawdown": {
        "term": "Maximum Drawdown",
        "section": "Risk & Return",
        "short": "The worst peak-to-trough drop over the period — the deepest loss you'd have sat through if you bought the top.",
        "long": "Measures the largest cumulative decline from a running high-water mark to a subsequent low. It captures pain that volatility alone misses: a strategy can be low-vol yet still suffer a brutal, prolonged drawdown. Recovery from a 50% drawdown requires a 100% gain.",
        "formula": "DD_t = (Price_t − RunningMax_t) / RunningMax_t",
    },
    "skewness": {
        "term": "Skewness",
        "section": "Risk & Return",
        "short": "Whether the return distribution leans toward big up-moves (positive) or big down-moves (negative).",
        "long": "The asymmetry of the daily-return distribution. Negative skew (common in equities) means crashes tend to be sharper than rallies — the left tail is fatter. Positive skew means occasional large gains. Zero skew is a symmetric distribution.",
    },
    "kurtosis": {
        "term": "Kurtosis (Excess)",
        "section": "Risk & Return",
        "short": "How 'fat' the tails are — how often extreme moves happen versus a normal bell curve.",
        "long": "Excess kurtosis measures tail-heaviness relative to a normal distribution (which is 0). High positive values mean fat tails: extreme days happen far more often than a bell curve predicts. Most financial returns are strongly leptokurtic (fat-tailed), which is why VaR built on a normal assumption understates risk.",
    },
    "beta": {
        "term": "Beta (vs SPY)",
        "section": "Risk & Return",
        "short": "How much the stock moves for each 1% move in the S&P 500. >1 = more volatile than the market.",
        "long": "The slope of the stock's daily returns regressed on the market's (SPY). Beta of 1.5 implies the stock tends to move 1.5% for every 1% market move; 0.5 implies it's defensive. Beta captures only market-correlated (systematic) risk, not stock-specific risk.",
        "formula": "β = Cov(stock, market) / Var(market)",
    },
    "sharpe": {
        "term": "Sharpe Ratio",
        "section": "Risk & Return",
        "short": "Return earned per unit of risk taken. Higher is better; above 1 is generally considered strong.",
        "long": "Average excess return divided by volatility, annualised. It rewards consistent returns and penalises bumpiness. The rolling version here uses a 1-year window and assumes a risk-free rate near zero. It treats upside and downside volatility equally, which can flatter trend-following strategies.",
        "formula": "Sharpe = (mean return / std of return) × √252",
    },
    "var": {
        "term": "Value at Risk (VaR)",
        "section": "Risk & Return",
        "short": "A loss threshold you'd expect to exceed only rarely — e.g. VaR 95% is the worst day in a typical 20.",
        "long": "The historical quantile of the daily-return distribution over a rolling 1-year window. VaR 95% is the loss exceeded on the worst 5% of days; VaR 99% on the worst 1%. VaR says how often a bad day occurs but nothing about how bad the days beyond it are — that's what Expected Shortfall adds.",
    },
    "expected_shortfall": {
        "term": "Expected Shortfall (ES / CVaR)",
        "section": "Risk & Return",
        "short": "The average loss on the bad days that breach VaR — how deep the tail actually goes.",
        "long": "Also called Conditional VaR, ES is the mean of all returns worse than the VaR threshold. Where VaR marks the door to the tail, ES tells you the average severity once you're through it. It is more conservative and better-behaved than VaR for fat-tailed assets.",
    },
    "seasonality": {
        "term": "Seasonality",
        "section": "Risk & Return",
        "short": "Average return by calendar month over the full history — recurring strong/weak periods.",
        "long": "The mean daily return grouped by calendar month across all available history. It surfaces recurring patterns (e.g. the historical weakness of September). Treat it as descriptive, not predictive — with limited years of data, monthly averages are noisy and prone to overfitting.",
    },
    "monte_carlo": {
        "term": "Monte Carlo Projection",
        "section": "Risk & Return",
        "short": "1,000 simulated future price paths, showing the range of plausible outcomes — not a forecast.",
        "long": "Simulates forward prices using geometric Brownian motion calibrated to the stock's own historical drift and volatility. The shaded bands are the 5–95% and 25–75% outcome ranges across simulations. It assumes returns are normally distributed and that the past drift/vol persist — both strong assumptions, so read it as a distribution of scenarios, not a target.",
        "formula": "S_t = S_0 · exp(Σ (μ − ½σ²) + σ·Z)",
    },
    "cumulative_return": {
        "term": "Cumulative Return",
        "section": "Risk & Return",
        "short": "Growth of $100 invested at the start, compared against SPY and QQQ.",
        "long": "Each series is normalised to 100 at the earliest shared date, so lines show relative performance regardless of starting price. Rising faster than SPY/QQQ indicates outperformance over that window. Does not adjust for risk — pair it with beta and volatility.",
    },
    "poc": {
        "term": "Volume Profile / Point of Control",
        "section": "Risk & Return",
        "short": "The price level where the most volume traded over the last 2 years — a magnet for price.",
        "long": "Volume profile bins two years of volume by price level instead of by time. The Point of Control (POC) is the highest-volume price — a level where lots of positions were established, so it often acts as support or resistance. High-volume nodes tend to attract price; low-volume gaps tend to be traversed quickly.",
    },

    # ----------------------------------------------------------------- Options & Volatility
    "gex": {
        "term": "Gamma Exposure (GEX)",
        "section": "Options & Volatility",
        "short": "How much dealers must buy or sell to stay hedged as price moves. Shapes whether moves get dampened or amplified.",
        "long": "Aggregates option gamma across strikes, weighted by open interest, signed by the standard dealer convention (long calls +, short puts −). Net-positive gamma means dealers hedge against moves (selling rallies, buying dips), which dampens volatility; net-negative gamma means they hedge with moves, amplifying them. This is a naive end-of-day model built on delayed yfinance open interest and uniform dealer assumptions — directional, not an institutional feed.",
        "formula": "GEX = Γ × OI × 100 × Spot² × 0.01",
    },
    "gamma_flip": {
        "term": "Gamma Flip",
        "section": "Options & Volatility",
        "short": "The price where dealer gamma flips sign. Above it moves get dampened; below it they get amplified.",
        "long": "The strike at which cumulative net GEX crosses zero. Above the flip, dealers are net-long gamma and their hedging is mean-reverting (vol-dampening). Below it they are net-short gamma and hedging is trend-amplifying (vol-expanding). It's a regime marker, not a price target, and shifts as open interest changes.",
    },
    "call_wall": {
        "term": "Call Wall",
        "section": "Options & Volatility",
        "short": "The strike with the largest positive call gamma — often acts as a near-term ceiling / resistance.",
        "long": "The strike carrying the heaviest call open interest and gamma. As price approaches, dealer hedging tends to sell into strength, which can cap rallies. It frequently coincides with where price stalls into a monthly expiration, but it is a tendency, not a guarantee.",
    },
    "put_wall": {
        "term": "Put Wall",
        "section": "Options & Volatility",
        "short": "The strike with the largest negative put gamma — often acts as a near-term floor / support.",
        "long": "The strike with the heaviest put open interest and gamma. Dealer hedging there tends to buy into weakness, cushioning selloffs and acting as support. Like the call wall, it's a probabilistic level that can break, especially if it migrates as positions roll.",
    },
    "iv_smile": {
        "term": "Implied Volatility Smile / Skew",
        "section": "Options & Volatility",
        "short": "How option-implied volatility changes across strikes. A steep left side means the market prices downside risk dearly.",
        "long": "Plots implied volatility against moneyness (strike ÷ spot). Equities typically show a 'skew' — out-of-the-money puts carry higher IV than calls — because investors pay up for crash protection. A steepening skew signals rising fear; a flattening one signals complacency.",
    },
    "implied_volatility": {
        "term": "Implied Volatility (IV)",
        "section": "Options & Volatility",
        "short": "The market's expectation of future volatility, backed out of an option's price. Higher = pricier options.",
        "long": "The volatility input that makes the Black-Scholes model reproduce an option's traded price. Unlike realized (historical) volatility, IV is forward-looking and embeds supply/demand for options. It usually rises into earnings and macro events and collapses afterward ('vol crush').",
    },
    "delta": {
        "term": "Delta",
        "section": "Options & Volatility",
        "short": "How much an option's price moves per $1 move in the stock. Also a rough probability of finishing in-the-money.",
        "long": "The first derivative of option value with respect to spot. A 0.50-delta call gains ~$0.50 per $1 stock move and is roughly at-the-money. Calls range 0→1, puts −1→0. Delta also approximates the risk-neutral probability the option expires in-the-money and tells you the share-equivalent exposure of a position.",
    },
    "gamma": {
        "term": "Gamma",
        "section": "Options & Volatility",
        "short": "How fast delta itself changes as the stock moves. Highest for at-the-money, near-expiry options.",
        "long": "The second derivative of option value with respect to spot (the rate of change of delta). High gamma means delta — and therefore hedging needs — shift rapidly with price, which is why near-expiry at-the-money options drive the sharpest dealer hedging flows and underpin GEX.",
    },
    "theta": {
        "term": "Theta",
        "section": "Options & Volatility",
        "short": "How much value an option loses each day from time passing, all else equal — 'time decay'.",
        "long": "The daily erosion of an option's price as expiration approaches. Theta is negative for long options (you lose value each day) and accelerates as expiry nears, especially for at-the-money contracts. Option sellers collect theta; buyers pay it.",
    },
    "vega": {
        "term": "Vega",
        "section": "Options & Volatility",
        "short": "How much an option's price changes per 1-point change in implied volatility.",
        "long": "Sensitivity of option value to a 1 percentage-point change in implied volatility. Long options are long vega (they gain when IV rises). Vega is largest for longer-dated, at-the-money options, which is why event-driven IV changes hit them hardest.",
    },
    "rho": {
        "term": "Rho",
        "section": "Options & Volatility",
        "short": "How much an option's price changes per 1-point change in interest rates. Usually the least impactful Greek.",
        "long": "Sensitivity of option value to a 1 percentage-point change in the risk-free rate. Calls gain and puts lose as rates rise. Rho matters most for long-dated options (LEAPS) and is generally negligible for short-dated contracts.",
    },
    "open_interest": {
        "term": "Open Interest",
        "section": "Options & Volatility",
        "short": "The number of option contracts currently outstanding at a strike — the size of the standing position.",
        "long": "The total count of open (not-yet-closed) contracts for a given strike and expiration. Unlike volume (which counts a day's trades), OI is the cumulative standing position and is the weight behind GEX and wall calculations. yfinance reports it with a delay, typically updated once daily.",
    },
    "payoff": {
        "term": "Payoff Diagram & Breakeven",
        "section": "Options & Volatility",
        "short": "Profit/loss of an option position at expiration across stock prices, plus the breakeven point.",
        "long": "Shows P&L at expiration as a function of the underlying price for the selected position. Breakeven for a long call is strike + premium; for a long put, strike − premium. Long options cap loss at the premium; short options cap gain at the premium but carry large or unlimited loss. This ignores early assignment and pre-expiry time value.",
    },

    # ----------------------------------------------------------------- Market Microstructure
    "order_book": {
        "term": "Level 2 Order Book",
        "section": "Market Microstructure",
        "short": "The ladder of resting buy (bid) and sell (ask) orders at each price, by size.",
        "long": "Level 2 shows depth beyond the best bid/ask: how many shares are queued at each price level on both sides. It reveals where liquidity sits and where price may stall. Note: on this page the book is a simulation when no live feed key is configured.",
    },
    "order_book_imbalance": {
        "term": "Order Book Imbalance",
        "section": "Market Microstructure",
        "short": "The tilt between total resting bid size and ask size. A heavy side hints at near-term pressure.",
        "long": "The share of total displayed depth sitting on the bid versus the ask side. A strong bid imbalance suggests buyers are stacked and price may be supported; a strong ask imbalance suggests overhead supply. Displayed liquidity can be spoofed or pulled, so treat it as a soft, fast-changing signal.",
    },
    "mid_price": {
        "term": "Mid Price",
        "section": "Market Microstructure",
        "short": "The midpoint between the best bid and best ask — a cleaner 'fair' price than the last trade.",
        "long": "The average of the best bid and best ask. It's less noisy than the last traded price (which bounces between bid and ask) and is the common reference for fair value and for measuring slippage.",
        "formula": "Mid = (best bid + best ask) / 2",
    },
    "spread": {
        "term": "Bid-Ask Spread",
        "section": "Market Microstructure",
        "short": "The gap between the best buy and sell price — a direct cost of trading. Tighter = more liquid.",
        "long": "The difference between the best ask and best bid, shown in dollars and as a percentage of price. It's the immediate round-trip cost of crossing the market and a core liquidity gauge: liquid large-caps trade a penny wide, illiquid names far wider. Spreads widen in fast or stressed markets.",
        "formula": "Spread = best ask − best bid",
    },
    "vwap": {
        "term": "VWAP",
        "section": "Market Microstructure",
        "short": "Volume-Weighted Average Price for the session — the average price paid, weighted by size.",
        "long": "The session's average trade price weighted by volume. Institutions benchmark executions against VWAP (buying below it is 'good'). Price above VWAP is often read as intraday strength, below as weakness. Resets each session.",
        "formula": "VWAP = Σ(price × size) / Σ size",
    },
    "realized_vol": {
        "term": "Realized Volatility",
        "section": "Market Microstructure",
        "short": "Actual volatility measured from recent price moves, annualised — what just happened, not what's expected.",
        "long": "The standard deviation of recent log returns, annualised. Unlike implied volatility (forward-looking, from options), realized vol is backward-looking, computed from the tape. Comparing the two shows whether options are rich or cheap relative to actual movement.",
    },
    "market_depth": {
        "term": "Market Depth",
        "section": "Market Microstructure",
        "short": "Cumulative resting size on each side as you walk away from the mid — how much price would move to fill a big order.",
        "long": "Plots cumulative bid and ask size against price. A steep wall near the mid means deep liquidity and low impact; a shallow, far-reaching curve means a large order would push price further. The two sides meeting at the mid frame the cost of size.",
    },

    # ----------------------------------------------------------------- Fundamentals & Positioning
    "trailing_pe": {
        "term": "Trailing P/E",
        "section": "Fundamentals & Positioning",
        "short": "Price divided by the last 12 months of earnings. How many dollars you pay per dollar of past profit.",
        "long": "Share price over trailing-twelve-month earnings per share. A higher multiple implies the market expects growth or assigns lower risk. Meaningless for unprofitable companies and not comparable across sectors with different growth/capital profiles.",
    },
    "forward_pe": {
        "term": "Forward P/E",
        "section": "Fundamentals & Positioning",
        "short": "Price divided by next year's expected earnings. Forward-looking version of the P/E.",
        "long": "Share price over estimated forward earnings per share. Lower than trailing P/E when earnings are expected to grow. Only as reliable as the analyst estimates behind it, which are often optimistic and revised down over time.",
    },
    "ev_ebitda": {
        "term": "EV / EBITDA",
        "section": "Fundamentals & Positioning",
        "short": "Enterprise value relative to operating cash earnings — a capital-structure-neutral valuation multiple.",
        "long": "Enterprise value (market cap + debt − cash) over EBITDA. Because it includes debt and strips out financing and tax effects, it compares firms with different leverage better than P/E and is favoured in M&A and across capital structures.",
    },
    "price_book": {
        "term": "Price / Book",
        "section": "Fundamentals & Positioning",
        "short": "Price relative to net asset (book) value. Below 1 can signal value — or distress.",
        "long": "Share price over book value per share. Useful for asset-heavy and financial firms where book value is meaningful; less so for asset-light or IP-driven businesses, where book understates true value. A low ratio may flag undervaluation or deteriorating fundamentals.",
    },
    "peg": {
        "term": "PEG Ratio",
        "section": "Fundamentals & Positioning",
        "short": "P/E divided by earnings growth. Puts the valuation multiple in the context of how fast profits grow.",
        "long": "The trailing or forward P/E divided by the expected earnings-growth rate. A PEG near 1 is often read as fairly valued for the growth; below 1 as cheap relative to growth. Sensitive to the growth estimate used, so small changes swing it widely.",
    },
    "short_float": {
        "term": "Short % of Float",
        "section": "Fundamentals & Positioning",
        "short": "Share of freely tradable stock sold short. High readings flag bearish bets — and squeeze potential.",
        "long": "Shorted shares as a percentage of the public float. Elevated short interest reflects bearish positioning but also fuel for a short squeeze if the stock rises and shorts cover. Pair with short ratio (days-to-cover) to gauge how crowded the exit is.",
    },
    "short_ratio": {
        "term": "Short Ratio (Days to Cover)",
        "section": "Fundamentals & Positioning",
        "short": "How many days of average volume it would take shorts to buy back their position.",
        "long": "Shares sold short divided by average daily volume. A high ratio means shorts can't exit quickly without moving price, increasing squeeze risk on positive catalysts. A low ratio means short covering would be absorbed easily.",
    },
    "dividend_yield": {
        "term": "Dividend Yield",
        "section": "Fundamentals & Positioning",
        "short": "Annual dividends as a percentage of price — the income return before any price change.",
        "long": "Trailing annual dividend per share over price. Higher yields offer income but can signal a depressed price or a payout at risk of being cut. Always check it against the payout ratio and cash flow for sustainability.",
    },
    "price_target": {
        "term": "Analyst Price Target",
        "section": "Fundamentals & Positioning",
        "short": "The consensus 12-month price covering analysts expect, with low/mean/high range and implied upside.",
        "long": "Aggregates sell-side analysts' 12-month price targets into low, mean, median and high. The spread shows disagreement; the mean versus current price implies upside/downside. Targets lag price, cluster around it, and carry well-documented optimism bias — a directional gauge, not a guarantee.",
    },
    "mspr": {
        "term": "Insider Sentiment (MSPR)",
        "section": "Fundamentals & Positioning",
        "short": "A monthly score of net insider buying vs selling. Positive = insiders accumulating.",
        "long": "Monthly Share Purchase Ratio summarises insider transactions into a −100…+100 sentiment score. Sustained positive readings — especially executive buying — are a historically constructive signal, since insiders buy for one reason but sell for many. Sourced from Finnhub when configured.",
    },
    "insider_transactions": {
        "term": "Insider Transactions",
        "section": "Fundamentals & Positioning",
        "short": "Buys and sells by company officers and directors, netted by month and shown against price.",
        "long": "Form 3/4/5 filings of trades by insiders (executives, directors, 10% owners). Net buying, particularly clustered open-market purchases by executives, has historically preceded outperformance. Sales are noisier (diversification, taxes, 10b5-1 plans) and harder to read.",
    },
    "institutional_holders": {
        "term": "Institutional Holders",
        "section": "Fundamentals & Positioning",
        "short": "The largest fund/institution shareholders by shares held — who the big owners are.",
        "long": "Top institutional owners (funds, asset managers) ranked by shares held, from regulatory disclosures. Heavy, stable institutional ownership signals conviction but can also mean crowded positioning that unwinds together. Data is reported with a lag.",
    },
    "sec_13f": {
        "term": "13F Filings",
        "section": "Fundamentals & Positioning",
        "short": "Quarterly disclosures of large institutions' US equity holdings — a delayed window into smart-money positioning.",
        "long": "Institutional managers over $100M must file Form 13F within 45 days of quarter-end, listing long US equity positions. It reveals accumulation/distribution trends but is stale by up to a quarter and omits shorts, cash and non-US holdings — context, not a real-time signal.",
    },
    "consensus_estimates": {
        "term": "Consensus Estimates (EPS / Revenue)",
        "section": "Fundamentals & Positioning",
        "short": "The average analyst forecast for next-period earnings and revenue, with the next earnings date.",
        "long": "The mean of sell-side analyst forecasts for upcoming EPS and revenue. The market reacts to results relative to these expectations, not in absolute terms — beating a high bar can still sell off. Estimates drift and are revised right up to the print.",
    },
    "growth": {
        "term": "Earnings / Revenue Growth",
        "section": "Fundamentals & Positioning",
        "short": "Year-over-year growth in profits and sales — the engine behind forward valuation multiples.",
        "long": "Trailing year-over-year change in earnings and revenue. Revenue growth shows top-line demand; earnings growth shows whether that translates to the bottom line. Earnings growing faster than revenue indicates margin expansion; the reverse, margin pressure.",
    },
}
