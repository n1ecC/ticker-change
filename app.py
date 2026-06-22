from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import threading
import math
from concurrent.futures import ThreadPoolExecutor
from db import init_db, is_fresh, get_prices, store_prices
import db
import providers
import ml
import ai
from glossary import GLOSSARY

app = Flask(__name__)
CORS(app)

# Expose metric definitions to every template so the `metric` tooltip macro and
# the /glossary page share a single source of truth.
app.jinja_env.globals['GLOSSARY'] = GLOSSARY

with app.app_context():
    init_db()


# Process-level memo: collapses duplicate get_or_fetch_prices() calls within and
# across nearby requests (e.g. SPY is needed by beta, cumulative-return, etc.) so
# we don't re-query SQLite and rebuild the DataFrame several times per page load.
_PRICE_MEMO: dict = {}
_PRICE_MEMO_TTL = 45  # seconds
_PRICE_MEMO_LOCK = threading.Lock()


def _fetch_yfinance_with_retry(symbol: str, period: str, retries: int = 3):
    """Fetch history from yfinance with exponential backoff. Returns a df or None.

    yfinance frequently fails or returns empty on the first attempt (Yahoo rate
    limiting / transient errors); retrying is what eliminates most of the
    intermittent "data could not be retrieved" failures.
    """
    delay = 0.6
    for attempt in range(retries):
        try:
            df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
            if not df.empty:
                return df
            print(f"yfinance empty for {symbol} (attempt {attempt + 1}/{retries})")
        except Exception as e:
            print(f"yfinance fetch failed for {symbol} (attempt {attempt + 1}/{retries}): {e}")
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
    return None


def get_or_fetch_prices(symbol: str, period: str = "5y") -> pd.DataFrame | None:
    """Return cached prices from the DB, refreshing from yfinance when stale.

    Layered for speed and resilience:
      1. In-process memo (skips redundant DB reads within a request).
      2. SQLite cache (skips the network when data is < 1h old).
      3. yfinance fetch with retry/backoff on a miss.
      4. Stale-while-error: if the refresh fails but we hold older cached rows,
         serve those rather than returning nothing.
    """
    symbol = symbol.upper()

    now = time.time()
    with _PRICE_MEMO_LOCK:
        cached = _PRICE_MEMO.get(symbol)
        if cached is not None and now - cached[0] < _PRICE_MEMO_TTL:
            return cached[1]

    if not is_fresh(symbol):
        df = _fetch_yfinance_with_retry(symbol, period)
        if df is not None and not df.empty:
            store_prices(symbol, df)
        else:
            # Refresh failed — fall through and serve whatever we already have.
            print(f"Serving stale/cached data for {symbol} (refresh unavailable)")

    result = get_prices(symbol)

    # Only memoise real data so a transient failure isn't cached as "no data".
    if result is not None and not result.empty:
        with _PRICE_MEMO_LOCK:
            _PRICE_MEMO[symbol] = (now, result)

    return result

def calculate_date_periods():
    """Calculate the date periods for comparison"""
    today = datetime.now()
    
    # include a richer set of short-term periods: days 1-5 and weeks 1-4
    periods = {
        '1D': today - timedelta(days=1),
        '2D': today - timedelta(days=2),
        '3D': today - timedelta(days=3),
        '4D': today - timedelta(days=4),
        '5D': today - timedelta(days=5),
        '1W': today - timedelta(weeks=1),
        '2W': today - timedelta(weeks=2),
        '3W': today - timedelta(weeks=3),
        '1M': today - timedelta(days=30),
        '2M': today - timedelta(days=60),
        '3M': today - timedelta(days=90),
        '6M': today - timedelta(days=180),
        '1Y': today - timedelta(days=365),
        '2Y': today - timedelta(days=365*2),
        '3Y': today - timedelta(days=365*3),
        '4Y': today - timedelta(days=365*4),
        '5Y': today - timedelta(days=365*5),
        'YTD': datetime(today.year, 1, 1)
    }
    
    return periods

def get_current_price_yfinance(ticker, retries=3):
    """Get current price using yfinance with retry logic"""
    for attempt in range(retries):
        try:
            stock = yf.Ticker(ticker.upper())
            # Use shorter period for faster response, retry with longer if needed
            hist = stock.history(period="1d")
            
            if hist.empty and attempt < retries - 1:
                # Try with longer period on retry
                time.sleep(0.5)
                hist = stock.history(period="5d")
            
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
            
            # If empty, wait and retry
            if attempt < retries - 1:
                time.sleep(1)
                
        except Exception as e:
            print(f"Attempt {attempt + 1} failed for {ticker}: {str(e)}")
            if attempt < retries - 1:
                time.sleep(1)
            continue
    
    return None

def get_historical_price_yfinance(ticker, target_date, retries=2):
    """Get historical price using yfinance with fixed datetime handling and retry logic"""
    for attempt in range(retries):
        try:
            stock = yf.Ticker(ticker.upper())
            
            # Determine appropriate period based on target date
            days_diff = (datetime.now() - target_date).days

            # Short windows: use 5d for targets up to 5 days ago
            if days_diff <= 5:
                period = "5d"
            # 2-week window
            elif days_diff <= 14:
                period = "1mo"
            # 3-week window
            elif days_diff <= 21:
                period = "1mo"
            # ~4 weeks / 1 month
            elif days_diff <= 30:
                period = "3mo"
            # up to ~3 months
            elif days_diff <= 90:
                period = "6mo"
            # up to ~1 year
            elif days_diff <= 365:
                period = "1y"
            elif days_diff <= 365 * 2:
                period = "2y"
            elif days_diff <= 365 * 5:
                period = "5y"
            else:
                period = "max"
            
            hist = stock.history(period=period)
            
            if hist.empty:
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                return None
                
            # Handle timezone-aware datetime comparison properly
            hist.index = pd.to_datetime(hist.index).tz_localize(None)
            target_date = pd.to_datetime(target_date).tz_localize(None)
            
            # Find the closest date that's before or equal to target
            valid_dates = hist.index[hist.index <= target_date]
            
            if len(valid_dates) > 0:
                closest_date = valid_dates[-1]
                return float(hist.loc[closest_date]['Close'])
            else:
                # If no previous dates, use the first available
                return float(hist['Close'].iloc[0])
                
        except Exception as e:
            print(f"Historical price attempt {attempt + 1} failed for {ticker}: {str(e)}")
            if attempt < retries - 1:
                time.sleep(0.5)
            continue
    
    return None

def calculate_weekdays_ago(num_weekdays):
    """Calculate the date N weekdays (trading days) ago"""
    if num_weekdays <= 0:
        return datetime.now()
    
    current_date = datetime.now()
    weekdays_counted = 0
    
    while weekdays_counted < num_weekdays:
        current_date -= timedelta(days=1)
        # Monday=0, Sunday=6, so weekdays are 0-4
        if current_date.weekday() < 5:
            weekdays_counted += 1
    
    return current_date

def get_price_ranges(ticker, current_price=None):
    """Compute all-time and 52-week high/low and 30-day ATR from cached DB prices."""
    df = get_or_fetch_prices(ticker)
    if df is None or df.empty:
        return None

    one_year_ago = pd.Timestamp.utcnow().tz_localize(None) - pd.DateOffset(years=1)
    year_df = df[df.index >= one_year_ago]

    all_time_high = float(df['high'].max())
    all_time_low  = float(df['low'].min())
    week52_high   = float(year_df['high'].max()) if not year_df.empty else all_time_high
    week52_low    = float(year_df['low'].min())  if not year_df.empty else all_time_low

    result = {
        'all_time_high': round(all_time_high, 2),
        'all_time_low':  round(all_time_low,  2),
        '52_week_high':  round(week52_high,   2),
        '52_week_low':   round(week52_low,    2),
    }

    month_df = df.tail(30).copy()
    if len(month_df) > 1:
        month_df['prev_close'] = month_df['close'].shift(1)
        tr = pd.concat([
            month_df['high'] - month_df['low'],
            (month_df['high'] - month_df['prev_close']).abs(),
            (month_df['low']  - month_df['prev_close']).abs(),
        ], axis=1).max(axis=1)
        atr_value = float(tr.mean())
        result['atr_30d'] = round(atr_value, 2)
        if current_price and current_price > 0:
            result['atr_30d_percent'] = round((atr_value / current_price) * 100, 2)

    return result

def generate_stock_chart(ticker, period="5y"):
    """Generate an interactive Plotly price chart with timeframe controls from cached DB data."""
    df = get_or_fetch_prices(ticker)
    if df is None or df.empty:
        return None

    try:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            row_heights=[0.7, 0.3],
            subplot_titles=('Price', 'Volume')
        )

        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df['open'],
                high=df['high'],
                low=df['low'],
                close=df['close'],
                name='Price',
                increasing_line_color='#10b981',
                decreasing_line_color='#f43f5e'
            ),
            row=1, col=1
        )

        colors = ['#10b981' if row['close'] >= row['open'] else '#f43f5e'
                  for idx, row in df.iterrows()]

        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df['volume'],
                name='Volume',
                marker_color=colors,
                showlegend=False,
                marker_line_width=0
            ),
            row=2, col=1
        )

        fig.update_layout(
            template='plotly_white',
            height=600,
            hovermode='x unified',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(size=12, family='Inter, sans-serif'),
            autosize=True,
            margin=dict(l=60, r=60, t=60, b=80),
            showlegend=False,
            xaxis_rangeslider_visible=False,
            transition=dict(duration=500, easing='cubic-in-out')
        )

        fig.update_yaxes(title_text="Price ($)", row=1, col=1)
        fig.update_yaxes(title_text="Volume", row=2, col=1)

        fig.update_xaxes(
            rangeselector=dict(
                buttons=list([
                    dict(count=1,  label="1D",  step="day",   stepmode="backward"),
                    dict(count=5,  label="5D",  step="day",   stepmode="backward"),
                    dict(count=1,  label="1M",  step="month", stepmode="backward"),
                    dict(count=3,  label="3M",  step="month", stepmode="backward"),
                    dict(count=6,  label="6M",  step="month", stepmode="backward"),
                    dict(count=1,  label="YTD", step="year",  stepmode="todate"),
                    dict(count=1,  label="1Y",  step="year",  stepmode="backward"),
                    dict(count=5,  label="5Y",  step="year",  stepmode="backward"),
                    dict(label="All", step="all")
                ]),
                bgcolor='#f4f4f5',
                activecolor='#f59e0b',
                x=0, y=1.0,
                xanchor='left', yanchor='top',
                font=dict(size=9)
            ),
            type='date',
            title_text="Date",
            row=2, col=1
        )

        chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
        date_range = {
            'start': df.index[0].strftime('%Y-%m-%d'),
            'end':   df.index[-1].strftime('%Y-%m-%d')
        }
        return {'html': chart_html, 'date_range': date_range}

    except Exception as e:
        print(f"Chart generation failed for {ticker}: {e}")
        return None

def get_chart_data_json(ticker, start_date=None, end_date=None):
    """Return OHLCV records from DB, optionally filtered by date range."""
    df = get_or_fetch_prices(ticker)
    if df is None or df.empty:
        return None

    if start_date:
        df = df[df.index >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date)]

    data = [
        {
            'date':   idx.strftime('%Y-%m-%d'),
            'open':   row['open'],
            'high':   row['high'],
            'low':    row['low'],
            'close':  row['close'],
            'volume': int(row['volume']),
        }
        for idx, row in df.iterrows()
    ]
    return {
        'data': data,
        'date_range': {
            'start': df.index[0].strftime('%Y-%m-%d'),
            'end':   df.index[-1].strftime('%Y-%m-%d'),
        }
    }

