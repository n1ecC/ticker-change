from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from db import init_db, is_fresh, get_prices, store_prices
import providers

app = Flask(__name__)
CORS(app)

with app.app_context():
    init_db()


def get_or_fetch_prices(symbol: str, period: str = "5y") -> pd.DataFrame | None:
    """Return cached prices from DB, fetching from yfinance if stale or missing."""
    symbol = symbol.upper()
    if not is_fresh(symbol):
        try:
            stock = yf.Ticker(symbol)
            df = stock.history(period=period)
            if not df.empty:
                store_prices(symbol, df)
        except Exception as e:
            print(f"yfinance fetch failed for {symbol}: {e}")
    return get_prices(symbol)

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
    """Fetch key valuation multiples and short interest from yfinance info."""
    try:
        info = yf.Ticker(ticker.upper()).info
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


def compute_analytics(ticker: str) -> dict | None:
    """Compute all analytics metrics from cached DB prices."""
    df = get_or_fetch_prices(ticker)
    if df is None or df.empty:
        return None

    df = df.copy()
    df['returns'] = df['close'].pct_change()
    df = df.dropna(subset=['returns'])

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
        },
        'charts': {
            'distribution': dist_chart,
            'volatility': vol_chart,
            'drawdown': dd_chart,
            'sharpe': sharpe_chart,
            'seasonality': season_chart,
            'beta': beta_chart,
            'var': var_chart,
            'volume_profile': vp_chart,
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

    # Attach insider chart (needs price df)
    price_df = get_or_fetch_prices(ticker)
    if price_df is not None:
        data['charts']['insider'] = get_insider_chart(ticker, price_df)

    return render_template('analytics.html', data=data, ticker=ticker)


@app.route('/api/analytics/<ticker>')
def analytics_api(ticker):
    data = compute_analytics(ticker)
    if data is None:
        return jsonify({"error": f"Could not retrieve data for {ticker}"}), 404
    # strip chart HTML from JSON response — charts are for the template only
    data.pop('charts', None)
    return jsonify(data)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)