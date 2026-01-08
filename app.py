from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import numpy as np

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
        '4W': today - timedelta(weeks=4),
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

def get_current_price_yfinance(ticker):
    """Get current price using yfinance"""
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="5d")
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except:
        return None
    return None

def get_historical_price_yfinance(ticker, target_date):
    """Get historical price using yfinance with fixed datetime handling"""
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
            
    except:
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
        # If the target date is a weekend (Saturday=5, Sunday=6), mark as weekend placeholder
        # but do NOT dash monthly/yearly periods (they should still attempt to compute values)
        monthly_yearly = {"1M", "2M", "3M", "6M", "1Y", "2Y", "5Y", "YTD"}
        if isinstance(target_date, datetime) and target_date.weekday() >= 5 and period_name not in monthly_yearly:
            percentage_data.append({
                "period": period_name,
                "value": "N/A",
                "placeholder": "weekend"
            })
            net_change_data.append({
                "period": period_name,
                "value": "N/A",
                "placeholder": "weekend"
            })
            continue

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
    
    return {
        "ticker": ticker.upper(),
        "current_price": current_price,
        "percentage_data": percentage_data,
        "net_change_data": net_change_data
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
    if ticker:
        data = get_stock_data(ticker)
        return render_template('stock.html', data=data)
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)