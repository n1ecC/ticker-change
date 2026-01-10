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

def generate_stock_chart(ticker, period="5y", retries=2):
    """Generate an interactive Plotly candlestick chart with timeframe controls
    
    Args:
        ticker: Stock ticker symbol
        period: Default data period to fetch (5y gives full flexibility)
        retries: Number of retry attempts if data fetch fails
    
    Returns:
        HTML string with interactive Plotly chart including:
        - Range selector buttons (1D, 5D, 1M, 3M, 6M, YTD, 1Y, 5Y, All)
        - Range slider for manual date selection
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
            
            # Create subplots with secondary y-axis for volume
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                row_heights=[0.7, 0.3],
                subplot_titles=(f'{ticker.upper()} Stock Price', 'Volume')
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
            
            # Add volume bar chart
            colors = ['#26a69a' if row['Close'] >= row['Open'] else '#ef5350' 
                      for idx, row in df.iterrows()]
            
            fig.add_trace(
                go.Bar(
                    x=df.index,
                    y=df['Volume'],
                    name='Volume',
                    marker_color=colors,
                    showlegend=False
                ),
                row=2, col=1
            )
            
            # Update layout with interactive controls
            fig.update_layout(
                title={
                    'text': f'{ticker.upper()} Interactive Chart',
                    'font': {'size': 20, 'color': '#1e293b'},
                    'x': 0.5,
                    'xanchor': 'center'
                },
                yaxis_title='Price ($)',
                yaxis2_title='Volume',
                template='plotly_white',
                height=650,
                hovermode='x unified',
                font=dict(size=12),
                margin=dict(l=50, r=50, t=100, b=50),
                # Enable range slider for manual selection
                xaxis2=dict(
                    rangeslider=dict(
                        visible=True,
                        thickness=0.05
                    ),
                    type='date'
                )
            )
            
            # Add range selector buttons for quick timeframe switching
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
                    y=1.15,
                    xanchor='left',
                    yanchor='top'
                ),
                title_text="Date",
                row=2, col=1
            )
        
            # Return HTML div with CDN-hosted Plotly.js
            return fig.to_html(full_html=False, include_plotlyjs='cdn')
            
        except Exception as e:
            print(f"Chart generation attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(1)
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
    chart_html = generate_stock_chart(ticker)
    
    return {
        "ticker": ticker.upper(),
        "current_price": current_price,
        "percentage_data": percentage_data,
        "net_change_data": net_change_data,
        "chart_html": chart_html
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

@app.route('/stock')
def stock_page():
    ticker = request.args.get('ticker', '')
    weekdays = request.args.get('weekdays', '')
    
    if ticker:
        data = get_stock_data(ticker)
        
        # Handle custom weekdays lookback
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
                            'weekdays': num_weekdays,
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
        
        data['custom_result'] = custom_result
        return render_template('stock.html', data=data)
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)