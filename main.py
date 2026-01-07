#!/usr/bin/env python3
"""
Stock Price Change Tracker CLI Tool
Displays percentage and net changes for various time periods
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from tabulate import tabulate
import argparse
import sys
from colorama import init, Fore, Style
import time
import numpy as np

# Initialize colorama for cross-platform color support
init()

def calculate_date_periods():
    """Calculate the date periods for comparison"""
    today = datetime.now()
    
    periods = {
        '1D': today - timedelta(days=1),
        '2D': today - timedelta(days=2),
        '5D': today - timedelta(days=5),
        '1W': today - timedelta(weeks=1),
        '2W': today - timedelta(weeks=2),
        '1M': today - timedelta(days=30),
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
        # Get recent data to ensure we have current price
        hist = stock.history(period="5d")
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"Error getting current price: {e}")
    return None

def get_historical_price_yfinance(ticker, target_date):
    """Get historical price using yfinance with fixed datetime handling"""
    try:
        stock = yf.Ticker(ticker.upper())
        
        # Determine appropriate period based on target date
        days_diff = (datetime.now() - target_date).days
        
        if days_diff <= 7:
            period = "1mo"
        elif days_diff <= 30:
            period = "3mo"
        elif days_diff <= 90:
            period = "6mo"
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
            
    except Exception as e:
        print(f"Debug - Error getting historical data for {target_date}: {e}")
        return None

def colorize_value(value, is_percentage=False):
    """Colorize positive/negative values"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    
    if value > 0:
        color = Fore.GREEN
        sign = "+" if is_percentage else ""
    elif value < 0:
        color = Fore.RED
        sign = "" if is_percentage else "-"
    else:
        return f"{'+' if is_percentage else ''}{value:.2f}{('%' if is_percentage else '')}"
    
    formatted_value = f"{sign}{abs(value):.2f}{('%' if is_percentage else '')}"
    return f"{color}{formatted_value}{Style.RESET_ALL}"

def validate_ticker(ticker):
    """Quick validation of ticker"""
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="1d")
        return not hist.empty
    except:
        return False

def main():
    parser = argparse.ArgumentParser(description="Stock Price Change Tracker")
    parser.add_argument("ticker", nargs="?", help="Stock ticker symbol (e.g., AAPL)")
    
    args = parser.parse_args()
    
    # Get ticker from command line arguments or user input
    ticker = args.ticker
    if not ticker:
        ticker = input("Enter stock ticker symbol: ").strip()
    
    if not ticker:
        print("Error: No ticker symbol provided")
        sys.exit(1)
    
    ticker = ticker.upper().strip()
    
    try:
        print(f"Fetching data for {ticker}...")
        
        # Validate ticker
        if not validate_ticker(ticker):
            print(f"Warning: '{ticker}' may not be a valid ticker symbol.")
        
        # Get current price
        current_price = get_current_price_yfinance(ticker)
        if current_price is None:
            print("Error: Could not retrieve current price")
            print("This might be due to network issues or rate limiting.")
            sys.exit(1)
        
        print(f"Current price: ${current_price:.2f}")
        
        # Calculate periods
        periods = calculate_date_periods()
        
        # Data for tables
        percentage_data = []
        net_change_data = []
        
        # Add current price row
        percentage_data.append(["Current Price", f"${current_price:.2f}"])
        net_change_data.append(["Current Price", f"${current_price:.2f}"])
        
        # Calculate changes for each period
        successful_fetches = 0
        
        for period_name, target_date in periods.items():
            print(f"Fetching {period_name} data...")
            historical_price = get_historical_price_yfinance(ticker, target_date)
            
            if historical_price is not None and not np.isnan(historical_price):
                # Calculate net change
                net_change = current_price - historical_price
                
                # Calculate percentage change
                if historical_price != 0:
                    percentage_change = ((current_price - historical_price) / historical_price) * 100
                else:
                    percentage_change = 0
                
                # Add to data with colorized values
                percentage_data.append([
                    period_name, 
                    colorize_value(percentage_change, is_percentage=True)
                ])
                net_change_data.append([
                    period_name, 
                    colorize_value(net_change)
                ])
                successful_fetches += 1
                print(f"  {period_name}: ${historical_price:.2f}")
            else:
                percentage_data.append([period_name, "N/A"])
                net_change_data.append([period_name, "N/A"])
                print(f"  {period_name}: N/A")
        
        if successful_fetches == 0:
            print("\nWarning: Could not fetch historical data for any periods.")
            print("This might be due to:")
            print("1. Network issues or rate limiting")
            print("2. The ticker symbol may not have sufficient historical data")
            print("3. Yahoo Finance API temporary issues")
            print("\nTry again later or check if the ticker is valid.")
        
        # Display tables
        print(f"\n{Fore.CYAN}=== Stock Price Analysis for {ticker} ==={Style.RESET_ALL}")
        
        # Display percentage change table
        print(f"\n{Fore.YELLOW}Percentage Change Table:{Style.RESET_ALL}")
        print(tabulate(percentage_data, headers=["Period", "Change (%)"], tablefmt="grid"))
        
        # Display net change table
        print(f"\n{Fore.YELLOW}Net Change Table:{Style.RESET_ALL}")
        print(tabulate(net_change_data, headers=["Period", "Change ($)"], tablefmt="grid"))
            
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()