def get_stock_data(ticker):
    """Get all stock data for web display, using the DB as the single data source."""
    if not ticker:
        return {"error": "No ticker provided"}

    df = get_or_fetch_prices(ticker)
    if df is None or df.empty:
        return {"error": f"Could not retrieve data for {ticker}"}

    current_price = float(df['close'].iloc[-1])
    periods = calculate_date_periods()

    percentage_data = [{"period": "Current Price", "value": f"${current_price:.2f}"}]
    net_change_data = [{"period": "Current Price", "value": f"${current_price:.2f}"}]

    for period_name, target_date in periods.items():
        target_ts = pd.to_datetime(target_date).tz_localize(None)
        valid = df[df.index <= target_ts]
        if valid.empty:
            historical_price = None
        else:
            historical_price = float(valid['close'].iloc[-1])

        if historical_price and not np.isnan(historical_price) and historical_price != 0:
            net_change = current_price - historical_price
            pct_change = ((current_price - historical_price) / historical_price) * 100
            percentage_data.append({
                "period": period_name,
                "value": round(pct_change, 2),
                "raw_value": pct_change,
                "is_positive": pct_change > 0,
            })
            net_change_data.append({
                "period": period_name,
                "value": round(net_change, 2),
                "raw_value": net_change,
                "is_positive": net_change > 0,
            })
        else:
            percentage_data.append({"period": period_name, "value": "N/A"})
            net_change_data.append({"period": period_name, "value": "N/A"})

    chart_result = generate_stock_chart(ticker)
    return {
        "ticker": ticker.upper(),
        "current_price": current_price,
        "percentage_data": percentage_data,
        "net_change_data": net_change_data,
        "chart_html": chart_result['html'] if chart_result else None,
        "chart_date_range": chart_result['date_range'] if chart_result else None,
    }

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200


@app.route('/glossary')
def glossary_page():
    """Full metric reference, grouped by section (preserving GLOSSARY order)."""
    sections: dict[str, list] = {}
    for key, g in GLOSSARY.items():
        sections.setdefault(g['section'], []).append({**g, 'key': key})
    return render_template('glossary.html', sections=sections)

@app.route('/api/stock/<ticker>')
def stock_api(ticker):
    data = get_stock_data(ticker)
    return jsonify(data)

