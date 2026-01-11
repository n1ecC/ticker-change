from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

app = Flask(__name__)
# Enable CORS for cross-origin requests (adjust origins in production)
CORS(app)

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

def get_price_ranges(ticker, retries=2):
    """Get all-time and 52-week high/low price ranges
    
    Args:
        ticker: Stock ticker symbol
        retries: Number of retry attempts if data fetch fails
    
    Returns:
        Dictionary with 'all_time_high', 'all_time_low', '52_week_high', '52_week_low'
        or None if fetch fails
    """
    for attempt in range(retries):
        try:
            stock = yf.Ticker(ticker.upper())
            
            # Get all-time data (max period)
            all_time_hist = stock.history(period="max")
            if all_time_hist.empty:
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                return None
            
            # Get 52-week data
            year_hist = stock.history(period="1y")
            if year_hist.empty:
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                return None
            
            # Calculate ranges
            all_time_high = float(all_time_hist['High'].max())
            all_time_low = float(all_time_hist['Low'].min())
            week52_high = float(year_hist['High'].max())
            week52_low = float(year_hist['Low'].min())
            
            # Calculate ATR for last month (30 days)
            month_hist = stock.history(period="1mo")
            atr_value = None
            if not month_hist.empty and len(month_hist) > 1:
                # Calculate True Range for each day
                # True Range = max(High - Low, |High - Previous Close|, |Low - Previous Close|)
                month_hist = month_hist.copy()
                month_hist['Previous Close'] = month_hist['Close'].shift(1)
                
                # Calculate the three components
                high_low = month_hist['High'] - month_hist['Low']
                high_prev_close = abs(month_hist['High'] - month_hist['Previous Close'])
                low_prev_close = abs(month_hist['Low'] - month_hist['Previous Close'])
                
                # True Range is the maximum of the three
                true_range = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
                
                # ATR is the average of True Range (excluding first day which has NaN for previous close)
                atr_value = float(true_range.mean())
            
            result = {
                'all_time_high': round(all_time_high, 2),
                'all_time_low': round(all_time_low, 2),
                '52_week_high': round(week52_high, 2),
                '52_week_low': round(week52_low, 2)
            }
            
            if atr_value is not None:
                result['atr_30d'] = round(atr_value, 2)
            
            return result
            
        except Exception as e:
            print(f"Price ranges fetch attempt {attempt + 1} failed for {ticker}: {e}")
            if attempt < retries - 1:
                time.sleep(0.5)
            continue
    
    return None