@app.route('/api/chart-data/<ticker>')
def chart_data_api(ticker):
    """API endpoint to fetch chart data for a specific date range"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    result = get_chart_data_json(ticker, start_date=start_date, end_date=end_date)
    
    if result is None:
        return jsonify({"error": f"Could not retrieve chart data for {ticker}"}), 404
    
    return jsonify(result)

@app.route('/stock')
def stock_page():
    ticker = request.args.get('ticker', '')
    weekdays = request.args.get('weekdays', '')
    days = request.args.get('days', '')
    
    if ticker:
        data = get_stock_data(ticker)
        
        # Get price ranges (all-time and 52-week) - always fetch for display
        price_ranges = get_price_ranges(ticker, current_price=data.get('current_price'))
        if price_ranges:
            data['price_ranges'] = price_ranges
        
        # Handle custom lookback (weekdays or days)
        custom_result = None
        df_cached = get_or_fetch_prices(ticker)

        def _lookback_result(target_date, label_type, label_count):
            if df_cached is None or df_cached.empty:
                return {'error': 'No price data available'}
            target_ts = pd.to_datetime(target_date).tz_localize(None)
            valid = df_cached[df_cached.index <= target_ts]
            if valid.empty:
                return {'error': f'Could not retrieve price for {label_count} {label_type} ago'}
            historical_price = float(valid['close'].iloc[-1])
            current_price = data['current_price']
            net_change = current_price - historical_price
            pct_change = ((current_price - historical_price) / historical_price) * 100
            atr_percent = None
            if price_ranges and 'atr_30d' in price_ranges and current_price > 0:
                atr_percent = round((price_ranges['atr_30d'] / current_price) * 100, 2)
            return {
                'type': label_type,
                'days': label_count,
                'target_date': target_date.strftime('%Y-%m-%d'),
                'historical_price': round(historical_price, 2),
                'current_price': round(current_price, 2),
                'net_change': round(net_change, 2),
                'percentage_change': round(pct_change, 2),
                'is_positive': net_change > 0,
                'atr_percent': atr_percent,
            }

        if weekdays:
            try:
                num_weekdays = int(weekdays)
                if num_weekdays > 0:
                    custom_result = _lookback_result(
                        calculate_weekdays_ago(num_weekdays), 'weekdays', num_weekdays
                    )
            except ValueError:
                custom_result = {'error': 'Please enter a valid number'}
        elif days:
            try:
                num_days = int(days)
                if num_days > 0:
                    custom_result = _lookback_result(
                        datetime.now() - timedelta(days=num_days), 'days', num_days
                    )
            except ValueError:
                custom_result = {'error': 'Please enter a valid number'}
        
        data['custom_result'] = custom_result
        data['fundamentals'] = get_fundamentals(ticker)
        return render_template('stock.html', data=data)
    return render_template('index.html')

def get_fundamentals(ticker: str) -> dict | None:
    """Fetch key valuation multiples, short interest, consensus forecasts, and upcoming events from yfinance."""
    try:
        t = yf.Ticker(ticker.upper())
        info = t.info or {}
        
        # Extract upcoming earnings dates and consensus forecasts if available
        cal = None
        try:
            cal = t.calendar
        except Exception:
            pass  # Some tickers (e.g., indices) might fail to return calendar data
            
        next_earnings = None
        consensus_eps = None
        consensus_rev = None
        
        if cal and isinstance(cal, dict):
            dates = cal.get('Earnings Date')
            if dates and len(dates) > 0:
                next_earnings = dates[0].strftime('%Y-%m-%d')
            consensus_eps = cal.get('Earnings Average')
            consensus_rev = cal.get('Revenue Average')

        raw = {
            'Name':           info.get('longName') or info.get('shortName'),
            'Sector':         info.get('sector'),
            'Industry':       info.get('industry'),
            'Market Cap':     info.get('marketCap'),
            'Trailing P/E':   info.get('trailingPE'),
            'Forward P/E':    info.get('forwardPE'),
            'EV / EBITDA':    info.get('enterpriseToEbitda'),
            'Price / Book':   info.get('priceToBook'),
            'EPS (TTM)':      info.get('trailingEps'),
            'Short % Float':  info.get('shortPercentOfFloat'),
            'Short Ratio':    info.get('shortRatio'),
            'Dividend Yield': info.get('dividendYield'),
            
            # --- Forward Estimates & Guidance ---
            'PEG Ratio':       info.get('pegRatio'),
            'Earnings Growth': info.get('earningsGrowth'),
            'Revenue Growth':  info.get('revenueGrowth'),
            'Next Earnings':   next_earnings,
            'Consensus EPS':   consensus_eps,
            'Consensus Revenue': consensus_rev,
        }
        return {k: v for k, v in raw.items() if v is not None}
    except Exception as e:
        print(f"Fundamentals fetch failed for {ticker}: {e}")
        return None


def get_options_smile(ticker: str, current_price: float) -> str | None:
    """Build an IV smile chart from yfinance options data across 3-4 expirations."""
    try:
        stock = yf.Ticker(ticker.upper())
        expirations = stock.options
        if not expirations:
            return None

        selected = expirations[:min(4, len(expirations))]
        palette = ['#f59e0b', '#6366f1', '#10b981', '#f43f5e']

        fig = go.Figure()
        plotted = 0
        for exp in selected:
            chain = stock.option_chain(exp)
            calls = chain.calls.copy() if hasattr(chain, 'calls') else pd.DataFrame()
            if calls.empty or 'impliedVolatility' not in calls.columns:
                continue
            calls = calls[
                (calls['strike'] >= current_price * 0.70) &
                (calls['strike'] <= current_price * 1.50) &
                (calls['impliedVolatility'] > 0.01)
            ].sort_values('strike')
            if len(calls) < 3:
                continue
            fig.add_trace(go.Scatter(
                x=calls['strike'] / current_price,
                y=calls['impliedVolatility'] * 100,
                mode='lines+markers',
                name=exp,
                line=dict(color=palette[plotted % len(palette)], width=2),
                marker=dict(size=5),
            ))
            plotted += 1

        if plotted == 0:
            return None

        fig.add_vline(x=1.0, line_dash='dash', line_color='#a1a1aa',
                      annotation_text='ATM', annotation_position='top right')
        fig.update_layout(
            xaxis_title='Moneyness  (Strike / Spot)',
            yaxis_title='Implied Volatility (%)',
            template='plotly_white',
            height=350,
            margin=dict(l=50, r=30, t=30, b=50),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)
    except Exception as e:
        print(f"Options smile failed for {ticker}: {e}")
        return None


def get_gex_profile(ticker: str, current_price: float, rf_rate: float = 0.045) -> dict | None:
    """Build a dealer Gamma Exposure (GEX) profile from yfinance option chains.

    Per strike:  GEX = Γ × OpenInterest × 100 × Spot² × 0.01, signed by the
    standard dealer-positioning convention (long calls → +, short puts → −).
    yfinance does not return Γ, so it is computed with the in-house Black-Scholes
    engine (`calculate_greeks`) from the strike, spot, time-to-expiry and the
    chain's implied volatility. Aggregated across the nearest expirations this
    yields the structural levels traders watch: the gamma-flip strike, the call
    wall (resistance) and the put wall (support). Values are in $M per 1% move.

    This is a naive end-of-day model (delayed yfinance OI, uniform dealer
    assumptions) — directional, not an institutional low-latency feed.
    """
    try:
        stock = yf.Ticker(ticker.upper())
        expirations = stock.options
        if not expirations or not current_price:
            return None

        today = datetime.now()
        # Near-term expirations dominate dealer gamma; cap the chain count so the
        # page stays responsive (each option_chain call is a separate request).
        selected = []
        for exp in expirations:
            try:
                exp_dt = datetime.strptime(exp, '%Y-%m-%d')
            except ValueError:
                continue
            dte = (exp_dt - today).days
            if dte < 0:
                continue
            selected.append((exp, exp_dt, dte))
            if len(selected) >= 6:
                break
        if not selected:
            return None

        # Independent network calls — fetch the chains concurrently.
        def _fetch(exp_tuple):
            try:
                return exp_tuple, stock.option_chain(exp_tuple[0])
            except Exception:
                return exp_tuple, None

        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            fetched = list(pool.map(_fetch, selected))

        lo, hi = current_price * 0.80, current_price * 1.20
        agg: dict = {}  # strike -> {'call': gex, 'put': gex}

        for (exp, exp_dt, dte), chain in fetched:
            if chain is None:
                continue
            t_years = max(1e-5, (dte + 1) / 365.0)
            for df_side, side in ((getattr(chain, 'calls', None), 'call'),
                                  (getattr(chain, 'puts', None), 'put')):
                if df_side is None or df_side.empty:
                    continue
                for _, row in df_side.iterrows():
                    k = float(row.get('strike', 0) or 0)
                    if not (lo <= k <= hi):
                        continue
                    oi = row.get('openInterest', 0)
                    iv = row.get('impliedVolatility', 0)
                    if oi is None or iv is None or np.isnan(oi) or np.isnan(iv):
                        continue
                    oi, iv = float(oi), float(iv)
                    if oi <= 0 or iv <= 0.01:
                        continue
                    greeks = calculate_greeks(current_price, k, t_years, iv, rf_rate)
                    if not greeks:
                        continue
                    # Dollar gamma per 1% move, scaled to millions of $.
                    gex = greeks['gamma'] * oi * 100 * current_price ** 2 * 0.01 / 1e6
                    bucket = agg.setdefault(k, {'call': 0.0, 'put': 0.0})
                    bucket[side] += gex
        if not agg:
            return None

        strikes  = sorted(agg.keys())
        call_gex = [agg[k]['call'] for k in strikes]    # dealers long calls  → +
        put_gex  = [-agg[k]['put'] for k in strikes]    # dealers short puts  → −
        net_gex  = [c + p for c, p in zip(call_gex, put_gex)]

        # $5-binned aggregation: collapse minor/weekly strikes into the round
        # institutional levels so a single noisy strike can't masquerade as a
        # wall. Purely a visual view — stats below stay on the precise strikes.
        bin_agg: dict = {}
        for k in strikes:
            slot = bin_agg.setdefault(round(k / 5.0) * 5, {'call': 0.0, 'put': 0.0})
            slot['call'] += agg[k]['call']
            slot['put']  += agg[k]['put']
        bin_strikes = sorted(bin_agg.keys())
        bin_call = [bin_agg[k]['call'] for k in bin_strikes]
        bin_put  = [-bin_agg[k]['put'] for k in bin_strikes]
        bin_net  = [c + p for c, p in zip(bin_call, bin_put)]

        # Cumulative net GEX from the lowest strike up. Its zero crossing is the
        # gamma flip; its slope at a strike is the local gamma density (steep =
        # concentrated/pinning, flat = thin/air-pocket).
        cum     = list(np.cumsum(net_gex))
        bin_cum = list(np.cumsum(bin_net))

        # Walls = largest gamma concentration on the side of spot where it can
        # actually act as resistance/support. A call wall below spot (or put wall
        # above) is meaningless, so constrain by side, falling back to the global
        # extreme only if one side is empty.
        def _wall(vals, want_above, pick):
            sided = [(v, k) for v, k in zip(vals, strikes)
                     if (k >= current_price) == want_above]
            pool  = [(v, k) for v, k in (sided or list(zip(vals, strikes)))
                     if (v > 0 if pick is max else v < 0)]
            return pick(pool)[1] if pool else None

        call_wall = _wall(call_gex, want_above=True,  pick=max)
        put_wall  = _wall(put_gex,  want_above=False, pick=min)

        # Gamma flip: strike where cumulative net GEX crosses zero (linear-
        # interpolated). Below it dealers are net-short gamma (trend-amplifying);
        # above it net-long gamma (mean-reverting / vol-dampening).
        gamma_flip = None
        for i in range(1, len(cum)):
            if (cum[i - 1] <= 0 < cum[i]) or (cum[i - 1] >= 0 > cum[i]):
                x0, x1, y0, y1 = strikes[i - 1], strikes[i], cum[i - 1], cum[i]
                gamma_flip = round(float(x0 + (x1 - x0) * (-y0) / (y1 - y0)), 2) if y1 != y0 else float(x1)
                break

        total_gex = float(np.sum(net_gex))

        # Top concentration nodes: the strikes carrying the most gamma (by |net|),
        # their share of total gross gamma, and the hedging behaviour they impose.
        # The leaderboard a trader reads instead of eyeballing a 100-point axis.
        gross  = sum(abs(v) for v in net_gex) or 1.0
        ranked = sorted(zip(strikes, net_gex, call_gex, put_gex),
                        key=lambda t: abs(t[1]), reverse=True)
        top_nodes = [{
            'strike': round(k, 2),
            'net':    round(n, 1),
            'pct':    round(abs(n) / gross * 100, 1),
            'kind':   'Vol-dampening' if n >= 0 else 'Trend-amplifying',
            'side':   'Call' if abs(c) >= abs(p) else 'Put',
        } for k, n, c, p in ranked[:5]]

        # Symmetric, locked axes so bar proportions reflect real positioning
        # shifts rather than Plotly auto-scaling one side independently.
        bar_max = max([abs(v) for v in call_gex + put_gex + net_gex
                       + bin_call + bin_put + bin_net] or [1.0]) * 1.08
        cum_max = max([abs(v) for v in cum + bin_cum] or [1.0]) * 1.08
        net_color     = ['#10b981' if v >= 0 else '#f43f5e' for v in net_gex]
        bin_net_color = ['#10b981' if v >= 0 else '#f43f5e' for v in bin_net]

        fig = go.Figure()
        # Trace order is load-bearing — the toggle buttons index into it below.
        # 0 call·raw  1 put·raw  2 net·raw  3 cum·raw
        fig.add_trace(go.Bar(y=strikes, x=call_gex, orientation='h', name='Call GEX',
                             marker_color='#10b981', marker_line_width=0, visible=True))
        fig.add_trace(go.Bar(y=strikes, x=put_gex, orientation='h', name='Put GEX',
                             marker_color='#f43f5e', marker_line_width=0, visible=True))
        fig.add_trace(go.Bar(y=strikes, x=net_gex, orientation='h', name='Net GEX',
                             marker_color=net_color, marker_line_width=0, visible=False))
        fig.add_trace(go.Scatter(y=strikes, x=cum, mode='lines', name='Cumulative',
                             xaxis='x2', line=dict(color='#6366f1', width=2, shape='spline'),
                             visible=True))
        # 4 call·$5  5 put·$5  6 net·$5  7 cum·$5
        fig.add_trace(go.Bar(y=bin_strikes, x=bin_call, orientation='h', name='Call GEX',
                             marker_color='#10b981', marker_line_width=0, visible=False))
        fig.add_trace(go.Bar(y=bin_strikes, x=bin_put, orientation='h', name='Put GEX',
                             marker_color='#f43f5e', marker_line_width=0, visible=False))
        fig.add_trace(go.Bar(y=bin_strikes, x=bin_net, orientation='h', name='Net GEX',
                             marker_color=bin_net_color, marker_line_width=0, visible=False))
        fig.add_trace(go.Scatter(y=bin_strikes, x=bin_cum, mode='lines', name='Cumulative',
                             xaxis='x2', line=dict(color='#6366f1', width=2, shape='spline'),
                             visible=False))

        fig.add_hline(y=current_price, line_dash='solid', line_color='#f59e0b',
                      line_width=1.5, annotation_text=f'Spot ${current_price:.2f}',
                      annotation_position='top right')
        if gamma_flip:
            fig.add_hline(y=gamma_flip, line_dash='dash', line_color='#6366f1',
                          line_width=1, annotation_text=f'γ-flip ${gamma_flip}',
                          annotation_position='bottom right')

        def _vis(shown):
            return [i in shown for i in range(8)]
        buttons = [
            dict(label='Call / Put',      method='update', args=[{'visible': _vis({0, 1, 3})}]),
            dict(label='Net',             method='update', args=[{'visible': _vis({2, 3})}]),
            dict(label='Call / Put · $5', method='update', args=[{'visible': _vis({4, 5, 7})}]),
            dict(label='Net · $5',        method='update', args=[{'visible': _vis({6, 7})}]),
        ]

        fig.update_layout(
            barmode='relative',
            xaxis=dict(title='Gamma Exposure ($M per 1% move)', range=[-bar_max, bar_max],
                       zeroline=True, zerolinecolor='rgba(120,120,120,0.45)'),
            xaxis2=dict(overlaying='x', side='top', range=[-cum_max, cum_max],
                        showgrid=False, zeroline=False, tickfont=dict(color='#6366f1', size=9)),
            yaxis_title='Strike ($)',
            template='plotly_white',
            height=460,
            margin=dict(l=60, r=30, t=46, b=78),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', xanchor='center', x=0.5, yanchor='top', y=-0.14),
            bargap=0.12,
            updatemenus=[dict(type='dropdown', direction='down', x=0, y=1.12,
                              xanchor='left', yanchor='bottom', showactive=True,
                              pad=dict(t=2, b=2), font=dict(size=10), buttons=buttons)],
        )
        return {
            'chart': fig.to_html(full_html=False, include_plotlyjs=False),
            'stats': {
                'total_gex': round(total_gex, 1),
                'regime': 'positive' if total_gex >= 0 else 'negative',
                'call_wall': call_wall,
                'put_wall': put_wall,
                'gamma_flip': gamma_flip,
                'top_nodes': top_nodes,
                'dte_range': f"{selected[0][2]}–{selected[-1][2]}d",
                'n_expirations': len(selected),
            },
        }
    except Exception as e:
        print(f"GEX profile failed for {ticker}: {e}")
        return None


def get_insider_chart(ticker: str, price_df: pd.DataFrame) -> str | None:
    """Dual-axis chart: net monthly insider share activity vs. stock price."""
    try:
        raw = yf.Ticker(ticker.upper()).insider_transactions
        if raw is None or (hasattr(raw, 'empty') and raw.empty):
            return None

        idf = raw.copy()
        # Normalise date index
        if hasattr(idf.index, 'tz'):
            idf.index = pd.to_datetime(idf.index).tz_localize(None)
        else:
            idf.index = pd.to_datetime(idf.index)

        # Determine net signed shares
        shares_col = next((c for c in idf.columns if 'share' in c.lower()), None)
        txn_col    = next((c for c in idf.columns
                           if c.lower() in ('transaction', 'text', 'type')), None)
        if shares_col is None:
            return None

        idf['_shares'] = pd.to_numeric(idf[shares_col], errors='coerce').fillna(0)
        if txn_col:
            idf['_signed'] = idf.apply(
                lambda r: r['_shares'] if 'purchase' in str(r[txn_col]).lower()
                          else -r['_shares'],
                axis=1
            )
        else:
            idf['_signed'] = idf['_shares']

        monthly = idf['_signed'].resample('ME').sum()
        monthly = monthly[monthly != 0]
        if monthly.empty:
            return None

        bar_colors = ['#10b981' if v > 0 else '#f43f5e' for v in monthly.values]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=monthly.index, y=monthly.values,
            name='Net Insider Shares',
            marker_color=bar_colors,
            marker_line_width=0,
            yaxis='y',
        ))
        fig.add_trace(go.Scatter(
            x=price_df.index, y=price_df['close'],
            mode='lines', name='Price',
            line=dict(color='#f59e0b', width=1.5),
            yaxis='y2',
        ))
        fig.update_layout(
            yaxis =dict(title='Net Shares (Insider)', side='left'),
            yaxis2=dict(title='Price ($)', side='right',
                        overlaying='y', showgrid=False),
            template='plotly_white',
            height=350,
            margin=dict(l=60, r=60, t=30, b=50),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)
    except Exception as e:
        print(f"Insider chart failed for {ticker}: {e}")
        return None


def get_cumulative_return_chart(ticker: str, df: pd.DataFrame) -> str | None:
    """Normalised cumulative return: ticker vs. SPY vs. QQQ from the stock's earliest date."""
    try:
        spy_df = get_or_fetch_prices('SPY')
        qqq_df = get_or_fetch_prices('QQQ')

        series = {'ticker': df['close'].copy()}
        if spy_df is not None and not spy_df.empty:
            series['SPY'] = spy_df['close'].copy()
        if qqq_df is not None and not qqq_df.empty:
            series['QQQ'] = qqq_df['close'].copy()

        # Align on common dates, start from the earliest date present in all series
        combined = pd.DataFrame(series).dropna()
        if len(combined) < 2:
            return None

        # Normalise to 100 at day-0
        normalised = (combined / combined.iloc[0]) * 100

        palette = {
            'ticker': '#f59e0b',
            'SPY':    '#a1a1aa',
            'QQQ':    '#6366f1',
        }
        names = {
            'ticker': ticker.upper(),
            'SPY':    'SPY',
            'QQQ':    'QQQ',
        }

        fig = go.Figure()
        for key in ['SPY', 'QQQ', 'ticker']:
            if key not in normalised.columns:
                continue
            final_val = round(float(normalised[key].iloc[-1]), 1)
            fig.add_trace(go.Scatter(
                x=normalised.index,
                y=normalised[key],
                mode='lines',
                name=f"{names[key]}  {final_val}",
                line=dict(
                    color=palette[key],
                    width=2.5 if key == 'ticker' else 1.5,
                ),
            ))

        fig.add_hline(y=100, line_dash='dot', line_color='#52525b', line_width=1)
        fig.update_layout(
            yaxis_title='Growth of $100',
            template='plotly_white',
            height=350,
            margin=dict(l=50, r=30, t=30, b=50),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
            hovermode='x unified',
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)
    except Exception as e:
        print(f"Cumulative return chart failed for {ticker}: {e}")
        return None


def get_price_target_chart(ticker: str, current_price: float) -> str | None:
    """Analyst consensus price target gauge via Finnhub."""
    try:
        pt = providers.finnhub_price_target(ticker)
        if not pt:
            return None

        t_low  = pt.get('low')
        t_mean = pt.get('mean')
        t_med  = pt.get('median')
        t_high = pt.get('high')
        if not all(isinstance(v, (int, float)) for v in [t_low, t_mean, t_high]):
            return None

        upside_pct = ((t_mean - current_price) / current_price) * 100
        is_upside  = upside_pct >= 0
        upside_color = '#10b981' if is_upside else '#f43f5e'

        fig = go.Figure()

        # Range band: low → high
        fig.add_shape(type='rect',
            x0=t_low, x1=t_high, y0=0.35, y1=0.65,
            fillcolor='rgba(99,102,241,0.12)',
            line=dict(width=0),
        )
        # Low → high bar spine
        fig.add_trace(go.Scatter(
            x=[t_low, t_high], y=[0.5, 0.5],
            mode='lines',
            line=dict(color='#6366f1', width=3),
            name=f'Target range  ${t_low:.0f} – ${t_high:.0f}',
            showlegend=True,
        ))
        # Mean target diamond
        fig.add_trace(go.Scatter(
            x=[t_mean], y=[0.5],
            mode='markers+text',
            marker=dict(color='#f59e0b', size=16, symbol='diamond',
                        line=dict(color='white', width=1.5)),
            text=[f'${t_mean:.0f}'],
            textposition='top center',
            textfont=dict(size=11, color='#f59e0b'),
            name=f'Mean target  ${t_mean:.2f}',
            showlegend=True,
        ))
        # Median target (smaller)
        if t_med and t_med != t_mean:
            fig.add_trace(go.Scatter(
                x=[t_med], y=[0.5],
                mode='markers',
                marker=dict(color='#a78bfa', size=10, symbol='diamond'),
                name=f'Median  ${t_med:.2f}',
                showlegend=True,
            ))
        # Current price circle
        fig.add_trace(go.Scatter(
            x=[current_price], y=[0.5],
            mode='markers+text',
            marker=dict(color=upside_color, size=16, symbol='circle',
                        line=dict(color='white', width=1.5)),
            text=[f'${current_price:.0f}'],
            textposition='bottom center',
            textfont=dict(size=11, color=upside_color),
            name=f'Current  ${current_price:.2f}',
            showlegend=True,
        ))

        # Upside annotation
        sign = '+' if is_upside else ''
        fig.add_annotation(
            x=(t_mean + current_price) / 2,
            y=0.72,
            text=f'{sign}{upside_pct:.1f}% to mean target',
            showarrow=False,
            font=dict(size=13, color=upside_color),
        )

        # Low / High labels
        for x_val, label in [(t_low, f'Low\n${t_low:.0f}'), (t_high, f'High\n${t_high:.0f}')]:
            fig.add_annotation(
                x=x_val, y=0.28,
                text=label.replace('\n', '<br>'),
                showarrow=False,
                font=dict(size=10, color='#a1a1aa'),
                align='center',
            )

        updated = pt.get('updated', '')
        fig.update_layout(
            xaxis=dict(
                range=[t_low * 0.88, t_high * 1.08],
                showgrid=False, zeroline=False,
                showticklabels=True,
                tickprefix='$',
            ),
            yaxis=dict(range=[0, 1], visible=False),
            template='plotly_white',
            height=280,
            margin=dict(l=20, r=20, t=40, b=60),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='top', y=-0.18, x=0,
                        font=dict(size=10)),
            annotations=fig.layout.annotations + (
                [dict(
                    x=0.5, y=-0.32, xref='paper', yref='paper',
                    text=f'Updated {updated}' if updated else '',
                    showarrow=False,
                    font=dict(size=9, color='#71717a'),
                )]
            ),
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)
    except Exception as e:
        print(f"Price target chart failed for {ticker}: {e}")
        return None