def generate_stock_chart(ticker, period="5y", retries=2):
    """Generate an interactive Plotly price chart with timeframe controls
    
    Args:
        ticker: Stock ticker symbol
        period: Default data period to fetch (5y gives full flexibility)
        retries: Number of retry attempts if data fetch fails
    
    Returns:
        HTML string with interactive Plotly chart including:
        - Candlestick price chart
        - Color-coded volume histogram
        - Range selector buttons (1D, 5D, 1M, 3M, 6M, YTD, 1Y, 5Y, All)
        - Interactive zoom, pan, and hover tooltips
    """
    for attempt in range(retries):
        try:
            stock = yf.Ticker(ticker.upper())
            # Fetch 5 years of data for maximum flexibility
            df = stock.history(period=period)
            
            if df.empty:
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                return None
            
            # Create subplots with shared x-axis for volume
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.08,
                row_heights=[0.7, 0.3],
                subplot_titles=('Price', 'Volume')
            )
            
            # Add candlestick chart
            fig.add_trace(
                go.Candlestick(
                    x=df.index,
                    open=df['Open'],
                    high=df['High'],
                    low=df['Low'],
                    close=df['Close'],
                    name='Price',
                    increasing_line_color='#26a69a',
                    decreasing_line_color='#ef5350'
                ),
                row=1, col=1
            )
            
            # Add volume bars as color-coded histogram (green for up, red for down)
            colors = ['#26a69a' if row['Close'] >= row['Open'] else '#ef5350' 
                      for idx, row in df.iterrows()]
            
            fig.add_trace(
                go.Bar(
                    x=df.index,
                    y=df['Volume'],
                    name='Volume',
                    marker_color=colors,
                    showlegend=False,
                    marker_line_width=0
                ),
                row=2, col=1
            )
            
            # Update layout (no range slider) with smooth transitions
            fig.update_layout(
                template='plotly_white',
                height=600,
                hovermode='x unified',
                font=dict(size=12),
                autosize=True,
                margin=dict(l=60, r=60, t=60, b=80),
                showlegend=False,
                xaxis_rangeslider_visible=False,
                # Enable smooth transitions for chart updates
                transition=dict(
                    duration=500,  # Transition duration in milliseconds
                    easing='cubic-in-out'  # Easing function for smooth animation
                )
            )
            
            # Update y-axes labels
            fig.update_yaxes(title_text="Price ($)", row=1, col=1)
            fig.update_yaxes(title_text="Volume", row=2, col=1)
            
            # Add range selector buttons to bottom x-axis (no range slider)
            fig.update_xaxes(
                rangeselector=dict(
                    buttons=list([
                        dict(count=1, label="1D", step="day", stepmode="backward"),
                        dict(count=5, label="5D", step="day", stepmode="backward"),
                        dict(count=1, label="1M", step="month", stepmode="backward"),
                        dict(count=3, label="3M", step="month", stepmode="backward"),
                        dict(count=6, label="6M", step="month", stepmode="backward"),
                        dict(count=1, label="YTD", step="year", stepmode="todate"),
                        dict(count=1, label="1Y", step="year", stepmode="backward"),
                        dict(count=5, label="5Y", step="year", stepmode="backward"),
                        dict(label="All", step="all")
                    ]),
                    bgcolor='#f1f5f9',
                    activecolor='#3b82f6',
                    x=0,
                    y=1.0,
                    xanchor='left',
                    yanchor='top',
                    font=dict(size=9)
                ),
                type='date',
                title_text="Date",
                row=2, col=1
            )
        
            # Return HTML div with CDN-hosted Plotly.js and date range info
            chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
            date_range = {
                'start': df.index[0].strftime('%Y-%m-%d'),
                'end': df.index[-1].strftime('%Y-%m-%d')
            }
            return {'html': chart_html, 'date_range': date_range}
            
        except Exception as e:
            print(f"Chart generation attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(1)
            continue
    
    return None

def get_chart_data_json(ticker, start_date=None, end_date=None, retries=2):
    """Fetch historical stock data for a date range and return as JSON
    
    Args:
        ticker: Stock ticker symbol
        start_date: Start date (datetime or string, optional)
        end_date: End date (datetime or string, optional)
        retries: Number of retry attempts if data fetch fails
    
    Returns:
        Dictionary with 'data' (list of OHLCV records) and 'date_range' (start/end dates)
        or None if fetch fails
    """
    for attempt in range(retries):
        try:
            stock = yf.Ticker(ticker.upper())
            
            # Use period-based fetch if no dates specified, otherwise use date range
            if start_date and end_date:
                # Convert string dates to datetime if needed
                if isinstance(start_date, str):
                    start_date = pd.to_datetime(start_date)
                if isinstance(end_date, str):
                    end_date = pd.to_datetime(end_date)
                df = stock.history(start=start_date, end=end_date)
            else:
                # Default to 5 years if no dates specified
                df = stock.history(period="5y")
            
            if df.empty:
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                return None
            
            # Handle timezone
            df.index = pd.to_datetime(df.index).tz_localize(None)
            
            # Convert to list of dictionaries for JSON serialization
            data = []
            for idx, row in df.iterrows():
                data.append({
                    'date': idx.strftime('%Y-%m-%d'),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': int(row['Volume'])
                })
            
            return {
                'data': data,
                'date_range': {
                    'start': df.index[0].strftime('%Y-%m-%d'),
                    'end': df.index[-1].strftime('%Y-%m-%d')
                }
            }
            
        except Exception as e:
            print(f"Chart data fetch attempt {attempt + 1} failed for {ticker}: {e}")
            if attempt < retries - 1:
                time.sleep(0.5)
            continue
    
    return None

def get_stock_data(ticker):
    """Get all stock data for web display"""
    if not ticker:
        return {"error": "No ticker provided"}
    
    # Get current price
    current_price = get_current_price_yfinance(ticker)
    if current_price is None:
        return {"error": f"Could not retrieve data for {ticker}"}
    
    # Calculate periods
    periods = calculate_date_periods()
    
    # Prepare data for tables
    percentage_data = []
    net_change_data = []
    
    # Add current price row
    percentage_data.append({"period": "Current Price", "value": f"${current_price:.2f}"})
    net_change_data.append({"period": "Current Price", "value": f"${current_price:.2f}"})
    
    # Calculate changes for each period
    for period_name, target_date in periods.items():
        # Attempt to retrieve a historical price for all periods (including week-based periods).
        # Previously we skipped weekends for short periods which prevented weekly rows
        # from resolving when the target date landed on a weekend. We'll always attempt
        # to fetch the closest prior trading day in `get_historical_price_yfinance`.

        historical_price = get_historical_price_yfinance(ticker, target_date)

        if historical_price is not None and not np.isnan(historical_price) and historical_price != 0:
            # Calculate net change
            net_change = current_price - historical_price
            
            # Calculate percentage change
            percentage_change = ((current_price - historical_price) / historical_price) * 100
            
            # Add to data
            percentage_data.append({
                "period": period_name,
                "value": round(percentage_change, 2),
                "raw_value": percentage_change,
                "is_positive": percentage_change > 0
            })
            net_change_data.append({
                "period": period_name,
                "value": round(net_change, 2),
                "raw_value": net_change,
                "is_positive": net_change > 0
            })
        else:
            percentage_data.append({
                "period": period_name,
                "value": "N/A"
            })
            net_change_data.append({
                "period": period_name,
                "value": "N/A"
            })
    
    # Generate interactive chart
    chart_result = generate_stock_chart(ticker)
    if chart_result:
        chart_html = chart_result['html']
        chart_date_range = chart_result['date_range']
    else:
        chart_html = None
        chart_date_range = None
    
    return {
        "ticker": ticker.upper(),
        "current_price": current_price,
        "percentage_data": percentage_data,
        "net_change_data": net_change_data,
        "chart_html": chart_html,
        "chart_date_range": chart_date_range
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
        price_ranges = get_price_ranges(ticker)
        if price_ranges:
            data['price_ranges'] = price_ranges
        
        # Handle custom lookback (weekdays or days)
        custom_result = None
        if weekdays:
            try:
                num_weekdays = int(weekdays)
                if num_weekdays > 0:
                    target_date = calculate_weekdays_ago(num_weekdays)
                    historical_price = get_historical_price_yfinance(ticker, target_date)
                    
                    if historical_price and not np.isnan(historical_price) and historical_price != 0:
                        current_price = data['current_price']
                        net_change = current_price - historical_price
                        percentage_change = ((current_price - historical_price) / historical_price) * 100
                        
                        custom_result = {
                            'type': 'weekdays',
                            'days': num_weekdays,
                            'target_date': target_date.strftime('%Y-%m-%d'),
                            'historical_price': round(historical_price, 2),
                            'current_price': round(current_price, 2),
                            'net_change': round(net_change, 2),
                            'percentage_change': round(percentage_change, 2),
                            'is_positive': net_change > 0
                        }
                    else:
                        custom_result = {'error': f'Could not retrieve price for {num_weekdays} weekdays ago'}
            except ValueError:
                custom_result = {'error': 'Please enter a valid number'}
        elif days:
            try:
                num_days = int(days)
                if num_days > 0:
                    # Simple calendar days calculation (no weekday filtering)
                    target_date = datetime.now() - timedelta(days=num_days)
                    historical_price = get_historical_price_yfinance(ticker, target_date)
                    
                    if historical_price and not np.isnan(historical_price) and historical_price != 0:
                        current_price = data['current_price']
                        net_change = current_price - historical_price
                        percentage_change = ((current_price - historical_price) / historical_price) * 100
                        
                        custom_result = {
                            'type': 'days',
                            'days': num_days,
                            'target_date': target_date.strftime('%Y-%m-%d'),
                            'historical_price': round(historical_price, 2),
                            'current_price': round(current_price, 2),
                            'net_change': round(net_change, 2),
                            'percentage_change': round(percentage_change, 2),
                            'is_positive': net_change > 0
                        }
                    else:
                        custom_result = {'error': f'Could not retrieve price for {num_days} days ago'}
            except ValueError:
                custom_result = {'error': 'Please enter a valid number'}
        
        data['custom_result'] = custom_result
        return render_template('stock.html', data=data)
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)