def compute_analytics(ticker: str) -> dict | None:
    """Compute all analytics metrics from cached DB prices."""
    df = get_or_fetch_prices(ticker)
    if df is None or len(df) < 2:
        return None

    df = df.copy()
    df['returns'] = df['close'].pct_change()
    df = df.dropna(subset=['returns'])
    if df.empty:
        return None

    current_price = float(df['close'].iloc[-1])

    # --- Descriptive stats ---
    mean_return  = float(df['returns'].mean())
    std_return   = float(df['returns'].std())
    skewness     = float(df['returns'].skew())
    kurtosis     = float(df['returns'].kurt())

    # --- Return distribution histogram ---
    hist_fig = go.Figure()
    hist_fig.add_trace(go.Histogram(
        x=df['returns'] * 100,
        nbinsx=80,
        name='Daily Returns',
        marker_color='#6366f1',
        opacity=0.8,
    ))
    hist_fig.update_layout(
        title='Daily Return Distribution (%)',
        xaxis_title='Daily Return (%)',
        yaxis_title='Frequency',
        template='plotly_white',
        height=350,
        margin=dict(l=50, r=30, t=50, b=50),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    dist_chart = hist_fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Rolling 30-day volatility (annualised) ---
    df['rolling_vol'] = df['returns'].rolling(30).std() * np.sqrt(252) * 100
    vol_fig = go.Figure()
    vol_fig.add_trace(go.Scatter(
        x=df.index, y=df['rolling_vol'],
        mode='lines', name='30d Vol',
        line=dict(color='#f59e0b', width=1.5),
        fill='tozeroy', fillcolor='rgba(245,158,11,0.1)',
    ))
    vol_fig.update_layout(
        title='Rolling 30-Day Annualised Volatility (%)',
        yaxis_title='Volatility (%)',
        template='plotly_white',
        height=300,
        margin=dict(l=50, r=30, t=50, b=50),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    vol_chart = vol_fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Drawdown ---
    df['cummax'] = df['close'].cummax()
    df['drawdown'] = (df['close'] - df['cummax']) / df['cummax'] * 100
    max_drawdown = float(df['drawdown'].min())
    dd_fig = go.Figure()
    dd_fig.add_trace(go.Scatter(
        x=df.index, y=df['drawdown'],
        mode='lines', name='Drawdown',
        line=dict(color='#ef4444', width=1),
        fill='tozeroy', fillcolor='rgba(239,68,68,0.15)',
    ))
    dd_fig.update_layout(
        title='Drawdown from Rolling Peak (%)',
        yaxis_title='Drawdown (%)',
        template='plotly_white',
        height=300,
        margin=dict(l=50, r=30, t=50, b=50),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    dd_chart = dd_fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Rolling Sharpe (252-day, risk-free ≈ 0) ---
    df['rolling_sharpe'] = (
        df['returns'].rolling(252).mean() /
        df['returns'].rolling(252).std()
    ) * np.sqrt(252)
    sharpe_fig = go.Figure()
    sharpe_fig.add_trace(go.Scatter(
        x=df.index, y=df['rolling_sharpe'],
        mode='lines', name='Sharpe',
        line=dict(color='#10b981', width=1.5),
    ))
    sharpe_fig.add_hline(y=1, line_dash='dash', line_color='#94a3b8',
                         annotation_text='Sharpe = 1')
    sharpe_fig.update_layout(
        title='Rolling 1-Year Sharpe Ratio',
        yaxis_title='Sharpe Ratio',
        template='plotly_white',
        height=300,
        margin=dict(l=50, r=30, t=50, b=50),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    sharpe_chart = sharpe_fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Seasonality: average monthly return ---
    df['month'] = df.index.month
    monthly_avg = df.groupby('month')['returns'].mean() * 100
    month_labels = ['Jan','Feb','Mar','Apr','May','Jun',
                    'Jul','Aug','Sep','Oct','Nov','Dec']
    colors = ['#10b981' if v >= 0 else '#ef4444' for v in monthly_avg.values]
    season_fig = go.Figure()
    season_fig.add_trace(go.Bar(
        x=[month_labels[m - 1] for m in monthly_avg.index],
        y=monthly_avg.values,
        marker_color=colors,
        name='Avg Monthly Return',
    ))
    season_fig.update_layout(
        title='Average Monthly Return (%)',
        yaxis_title='Avg Return (%)',
        template='plotly_white',
        height=300,
        margin=dict(l=50, r=30, t=50, b=50),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    season_chart = season_fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Beta vs SPY ---
    spy_df = get_or_fetch_prices('SPY')
    beta = None
    beta_chart = None
    if spy_df is not None and not spy_df.empty:
        spy_returns = spy_df['close'].pct_change().dropna()
        merged = pd.concat([df['returns'], spy_returns], axis=1, join='inner')
        merged.columns = ['stock', 'spy']
        if len(merged) > 30:
            cov   = merged['stock'].cov(merged['spy'])
            var   = merged['spy'].var()
            beta  = round(cov / var, 3) if var != 0 else None

            beta_fig = go.Figure()
            beta_fig.add_trace(go.Scatter(
                x=merged['spy'] * 100,
                y=merged['stock'] * 100,
                mode='markers',
                marker=dict(color='#6366f1', size=3, opacity=0.5),
                name='Daily returns',
            ))
            if beta is not None:
                x_vals = np.linspace(merged['spy'].min(), merged['spy'].max(), 100)
                y_vals = beta * x_vals + merged['stock'].mean() - beta * merged['spy'].mean()
                beta_fig.add_trace(go.Scatter(
                    x=x_vals * 100, y=y_vals * 100,
                    mode='lines',
                    line=dict(color='#f59e0b', width=2),
                    name=f'β = {beta}',
                ))
            beta_fig.update_layout(
                title=f'Beta vs SPY (β = {beta})',
                xaxis_title='SPY Daily Return (%)',
                yaxis_title=f'{ticker.upper()} Daily Return (%)',
                template='plotly_white',
                height=350,
                margin=dict(l=50, r=30, t=50, b=50),
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
            )
            beta_chart = beta_fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Rolling VaR (95 % & 99 %) + Expected Shortfall ---
    roll_win = 252
    df['var_95'] = df['returns'].rolling(roll_win).quantile(0.05) * 100
    df['var_99'] = df['returns'].rolling(roll_win).quantile(0.01) * 100

    def _rolling_es(series, window, q):
        def _es(arr):
            t = np.percentile(arr, q * 100)
            tail = arr[arr <= t]
            return tail.mean() if len(tail) > 0 else np.nan
        return series.rolling(window).apply(_es, raw=True)

    df['es_95'] = _rolling_es(df['returns'], roll_win, 0.05) * 100

    var_fig = go.Figure()
    var_fig.add_trace(go.Scatter(
        x=df.index, y=df['var_95'].abs(),
        mode='lines', name='VaR 95 %',
        line=dict(color='#f59e0b', width=1.5),
        fill='tozeroy', fillcolor='rgba(245,158,11,0.08)',
    ))
    var_fig.add_trace(go.Scatter(
        x=df.index, y=df['var_99'].abs(),
        mode='lines', name='VaR 99 %',
        line=dict(color='#f43f5e', width=1.5),
        fill='tozeroy', fillcolor='rgba(244,63,94,0.08)',
    ))
    var_fig.add_trace(go.Scatter(
        x=df.index, y=df['es_95'].abs(),
        mode='lines', name='ES 95 %',
        line=dict(color='#6366f1', width=1.5, dash='dash'),
    ))
    var_fig.update_layout(
        title='Rolling 1-Year Daily VaR & Expected Shortfall',
        yaxis_title='Potential Daily Loss (%)',
        template='plotly_white',
        height=320,
        margin=dict(l=50, r=30, t=50, b=50),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        legend=dict(orientation='h', yanchor='bottom', y=1.02),
    )
    var_chart = var_fig.to_html(full_html=False, include_plotlyjs=False)

    # Current VaR figures (latest rolling window)
    latest_var_95 = round(float(df['var_95'].dropna().iloc[-1]), 3) if not df['var_95'].dropna().empty else None
    latest_var_99 = round(float(df['var_99'].dropna().iloc[-1]), 3) if not df['var_99'].dropna().empty else None

    # --- Volume Profile (last 2 years) ---
    vp_cutoff = df.index[-1] - pd.DateOffset(years=2)
    vp_df = df[df.index >= vp_cutoff].copy()
    vp_chart = None
    if len(vp_df) > 30:
        price_min, price_max = vp_df['low'].min(), vp_df['high'].max()
        if price_min == price_max:
            poc_price = None
        else:
            n_bins = 50
            bins = np.linspace(price_min, price_max, n_bins + 1)
            bin_centers = (bins[:-1] + bins[1:]) / 2
            vp_df['_bin'] = pd.cut(vp_df['close'], bins=bins, labels=False)
            vol_by_price = vp_df.groupby('_bin')['volume'].sum().reindex(range(n_bins), fill_value=0)
            vp_colors = [
                '#f59e0b' if bc >= current_price else '#6366f1'
                for bc in bin_centers
            ]
            poc_bin = int(vol_by_price.idxmax())
            poc_price = round(float(bin_centers[poc_bin]), 2)

            vp_fig = go.Figure()
            vp_fig.add_trace(go.Bar(
                x=vol_by_price.values,
                y=bin_centers,
                orientation='h',
                marker_color=vp_colors,
                marker_line_width=0,
                name='Volume at Price',
            ))
            vp_fig.add_hline(y=current_price, line_dash='solid', line_color='#f59e0b',
                             line_width=1.5, annotation_text='Current',
                             annotation_position='top right')
            vp_fig.add_hline(y=poc_price, line_dash='dash', line_color='#6366f1',
                             line_width=1, annotation_text=f'POC ${poc_price}',
                             annotation_position='bottom right')
            vp_fig.update_layout(
                yaxis_title='Price ($)',
                xaxis_title='Cumulative Volume (2 yr)',
                template='plotly_white',
                height=420,
                margin=dict(l=60, r=30, t=30, b=50),
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                showlegend=False,
            )
            vp_chart = vp_fig.to_html(full_html=False, include_plotlyjs=False)
    else:
        poc_price = None

    # --- Forward estimates (Monte Carlo at multiple horizons) ---
    # Projects prices at 3-month, 6-month, and 1-year horizons using GBM
    # calibrated to the stock's own historical drift and volatility.
    def _mc_forward_estimate(current, mu, sigma, horizon_days, n_sims=1000, seed=42):
        """Compute forward price estimate at horizon_days using Monte Carlo GBM.
        Returns: (median, p5, p25, p75, p95, prob_gain, prob_up20, prob_dn20)"""
        rng = np.random.default_rng(seed)
        shocks = rng.normal(mu, sigma, size=(n_sims, horizon_days))
        paths = current * np.exp(np.cumsum(shocks, axis=1))
        terminal = paths[:, -1]
        return {
            'median': round(float(np.median(terminal)), 2),
            'p5': round(float(np.percentile(terminal, 5)), 2),
            'p25': round(float(np.percentile(terminal, 25)), 2),
            'p75': round(float(np.percentile(terminal, 75)), 2),
            'p95': round(float(np.percentile(terminal, 95)), 2),
            'prob_gain': round(float((terminal > current).mean() * 100), 1),
            'prob_up20': round(float((terminal > current * 1.2).mean() * 100), 1),
            'prob_dn20': round(float((terminal < current * 0.8).mean() * 100), 1),
        }

    forward_estimates = {}
    mc_chart = None
    mc_stats = {}
    log_ret = np.log(df['close'] / df['close'].shift(1)).dropna()
    if len(log_ret) > 30:
        mu = float(log_ret.mean())
        sigma = float(log_ret.std())

        # Calculate estimates at multiple horizons
        forward_estimates['3m'] = _mc_forward_estimate(current_price, mu, sigma, 63)   # ~3 months
        forward_estimates['6m'] = _mc_forward_estimate(current_price, mu, sigma, 126)  # ~6 months
        forward_estimates['1y'] = _mc_forward_estimate(current_price, mu, sigma, 252)  # ~1 year
        mc_stats = forward_estimates['1y'].copy()

        # Full 1-year projection chart
        horizon = 252
        n_sims = 1000
        rng = np.random.default_rng(42)
        shocks = rng.normal(mu, sigma, size=(n_sims, horizon))
        paths = current_price * np.exp(np.cumsum(shocks, axis=1))
        paths = np.hstack([np.full((n_sims, 1), current_price), paths])

        pct = np.percentile(paths, [5, 25, 50, 75, 95], axis=0)
        future_dates = pd.bdate_range(df.index[-1], periods=horizon + 1)

        mc_fig = go.Figure()
        # 5–95% band
        mc_fig.add_trace(go.Scatter(x=future_dates, y=pct[4], mode='lines',
            line=dict(width=0), showlegend=False, hoverinfo='skip'))
        mc_fig.add_trace(go.Scatter(x=future_dates, y=pct[0], mode='lines',
            line=dict(width=0), fill='tonexty', fillcolor='rgba(99,102,241,0.12)',
            name='5–95%', hoverinfo='skip'))
        # 25–75% band
        mc_fig.add_trace(go.Scatter(x=future_dates, y=pct[3], mode='lines',
            line=dict(width=0), showlegend=False, hoverinfo='skip'))
        mc_fig.add_trace(go.Scatter(x=future_dates, y=pct[1], mode='lines',
            line=dict(width=0), fill='tonexty', fillcolor='rgba(99,102,241,0.28)',
            name='25–75%', hoverinfo='skip'))
        # Median path
        mc_fig.add_trace(go.Scatter(x=future_dates, y=pct[2], mode='lines',
            line=dict(color='#f59e0b', width=2), name='Median'))
        # Mark key milestones
        date_3m = pd.bdate_range(df.index[-1], periods=64)[-1]
        date_6m = pd.bdate_range(df.index[-1], periods=127)[-1]
        for date, label, color in [(date_3m, '3m', '#6366f1'), (date_6m, '6m', '#8b5cf6')]:
            if date < future_dates[-1]:
                mc_fig.add_vline(x=date, line_dash='dash', line_color=color, line_width=1,
                                annotation_text=label, annotation_position='top')
        # Current price reference
        mc_fig.add_hline(y=current_price, line_dash='dot', line_color='#71717a',
                         line_width=1, annotation_text='Today',
                         annotation_position='bottom left',
                         annotation_font_size=10)
        mc_fig.update_layout(
            title='Monte Carlo Forward Projection (1 yr, 1000 paths)',
            yaxis_title='Simulated Price ($)',
            template='plotly_white',
            height=350,
            margin=dict(l=50, r=30, t=50, b=40),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0,
                        font=dict(size=10)),
            hovermode='x unified',
        )
        mc_chart = mc_fig.to_html(full_html=False, include_plotlyjs=False)

    return {
        'ticker': ticker.upper(),
        'current_price': current_price,
        'stats': {
            'mean_daily_return': round(mean_return * 100, 4),
            'daily_std': round(std_return * 100, 4),
            'annualised_vol': round(std_return * np.sqrt(252) * 100, 2),
            'skewness': round(skewness, 3),
            'kurtosis': round(kurtosis, 3),
            'max_drawdown': round(max_drawdown, 2),
            'beta': beta,
            'data_points': len(df),
            'var_95': latest_var_95,
            'var_99': latest_var_99,
            'poc_price': poc_price,
            'mc_median':    mc_stats.get('median'),
            'mc_p5':        mc_stats.get('p5'),
            'mc_p95':       mc_stats.get('p95'),
            'mc_prob_gain': mc_stats.get('prob_gain'),
            'mc_prob_up20': mc_stats.get('prob_up20'),
            'mc_prob_dn20': mc_stats.get('prob_dn20'),
        },
        'forward_estimates': forward_estimates,
        'charts': {
            'distribution': dist_chart,
            'volatility': vol_chart,
            'drawdown': dd_chart,
            'sharpe': sharpe_chart,
            'seasonality': season_chart,
            'beta': beta_chart,
            'var': var_chart,
            'volume_profile': vp_chart,
            'monte_carlo': mc_chart,
        }
    }


@app.route('/analytics')
def analytics_page():
    ticker = request.args.get('ticker', '').strip().upper()
    if not ticker:
        return render_template('analytics.html', data=None, ticker='')
    data = compute_analytics(ticker)
    if data is None:
        return render_template('analytics.html', data=None, ticker=ticker,
                               error=f"Could not retrieve data for {ticker}")

    # Attach fundamentals
    data['fundamentals'] = get_fundamentals(ticker)

    # Attach options smile
    data['charts']['options_smile'] = get_options_smile(ticker, data['current_price'])

    # Attach dealer Gamma Exposure (GEX) profile
    gex = get_gex_profile(ticker, data['current_price'])
    data['charts']['gex'] = gex['chart'] if gex else None
    data['gex'] = gex['stats'] if gex else None

    # Attach insider chart (needs price df)
    price_df = get_or_fetch_prices(ticker)
    if price_df is not None:
        data['charts']['insider'] = get_insider_chart(ticker, price_df)
        data['charts']['cumulative_return'] = get_cumulative_return_chart(ticker, price_df)

    # Attach analyst price target
    data['charts']['price_target'] = get_price_target_chart(ticker, data['current_price'])

    # Machine-learning Buy/Hold/Sell signal (None until a model is trained)
    data['ml'] = ml.predict(ticker)

    # AI analyst report — reads everything above, including the ML signal
    data['ai_report'] = ai.generate_report(ticker, data)

    return render_template('analytics.html', data=data, ticker=ticker)


@app.route('/api/analytics/<ticker>')
def analytics_api(ticker):
    data = compute_analytics(ticker)
    if data is None:
        return jsonify({"error": f"Could not retrieve data for {ticker}"}), 404
    # strip chart HTML from JSON response — charts are for the template only
    data.pop('charts', None)
    return jsonify(data)


def _insider_sentiment_chart(sentiment):
    """Bar chart of monthly insider MSPR (green = net buying, red = net selling)."""
    points = [s for s in sentiment if isinstance(s.get('mspr'), (int, float))]
    if not points:
        return None
    points = points[-18:]
    labels = [f"{s['year']}-{str(s['month']).zfill(2)}" for s in points]
    values = [s['mspr'] for s in points]
    colors = ['#10b981' if v >= 0 else '#ef4444' for v in values]
    fig = go.Figure(go.Bar(x=labels, y=values, marker_color=colors))
    fig.update_layout(
        title='Insider Sentiment (MSPR by month)',
        yaxis_title='MSPR',
        template='plotly_white',
        height=300,
        margin=dict(l=50, r=30, t=50, b=50),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _institutional_holders_chart(holders):
    """Horizontal bar of top institutional holders by shares held."""
    if not holders:
        return None
    top = holders[:10][::-1]
    fig = go.Figure(go.Bar(
        x=[h['shares'] for h in top],
        y=[h['holder'] for h in top],
        orientation='h',
        marker_color='#6366f1',
    ))
    fig.update_layout(
        title='Top Institutional Holders (shares)',
        template='plotly_white',
        height=380,
        margin=dict(l=10, r=30, t=50, b=40),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        yaxis=dict(automargin=True),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def compute_positioning(ticker: str) -> dict:
    """Assemble market-positioning data from all available free providers.

    Always returns a dict (never None) so the page renders even when every
    provider is unconfigured — each panel reports its own availability.
    """
    symbol = ticker.upper()
    cfg = providers.configured()

    # These provider calls are independent network requests; run them concurrently
    # so a cold-cache positioning load is bounded by the slowest call, not their sum.
    tasks = {
        'valuation':       lambda: providers.finnhub_metrics(symbol),
        'recommendations': lambda: providers.finnhub_recommendations(symbol),
        'sentiment':       lambda: providers.finnhub_insider_sentiment(symbol),
        'transactions':    lambda: providers.finnhub_insider_transactions(symbol),
        'sec_filings':     lambda: providers.sec_recent_filings(symbol, forms=("3", "4", "5")),
        'holders':         lambda: providers.fmp_institutional_holders(symbol),
        'sec_13f':         lambda: providers.sec_recent_filings(symbol, forms=("13F-HR", "13F-HR/A")),
    }
    results = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in futures:
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                print(f"positioning task {name} failed for {symbol}: {e}")
                results[name] = None

    valuation = results['valuation']
    recommendations = results['recommendations']
    sentiment = results['sentiment']
    transactions = results['transactions']
    sec_filings = results['sec_filings']
    insider = {
        'sentiment': sentiment,
        'transactions': transactions,
        'sec_filings': sec_filings,
        'chart': _insider_sentiment_chart(sentiment) if sentiment else None,
    }

    holders = results['holders']
    sec_13f = results['sec_13f']
    institutional = {
        'holders': holders,
        'sec_filings': sec_13f,
        'chart': _institutional_holders_chart(holders) if holders else None,
    }

    return {
        'ticker': symbol,
        'configured': cfg,
        'valuation': valuation,
        'recommendations': recommendations,
        'insider': insider,
        'institutional': institutional,
    }


@app.route('/positioning')
def positioning_page():
    ticker = request.args.get('ticker', '').strip().upper()
    if not ticker:
        return render_template('positioning.html', data=None, ticker='')
    data = compute_positioning(ticker)
    return render_template('positioning.html', data=data, ticker=ticker)


@app.route('/api/positioning/<ticker>')
def positioning_api(ticker):
    data = compute_positioning(ticker)
    data['insider'].pop('chart', None)
    data['institutional'].pop('chart', None)
    return jsonify(data)


def normal_cdf(x):
    """Cumulative distribution function of standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_pdf(x):
    """Probability density function of standard normal distribution."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calculate_greeks(s, k, t, v, r=0.045):
    """Calculate Black-Scholes option pricing and greeks."""
    if t <= 0:
        t = 1e-5
    if v <= 0:
        v = 1e-5
    try:
        d1 = (math.log(s / k) + (r + 0.5 * v * v) * t) / (v * math.sqrt(t))
        d2 = d1 - v * math.sqrt(t)
        
        pdf_d1 = normal_pdf(d1)
        cdf_d1 = normal_cdf(d1)
        cdf_d2 = normal_cdf(d2)
        
        cdf_minus_d1 = normal_cdf(-d1)
        cdf_minus_d2 = normal_cdf(-d2)
        
        # Call Greeks
        call_delta = cdf_d1
        call_theta = (-(s * pdf_d1 * v) / (2 * math.sqrt(t)) - r * k * math.exp(-r * t) * cdf_d2) / 365.0
        call_rho = (k * t * math.exp(-r * t) * cdf_d2) / 100.0
        
        # Put Greeks
        put_delta = cdf_d1 - 1.0
        put_theta = (-(s * pdf_d1 * v) / (2 * math.sqrt(t)) + r * k * math.exp(-r * t) * cdf_minus_d2) / 365.0
        put_rho = (-k * t * math.exp(-r * t) * cdf_minus_d2) / 100.0
        
        # Common Greeks
        gamma = pdf_d1 / (s * v * math.sqrt(t))
        vega = (s * math.sqrt(t) * pdf_d1) / 100.0
        
        return {
            'call_delta': round(call_delta, 4),
            'call_theta': round(call_theta, 4),
            'call_rho': round(call_rho, 4),
            'put_delta': round(put_delta, 4),
            'put_theta': round(put_theta, 4),
            'put_rho': round(put_rho, 4),
            'gamma': round(gamma, 4),
            'vega': round(vega, 4),
        }
    except Exception as e:
        print(f"Error calculating greeks: {e}")
        return None


def get_options_greeks_data(ticker, expiration_date=None, rf_rate=0.045):
    """Retrieve option chain and compute Black-Scholes Greeks for strikes around the spot price."""
    try:
        stock = yf.Ticker(ticker.upper())
        expirations = stock.options
        if not expirations:
            return None
        
        if not expiration_date or expiration_date not in expirations:
            expiration_date = expirations[0]
            
        chain = stock.option_chain(expiration_date)
        calls = chain.calls.copy() if hasattr(chain, 'calls') else pd.DataFrame()
        puts = chain.puts.copy() if hasattr(chain, 'puts') else pd.DataFrame()
        
        # Determine spot price
        hist = stock.history(period="1d")
        if hist.empty:
            df = get_or_fetch_prices(ticker)
            if df is not None and not df.empty:
                spot_price = float(df['close'].iloc[-1])
            else:
                spot_price = None
        else:
            spot_price = float(hist['Close'].iloc[-1])
            
        if not spot_price:
            return None
            
        exp_dt = datetime.strptime(expiration_date, '%Y-%m-%d')
        today = datetime.now()
        days_to_exp = (exp_dt - today).days + 1
        t_years = max(1e-5, days_to_exp / 365.0)
        
        lower_bound = spot_price * 0.70
        upper_bound = spot_price * 1.30
        
        call_strikes = calls['strike'].tolist() if not calls.empty else []
        put_strikes = puts['strike'].tolist() if not puts.empty else []
        all_strikes = sorted(list(set(call_strikes + put_strikes)))
        filtered_strikes = [s for s in all_strikes if lower_bound <= s <= upper_bound]
        
        call_dict = calls.set_index('strike').to_dict('index') if not calls.empty else {}
        put_dict = puts.set_index('strike').to_dict('index') if not puts.empty else {}
        
        rows = []
        for strike in filtered_strikes:
            c_opt = call_dict.get(strike, {})
            p_opt = put_dict.get(strike, {})
            
            c_iv = c_opt.get('impliedVolatility', 0)
            p_iv = p_opt.get('impliedVolatility', 0)
            
            # Avoid invalid values
            c_iv = c_iv if (c_iv and not np.isnan(c_iv)) else 0
            p_iv = p_iv if (p_iv and not np.isnan(p_iv)) else 0
            
            # Cross-IV fallback: if one side is missing IV but the other side has it,
            # use the other side's IV for Greeks computation (Put-Call parity / arbitrage alignment)
            c_iv_calc = c_iv
            p_iv_calc = p_iv
            if c_iv <= 0.01 and p_iv > 0.01:
                c_iv_calc = p_iv
            if p_iv <= 0.01 and c_iv > 0.01:
                p_iv_calc = c_iv
            
            c_greeks = calculate_greeks(spot_price, strike, t_years, c_iv_calc, rf_rate) if c_iv_calc > 0.01 else None
            p_greeks = calculate_greeks(spot_price, strike, t_years, p_iv_calc, rf_rate) if p_iv_calc > 0.01 else None
            
            c_bid = c_opt.get('bid', 0)
            c_ask = c_opt.get('ask', 0)
            c_last = c_opt.get('lastPrice', 0)
            
            p_bid = p_opt.get('bid', 0)
            p_ask = p_opt.get('ask', 0)
            p_last = p_opt.get('lastPrice', 0)
            
            rows.append({
                'strike': strike,
                'call_bid': c_bid if not np.isnan(c_bid) else 0,
                'call_ask': c_ask if not np.isnan(c_ask) else 0,
                'call_last': c_last if not np.isnan(c_last) else 0,
                'call_volume': int(c_opt.get('volume', 0)) if (c_opt.get('volume') is not None and not np.isnan(c_opt.get('volume', 0))) else 0,
                'call_oi': int(c_opt.get('openInterest', 0)) if (c_opt.get('openInterest') is not None and not np.isnan(c_opt.get('openInterest', 0))) else 0,
                'call_iv': round(c_iv * 100, 2),
                'call_delta': c_greeks['call_delta'] if c_greeks else 'N/A',
                'call_gamma': c_greeks['gamma'] if c_greeks else 'N/A',
                'call_theta': c_greeks['call_theta'] if c_greeks else 'N/A',
                'call_vega': c_greeks['vega'] if c_greeks else 'N/A',
                'call_rho': c_greeks['call_rho'] if c_greeks else 'N/A',
                'put_bid': p_bid if not np.isnan(p_bid) else 0,
                'put_ask': p_ask if not np.isnan(p_ask) else 0,
                'put_last': p_last if not np.isnan(p_last) else 0,
                'put_volume': int(p_opt.get('volume', 0)) if (p_opt.get('volume') is not None and not np.isnan(p_opt.get('volume', 0))) else 0,
                'put_oi': int(p_opt.get('openInterest', 0)) if (p_opt.get('openInterest') is not None and not np.isnan(p_opt.get('openInterest', 0))) else 0,
                'put_iv': round(p_iv * 100, 2),
                'put_delta': p_greeks['put_delta'] if p_greeks else 'N/A',
                'put_gamma': p_greeks['gamma'] if p_greeks else 'N/A',
                'put_theta': p_greeks['put_theta'] if p_greeks else 'N/A',
                'put_vega': p_greeks['vega'] if p_greeks else 'N/A',
                'put_rho': p_greeks['put_rho'] if p_greeks else 'N/A',
            })
            
        return {
            'ticker': ticker.upper(),
            'expirations': expirations,
            'selected_expiration': expiration_date,
            'spot_price': spot_price,
            'days_to_expiration': days_to_exp,
            'options': rows
        }
    except Exception as e:
        print(f"Error compiling options greeks: {e}")
        return None


@app.route('/live')
def live_page():
    ticker = request.args.get('ticker', '').strip().upper()
    return render_template('live.html', ticker=ticker)


@app.route('/api/config')
def api_config():
    key = providers.active_finnhub_key()
    return jsonify({
        "finnhub_key": key,
        "has_finnhub": bool(key),
    })


@app.route('/momentum')
def momentum_page():
    COST_BPS = 7.5
    tab = request.args.get('tab', 'universe')
    symbol = request.args.get('symbol', '').upper().strip()
    period = request.args.get('period', 'all')

    # 1. Fetch all symbols in database
    with db.get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT symbol FROM daily_prices").fetchall()
    symbols = [r["symbol"] for r in rows if r["symbol"] not in ["SPY", "QQQ"]]
    if not symbols:
        return render_template('momentum.html', data=None, error="No stock price data available in the database. Please visit the homepage and search for tickers first.")

    # Sort symbols for the dropdown list
    available_symbols = sorted(symbols)

    if tab == 'ticker':
        if not symbol:
            # Default to the first symbol if none searched
            symbol = available_symbols[0] if available_symbols else ""
            
        if symbol not in available_symbols:
            return render_template(
                'momentum.html',
                data=None,
                tab='ticker',
                available_symbols=available_symbols,
                searched_symbol=symbol,
                error=f"Ticker '{symbol}' is not currently cached in the database. Please search for it on the homepage first to download its history."
            )

        # Load ticker price data
        df = db.get_prices(symbol)
        if df is None or len(df) < 273:  # 252 + 21
            return render_template(
                'momentum.html',
                data=None,
                tab='ticker',
                available_symbols=available_symbols,
                searched_symbol=symbol,
                error=f"Ticker '{symbol}' has insufficient price history (need at least 273 trading days)."
            )

        close = df['close']
        daily_rets = close.pct_change()
        
        # Momentum score metrics
        latest_price = float(close.iloc[-1])
        
        # Compute scores at various horizons
        # 12-1 momentum: 252 days ago to 21 days ago
        p_latest = close.iloc[-1]
        p_21 = close.iloc[-22] if len(close) >= 22 else close.iloc[0]
        p_63 = close.iloc[-64] if len(close) >= 64 else close.iloc[0]
        p_126 = close.iloc[-127] if len(close) >= 127 else close.iloc[0]
        p_252 = close.iloc[-253] if len(close) >= 253 else close.iloc[0]
        
        mom_12_1 = (p_21 - p_252) / p_252 if p_252 > 0 else 0.0
        mom_6m = (p_latest - p_126) / p_126 if p_126 > 0 else 0.0
        mom_3m = (p_latest - p_63) / p_63 if p_63 > 0 else 0.0
        mom_1m = (p_latest - p_21) / p_21 if p_21 > 0 else 0.0

        # Calculate rank in universe
        universe_scores = {}
        for s in symbols:
            s_df = db.get_prices(s)
            if s_df is not None and len(s_df) >= 253:
                p_past_s = s_df['close'].iloc[-253]
                p_recent_s = s_df['close'].iloc[-22]
                if p_past_s > 0:
                    universe_scores[s] = (p_recent_s - p_past_s) / p_past_s
        sorted_univ = sorted(universe_scores.items(), key=lambda x: x[1], reverse=True)
        univ_ranks = {s: idx + 1 for idx, (s, _) in enumerate(sorted_univ)}
        rank = univ_ranks.get(symbol, len(symbols))

        # Time-Series Momentum Backtest
        # Signal: Long if 12-1 momentum is positive, cash (0) otherwise
        # Rolling 12-1 momentum score at each day:
        roll_score = close.shift(21) / close.shift(252) - 1
        signal = np.where(roll_score > 0, 1.0, 0.0)
        signal = pd.Series(signal, index=df.index).shift(1).fillna(0.0)
        
        trades = signal.diff().abs().fillna(0.0)
        cost_bps = COST_BPS / 1e4
        strat_rets = signal * daily_rets - trades * cost_bps
        
        # Start backtest from index 253
        backtest_dates = df.index[253:]
        strat_series = strat_rets.iloc[253:]
        hold_series = daily_rets.iloc[253:]
        trade_signals = signal.iloc[253:]
        
        # Apply timeframe filter if requested
        if period != 'all':
            latest_date = df.index[-1]
            if period == '3y':
                start_cutoff = latest_date - pd.DateOffset(years=3)
            elif period == '1y':
                start_cutoff = latest_date - pd.DateOffset(years=1)
            elif period == '6m':
                start_cutoff = latest_date - pd.DateOffset(months=6)
            elif period == '3m':
                start_cutoff = latest_date - pd.DateOffset(months=3)
            else:
                start_cutoff = backtest_dates[0]
                
            mask = backtest_dates >= start_cutoff
            if mask.any() and mask.sum() >= 10:
                backtest_dates = backtest_dates[mask]
                strat_series = strat_series[mask]
                hold_series = hold_series[mask]
                trade_signals = trade_signals[mask]
        
        # Compute performance stats
        def get_stats(series):
            cum = (1 + series).prod() - 1
            ann_ret = (1 + series.mean()) ** 252 - 1
            ann_vol = series.std() * np.sqrt(252)
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
            
            # Drawdown
            cum_prod = (1 + series).cumprod()
            running_max = cum_prod.cummax()
            drawdown = (cum_prod - running_max) / (running_max + 1e-8)
            max_dd = drawdown.min()
            return {
                "total_return": round(cum * 100, 1),
                "annual_return": round(ann_ret * 100, 1),
                "volatility": round(ann_vol * 100, 1),
                "sharpe": round(sharpe, 2),
                "max_dd": round(max_dd * 100, 1),
            }
            
        strat_stats = get_stats(strat_series)
        hold_stats = get_stats(hold_series)
        
        # Plotly chart: Strategy vs Buy & Hold
        cum_strat = (1 + strat_series).cumprod() * 10000
        cum_hold = (1 + hold_series).cumprod() * 10000
        
        # Convert index to string for guaranteed clean parsing in Plotly
        backtest_dates_str = backtest_dates.strftime('%Y-%m-%d').tolist()
        
        trade_dates = backtest_dates
        sig_diff = trade_signals.diff().fillna(0.0)
        
        # Entries: signal changes from 0 to 1
        buys = sig_diff == 1
        # Exits: signal changes from 1 to 0
        sells = sig_diff == -1
        
        buy_dates = trade_dates[buys]
        sell_dates = trade_dates[sells]
        
        # Calculate individual trade returns
        trade_records = []
        in_trade = False
        entry_idx = 0
        backtest_start_idx = df.index.get_loc(backtest_dates[0])
        
        for idx in range(len(trade_signals)):
            sig = trade_signals.iloc[idx]
            if sig == 1 and not in_trade:
                in_trade = True
                entry_idx = idx
            elif sig == 0 and in_trade:
                in_trade = False
                ret_val = close.iloc[backtest_start_idx + idx] / close.iloc[backtest_start_idx + entry_idx] - 1 - cost_bps * 2
                trade_records.append(ret_val)
                
        if in_trade:
            ret_val = close.iloc[-1] / close.iloc[backtest_start_idx + entry_idx] - 1 - cost_bps
            trade_records.append(ret_val)
            
        trade_count = len(trade_records)
        wins = [r for r in trade_records if r > 0]
        losses = [r for r in trade_records if r <= 0]
        
        win_rate = round(len(wins) / trade_count * 100, 1) if trade_count > 0 else 0.0
        profit_factor = round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0.0 else (99.0 if wins else 0.0)
        
        fig_perf = go.Figure()
        fig_perf.add_trace(go.Scatter(x=backtest_dates_str, y=cum_strat.tolist(), mode='lines', name='Trend-Following (Long/Cash)', line=dict(color='#fbbf24', width=2)))
        fig_perf.add_trace(go.Scatter(x=backtest_dates_str, y=cum_hold.tolist(), mode='lines', name=f'Buy & Hold {symbol}', line=dict(color='#64748b', width=1.5, dash='dash')))
        
        # Add Buy entry markers on the performance chart
        if not buy_dates.empty:
            buy_dates_str = buy_dates.strftime('%Y-%m-%d').tolist()
            buy_prices = cum_strat.loc[buy_dates].tolist()
            fig_perf.add_trace(go.Scatter(
                x=buy_dates_str, y=buy_prices,
                mode='markers',
                marker=dict(symbol='triangle-up', size=10, color='#10b981', line=dict(width=1, color='black')),
                name='Buy Entry'
            ))
            
        # Add Sell exit markers on the performance chart
        if not sell_dates.empty:
            sell_dates_str = sell_dates.strftime('%Y-%m-%d').tolist()
            sell_prices = cum_strat.loc[sell_dates].tolist()
            fig_perf.add_trace(go.Scatter(
                x=sell_dates_str, y=sell_prices,
                mode='markers',
                marker=dict(symbol='triangle-down', size=10, color='#ef4444', line=dict(width=1, color='black')),
                name='Sell Exit'
            ))
            
        fig_perf.update_layout(
            title=f'Trend-Following Strategy vs Buy & Hold for {symbol}',
            xaxis_title='Date',
            yaxis_title='Portfolio Value ($)',
            template='plotly_white',
            height=350,
            margin=dict(l=50, r=30, t=60, b=80),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            showlegend=True,
            legend=dict(orientation='h', yanchor='top', y=-0.18, xanchor='center', x=0.5),
            font=dict(family='Inter, sans-serif')
        )
        perf_chart_html = fig_perf.to_html(full_html=False, include_plotlyjs=False)
        
        # Plotly chart: Drawdowns comparison
        dd_strat = (cum_strat - cum_strat.cummax()) / cum_strat.cummax() * 100
        dd_hold = (cum_hold - cum_hold.cummax()) / cum_hold.cummax() * 100
        
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(x=backtest_dates_str, y=dd_strat.tolist(), mode='lines', name='Trend-Following DD', line=dict(color='#fbbf24', width=1.5), fill='tozeroy', fillcolor='rgba(251,191,36,0.1)'))
        fig_dd.add_trace(go.Scatter(x=backtest_dates_str, y=dd_hold.tolist(), mode='lines', name=f'{symbol} DD', line=dict(color='#ef4444', width=1, dash='dash'), fill='tozeroy', fillcolor='rgba(239,68,68,0.15)'))
        
        fig_dd.update_layout(
            title='Drawdown Comparison (%)',
            xaxis_title='Date',
            yaxis_title='Drawdown (%)',
            template='plotly_white',
            height=250,
            margin=dict(l=50, r=30, t=60, b=80),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            showlegend=True,
            legend=dict(orientation='h', yanchor='top', y=-0.22, xanchor='center', x=0.5),
            font=dict(family='Inter, sans-serif')
        )
        dd_chart_html = fig_dd.to_html(full_html=False, include_plotlyjs=False)
        
        # Plotly chart: Rolling 12-1 Momentum Score
        valid_score = roll_score.iloc[253:] * 100
        roll_dates = df.index[253:]
        if period != 'all':
            mask_roll = roll_dates >= start_cutoff
            if mask_roll.any():
                roll_dates = roll_dates[mask_roll]
                valid_score = valid_score[mask_roll]
        
        fig_roll = go.Figure()
        fig_roll.add_trace(go.Scatter(x=roll_dates.strftime('%Y-%m-%d').tolist(), y=valid_score.tolist(), mode='lines', name='12-1 Momentum %', line=dict(color='#818cf8', width=1.5)))
        fig_roll.add_hline(
            y=0,
            line_dash='dash',
            line_color='#ef4444',
            line_width=1,
            annotation_text="Zero Threshold (Trend Switch)",
            annotation_position="bottom right",
            annotation_font=dict(size=10, color='#71717a')
        )
        
        fig_roll.update_layout(
            title=f'Rolling 12-1 Momentum Score (%) for {symbol}',
            xaxis_title='Date',
            yaxis_title='Momentum Score (%)',
            template='plotly_white',
            height=280,
            margin=dict(l=50, r=30, t=50, b=50),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            showlegend=False,
            font=dict(family='Inter, sans-serif')
        )
        roll_chart_html = fig_roll.to_html(full_html=False, include_plotlyjs=False)
        
        # Determine current trend state
        current_score = roll_score.iloc[-1]
        trend_state = "BULLISH (Long)" if current_score > 0 else "BEARISH (Flat/Cash)"
        trend_color = "text-emerald-500" if current_score > 0 else "text-rose-500"
        
        data = {
            "symbol": symbol,
            "latest_price": latest_price,
            "mom_12_1": round(mom_12_1 * 100, 2),
            "mom_6m": round(mom_6m * 100, 2),
            "mom_3m": round(mom_3m * 100, 2),
            "mom_1m": round(mom_1m * 100, 2),
            "rank": rank,
            "total_rank_count": len(universe_scores),
            "trend_state": trend_state,
            "trend_color": trend_color,
            "strat_stats": strat_stats,
            "hold_stats": hold_stats,
            "perf_chart_html": perf_chart_html,
            "dd_chart_html": dd_chart_html,
            "roll_chart_html": roll_chart_html,
            "trade_count": trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "start_date": backtest_dates[0].strftime('%Y-%m-%d'),
            "end_date": backtest_dates[-1].strftime('%Y-%m-%d')
        }
        
        return render_template(
            'momentum.html',
            data=data,
            tab='ticker',
            available_symbols=available_symbols,
            searched_symbol=symbol,
            period=period,
            error=None
        )

    # ELSE: Tab == 'universe'
    # 2. Load close prices for these symbols
    prices = {}
    for t in symbols + ["SPY", "QQQ"]:
        df = db.get_prices(t)
        if df is not None:
            prices[t] = df["close"]
    
    if not prices:
        return render_template('momentum.html', data=None, error="Failed to load price data.")

    price_df = pd.DataFrame(prices)
    
    # 3. Calculate 12-1 momentum scores for active tickers
    momentum_lookback = 252
    exclude_days = 21
    
    if len(price_df) < momentum_lookback + 2:
        return render_template('momentum.html', data=None, error=f"Insufficient history in database. Need at least {momentum_lookback} daily bars.")
        
    scores = {}
    latest_idx = len(price_df) - 1
    recent_idx = latest_idx - exclude_days
    past_idx = latest_idx - momentum_lookback
    
    for t in symbols:
        if t in price_df.columns:
            p_past = price_df[t].iloc[past_idx]
            p_recent = price_df[t].iloc[recent_idx]
            if pd.notna(p_past) and pd.notna(p_recent) and p_past > 0:
                scores[t] = (p_recent - p_past) / p_past
                
    # Sort symbols by momentum score
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    leaderboard = [{"symbol": t, "score": round(score * 100, 2), "rank": idx + 1} for idx, (t, score) in enumerate(sorted_scores)]
    
    top_5 = [t for t, _ in sorted_scores[:5]]
    
    # 4. Backtest Momentum Strategy rebalanced monthly
    daily_rets = price_df.pct_change()
    rebalance_freq = 21
    start_idx = momentum_lookback + 1
    
    portfolio_returns = []
    active_portfolio = []
    backtest_dates = price_df.index[start_idx:]
    
    for i in range(start_idx, len(price_df)):
        is_rebalance = (i - start_idx) % rebalance_freq == 0
        if is_rebalance:
            step_scores = {}
            for t in symbols:
                if t in price_df.columns:
                    p_past = price_df[t].iloc[i - momentum_lookback]
                    p_recent = price_df[t].iloc[i - exclude_days]
                    if pd.notna(p_past) and pd.notna(p_recent) and p_past > 0:
                        step_scores[t] = (p_recent - p_past) / p_past
            sorted_step = sorted(step_scores.items(), key=lambda x: x[1], reverse=True)
            active_portfolio = [t for t, score in sorted_step[:5] if np.isfinite(score)]
            
        if active_portfolio:
            daily_ret = daily_rets[active_portfolio].iloc[i].mean()
        else:
            daily_ret = 0.0
            
        if is_rebalance and i > start_idx:
            daily_ret -= (COST_BPS / 1e4)
            
        portfolio_returns.append(daily_ret)
        
    strat_series = pd.Series(portfolio_returns, index=backtest_dates)
    spy_series = daily_rets["SPY"].loc[backtest_dates] if "SPY" in daily_rets.columns else pd.Series(0.0, index=backtest_dates)
    qqq_series = daily_rets["QQQ"].loc[backtest_dates] if "QQQ" in daily_rets.columns else pd.Series(0.0, index=backtest_dates)
    
    # Apply timeframe filter if requested
    if period != 'all':
        latest_date = price_df.index[-1]
        if period == '3y':
            start_cutoff = latest_date - pd.DateOffset(years=3)
        elif period == '1y':
            start_cutoff = latest_date - pd.DateOffset(years=1)
        elif period == '6m':
            start_cutoff = latest_date - pd.DateOffset(months=6)
        elif period == '3m':
            start_cutoff = latest_date - pd.DateOffset(months=3)
        else:
            start_cutoff = backtest_dates[0]
            
        mask = backtest_dates >= start_cutoff
        if mask.any() and mask.sum() >= 10:
            backtest_dates = backtest_dates[mask]
            strat_series = strat_series[mask]
            if "SPY" in daily_rets.columns:
                spy_series = spy_series[mask]
            if "QQQ" in daily_rets.columns:
                qqq_series = qqq_series[mask]
    
    # Compute stats
    def get_stats(series):
        cum = (1 + series).prod() - 1
        ann_ret = (1 + series.mean()) ** 252 - 1
        ann_vol = series.std() * np.sqrt(252)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        
        # Max drawdown
        cum_prod = (1 + series).cumprod()
        running_max = cum_prod.cummax()
        drawdown = (cum_prod - running_max) / (running_max + 1e-8)
        max_dd = drawdown.min()
        return {
            "total_return": round(cum * 100, 1),
            "annual_return": round(ann_ret * 100, 1),
            "volatility": round(ann_vol * 100, 1),
            "sharpe": round(sharpe, 2),
            "max_dd": round(max_dd * 100, 1),
        }
        
    strat_stats = get_stats(strat_series)
    spy_stats = get_stats(spy_series)
    qqq_stats = get_stats(qqq_series)
    
    # Create interactive plot
    cum_strat = (1 + strat_series).cumprod() * 10000
    cum_spy = (1 + spy_series).cumprod() * 10000
    cum_qqq = (1 + qqq_series).cumprod() * 10000
    
    # Convert index to string for guaranteed clean parsing in Plotly
    backtest_dates_str = backtest_dates.strftime('%Y-%m-%d').tolist()
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=backtest_dates_str, y=cum_strat, mode='lines', name='12-1 Momentum Strategy (Top 5)', line=dict(color='#fbbf24', width=2)))
    if "SPY" in daily_rets.columns:
        fig.add_trace(go.Scatter(x=backtest_dates_str, y=cum_spy, mode='lines', name='SPY (S&P 500) Benchmark', line=dict(color='#64748b', width=1.5, dash='dash')))
    if "QQQ" in daily_rets.columns:
        fig.add_trace(go.Scatter(x=backtest_dates_str, y=cum_qqq, mode='lines', name='QQQ (Nasdaq 100) Benchmark', line=dict(color='#818cf8', width=1.5, dash='dash')))
        
    fig.update_layout(
        title='Growth of $10,000 Investment',
        xaxis_title='Date',
        yaxis_title='Portfolio Value ($)',
        template='plotly_white',
        height=400,
        margin=dict(l=50, r=30, t=60, b=80),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        showlegend=True,
        legend=dict(orientation='h', yanchor='top', y=-0.15, xanchor='center', x=0.5),
        font=dict(family='Inter, sans-serif')
    )
    chart_html = fig.to_html(full_html=False, include_plotlyjs=False)
    
    data = {
        "leaderboard": leaderboard,
        "top_5": top_5,
        "strat_stats": strat_stats,
        "spy_stats": spy_stats,
        "qqq_stats": qqq_stats,
        "chart_html": chart_html,
        "start_date": backtest_dates[0].strftime('%Y-%m-%d'),
        "end_date": backtest_dates[-1].strftime('%Y-%m-%d')
    }
    
    return render_template(
        'momentum.html',
        data=data,
        tab='universe',
        available_symbols=available_symbols,
        searched_symbol='',
        period=period,
        error=None
    )


# User-configurable provider keys. Saved server-side (SQLite) so they apply to the
# backend Finnhub/FMP/LLM calls. Stored keys act as quota fallbacks behind the
# built-in dev key — see providers._ordered_keys / _finnhub_get / _fmp_get and the
# AI provider fallback in ai.py.
SETTINGS_FIELDS = (
    "finnhub_api_key", "fmp_api_key", "sec_user_agent",
) + providers.AI_SETTING_KEYS


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    saved = False
    if request.method == 'POST':
        for field in SETTINGS_FIELDS:
            db.set_setting(field, request.form.get(field, '').strip())
        saved = True

    current = {field: db.get_setting(field) for field in SETTINGS_FIELDS}
    return render_template(
        'settings.html',
        current=current,
        status=providers.configured(),
        saved=saved,
    )


@app.route('/api/options-greeks/<ticker>')
def options_greeks_api(ticker):
    expiration = request.args.get('expiration', '')
    rf_rate_raw = request.args.get('rf_rate', '0.045')
    try:
        rf_rate = float(rf_rate_raw)
    except ValueError:
        rf_rate = 0.045
        
    data = get_options_greeks_data(ticker, expiration, rf_rate)
    if not data:
        return jsonify({"error": f"Could not retrieve options data for {ticker}"}), 404
        
    return jsonify(data)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)