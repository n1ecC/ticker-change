"""OpenAPI 3.0 Specification Generator and Interactive Swagger UI.

Generates complete OpenAPI 3.0.3 compliant schemas for all backend endpoints
including institutional quantitative suite, corporate actions, options greeks,
analytics, positioning, chart data, and SEC filings.
"""
from typing import Any, Dict


def get_openapi_spec() -> Dict[str, Any]:
    """Return the complete OpenAPI 3.0.3 dictionary schema."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Ticker-change Quantitative Engine API",
            "version": "1.0.0",
            "description": (
                "Production-grade Quantitative & Institutional Analytics REST API. "
                "Provides point-in-time corporate action resolution, market microstructure (VPIN, Roll/Corwin-Schultz spreads), "
                "macro regime conditional betas, walk-forward algorithmic backtesting, SEC 8-K event classification, "
                "and Black-Scholes higher-order derivatives Greeks."
            ),
            "contact": {
                "name": "Quantitative Systems Team"
            },
            "license": {
                "name": "MIT"
            }
        },
        "servers": [
            {
                "url": "/",
                "description": "Current Environment Server"
            }
        ],
        "tags": [
            {
                "name": "Institutional & Quantitative Analytics",
                "description": "Microstructure, macro conditioning, CAR event studies, and walk-forward simulations"
            },
            {
                "name": "Corporate Actions & Identity",
                "description": "Point-in-time CIK/FIGI/CUSIP resolution, splits, symbol changes, and mergers"
            },
            {
                "name": "Options & Derivatives",
                "description": "Black-Scholes Greeks, IV Smile/Skew, GEX Gamma Exposure, and AI Options Reports"
            },
            {
                "name": "Market Data & Analytics",
                "description": "OHLCV historical charts, fundamental stats, forward estimates, and momentum metrics"
            },
            {
                "name": "Market Positioning & SEC Filings",
                "description": "Finnhub recommendations, insider sentiment (MSPR), institutional 13F holders, raw SEC filings"
            },
            {
                "name": "System & Cache Operations",
                "description": "Health checks, configuration, active ticker discovery, and EOD options cache warming"
            }
        ],
        "paths": {
            "/api/institutional/{ticker}": {
                "get": {
                    "tags": ["Institutional & Quantitative Analytics"],
                    "summary": "Institutional Quantitative Suite",
                    "description": (
                        "Computes high-frequency microstructure metrics (VPIN, Corwin-Schultz, Roll spread, Squeeze risk), "
                        "macro-financial conditioning (Bull/Bear dual betas, upside/downside capture ratios, yield curve spread), "
                        "walk-forward momentum/mean-reversion signals backtest, and parsed SEC 8-K material event classifications."
                    ),
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "description": "Equity ticker symbol (e.g. AAPL, NVDA, SPY)",
                            "schema": {"type": "string", "example": "AAPL"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Comprehensive institutional analytics report",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/InstitutionalAnalyticsResponse"
                                    }
                                }
                            }
                        },
                        "404": {
                            "description": "Ticker not found or insufficient pricing data",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/corporate-actions/{ticker}": {
                "get": {
                    "tags": ["Corporate Actions & Identity"],
                    "summary": "Point-in-Time Corporate Actions & Entity Timeline",
                    "description": "Resolves entity identification (CIK, FIGI, CUSIP) as-of a specific date and returns chronological corporate actions (splits, symbol changes, mergers).",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "description": "Ticker symbol to look up",
                            "schema": {"type": "string", "example": "AAPL"}
                        },
                        {
                            "name": "as_of",
                            "in": "query",
                            "required": False,
                            "description": "Point-in-time date (YYYY-MM-DD) for historical entity resolution",
                            "schema": {"type": "string", "format": "date", "example": "2020-08-30"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Entity details, symbol history, and corporate action log",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/CorporateActionsResponse"
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/options-greeks/{ticker}": {
                "get": {
                    "tags": ["Options & Derivatives"],
                    "summary": "Options Chain & Black-Scholes Greeks",
                    "description": "Returns full options chain strikes with calculated First and Second order Greeks (Delta, Gamma, Theta, Vega, Rho) and implied volatility.",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "description": "Underlying equity ticker symbol",
                            "schema": {"type": "string", "example": "AAPL"}
                        },
                        {
                            "name": "expiration",
                            "in": "query",
                            "required": False,
                            "description": "Expiration date (YYYY-MM-DD). Defaults to nearest expiration.",
                            "schema": {"type": "string", "format": "date", "example": "2026-09-18"}
                        },
                        {
                            "name": "rf_rate",
                            "in": "query",
                            "required": False,
                            "description": "Annualized risk-free interest rate",
                            "schema": {"type": "number", "default": 0.045, "example": 0.045}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Options chain greeks grid",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/OptionsGreeksResponse"
                                    }
                                }
                            }
                        },
                        "404": {
                            "description": "Options data unavailable for ticker",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/options-analysis/{ticker}": {
                "get": {
                    "tags": ["Options & Derivatives"],
                    "summary": "Options Posture, GEX & Volatility Analysis",
                    "description": "Computes Put-Call Ratio (PCR), Max Pain, ATM Implied Volatility, IV Rank, IV Percentile, Expected Move, and Dealer Gamma Exposure (GEX).",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "description": "Underlying ticker symbol",
                            "schema": {"type": "string", "example": "AAPL"}
                        },
                        {
                            "name": "expiration",
                            "in": "query",
                            "required": False,
                            "description": "Expiration date (YYYY-MM-DD)",
                            "schema": {"type": "string", "format": "date"}
                        },
                        {
                            "name": "rf_rate",
                            "in": "query",
                            "required": False,
                            "description": "Risk-free interest rate",
                            "schema": {"type": "number", "default": 0.045}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Quantitative options analysis synthesis",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/OptionsAnalysisResponse"
                                    }
                                }
                            }
                        },
                        "404": {
                            "description": "Failed to compute options analysis",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/options-ai-report/{ticker}": {
                "get": {
                    "tags": ["Options & Derivatives"],
                    "summary": "LLM Generated Options Volatility Synthesis",
                    "description": "Synthesizes dealer positioning, GEX profile, and volatility term structure into an actionable market report.",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "example": "AAPL"}
                        },
                        {
                            "name": "expiration",
                            "in": "query",
                            "schema": {"type": "string"}
                        },
                        {
                            "name": "rf_rate",
                            "in": "query",
                            "schema": {"type": "number", "default": 0.045}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "HTML or markdown formatted AI narrative",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "html": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        },
                        "500": {
                            "description": "AI synthesis generation failed",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/stock/{ticker}": {
                "get": {
                    "tags": ["Market Data & Analytics"],
                    "summary": "Consolidated Stock Fundamentals & Pricing",
                    "description": "Fetches current market quotes, multi-period percentage returns, and technical indicators.",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "example": "AAPL"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Current quote and metrics",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/chart-data/{ticker}": {
                "get": {
                    "tags": ["Market Data & Analytics"],
                    "summary": "Historical OHLCV Chart Series",
                    "description": "Fetches structured historical OHLCV data array with optional start_date and end_date filtering.",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "example": "AAPL"}
                        },
                        {
                            "name": "start_date",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "format": "date", "example": "2025-01-01"}
                        },
                        {
                            "name": "end_date",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "format": "date", "example": "2026-01-01"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "OHLCV array for charting",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "dates": {"type": "array", "items": {"type": "string"}},
                                            "open": {"type": "array", "items": {"type": "number"}},
                                            "high": {"type": "array", "items": {"type": "number"}},
                                            "low": {"type": "array", "items": {"type": "number"}},
                                            "close": {"type": "array", "items": {"type": "number"}},
                                            "volume": {"type": "array", "items": {"type": "number"}}
                                        }
                                    }
                                }
                            }
                        },
                        "404": {
                            "description": "Chart data unavailable",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/analytics/{ticker}": {
                "get": {
                    "tags": ["Market Data & Analytics"],
                    "summary": "Comprehensive Fundamental & Statistical Analytics",
                    "description": "Returns forward analyst estimates, distribution statistics, historical drawdown, and valuation multiples.",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "example": "AAPL"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Full analytics payload",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "404": {
                            "description": "Analytics unavailable",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/positioning/{ticker}": {
                "get": {
                    "tags": ["Market Positioning & SEC Filings"],
                    "summary": "Market Positioning & Institutional Sentiment",
                    "description": "Assembles institutional 13F holders, insider trading transactions, Finnhub monthly MSPR sentiment, and consensus analyst targets.",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "example": "AAPL"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Positioning breakdown",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/raw-sec-filings/{ticker}": {
                "get": {
                    "tags": ["Market Positioning & SEC Filings"],
                    "summary": "Raw SEC EDGAR Filings",
                    "description": "Retrieves recent Form 3, 4, 8-K, 10-Q, 10-K, and 13F filings from SEC EDGAR API.",
                    "parameters": [
                        {
                            "name": "ticker",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "example": "AAPL"}
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "List of recent filings",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/SECFiling"
                                        }
                                    }
                                }
                            }
                        },
                        "500": {
                            "description": "Error fetching filings from EDGAR",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            }
                        }
                    }
                }
            },
            "/api/config": {
                "get": {
                    "tags": ["System & Cache Operations"],
                    "summary": "System Provider Configuration Status",
                    "description": "Returns status and public keys for enabled 3rd-party financial providers.",
                    "responses": {
                        "200": {
                            "description": "Configuration object",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "finnhub_key": {"type": "string"},
                                            "has_finnhub": {"type": "boolean"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/active-tickers": {
                "get": {
                    "tags": ["System & Cache Operations"],
                    "summary": "List Tracked Database Tickers",
                    "description": "Returns distinct equity symbols stored in the SQLite daily_prices table.",
                    "responses": {
                        "200": {
                            "description": "Array of active symbols",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"type": "string", "example": "AAPL"}
                                    }
                                }
                            }
                        },
                        "403": {
                            "description": "Unauthorized access when WARM_CACHE_TOKEN is enforced"
                        }
                    }
                }
            },
            "/api/warm-cache": {
                "post": {
                    "tags": ["System & Cache Operations"],
                    "summary": "Trigger EOD Options Chain Warmer",
                    "description": "Triggers asynchronous background refresh of options chains for all tracked tickers.",
                    "security": [
                        {"BearerAuth": []}
                    ],
                    "responses": {
                        "202": {
                            "description": "Warming job accepted and dispatched in background"
                        },
                        "403": {
                            "description": "Unauthorized invalid bearer token"
                        },
                        "503": {
                            "description": "WARM_CACHE_TOKEN not configured on server"
                        }
                    }
                }
            },
            "/health": {
                "get": {
                    "tags": ["System & Cache Operations"],
                    "summary": "Health Check Probe",
                    "description": "Returns 200 OK if the web server is healthy.",
                    "responses": {
                        "200": {
                            "description": "Service is operational",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string", "example": "ok"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Token"
                }
            },
            "schemas": {
                "ErrorResponse": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {
                        "error": {"type": "string", "example": "Could not retrieve data"}
                    }
                },
                "SECFiling": {
                    "type": "object",
                    "properties": {
                        "form": {"type": "string", "example": "8-K"},
                        "filingDate": {"type": "string", "format": "date", "example": "2026-02-14"},
                        "reportDate": {"type": "string", "format": "date"},
                        "acceptanceDateTime": {"type": "string"},
                        "act": {"type": "string"},
                        "fileNumber": {"type": "string"},
                        "accessionNumber": {"type": "string"},
                        "items": {"type": "string", "example": "2.02,7.01"},
                        "size": {"type": "integer"},
                        "isXBRL": {"type": "integer"},
                        "isInlineXBRL": {"type": "integer"},
                        "primaryDocument": {"type": "string"},
                        "primaryDocDescription": {"type": "string"},
                        "url": {"type": "string", "format": "uri"}
                    }
                },
                "InstitutionalAnalyticsResponse": {
                    "type": "object",
                    "required": ["ticker", "microstructure", "macro_conditioning", "signals_backtest", "sec_8k_events"],
                    "properties": {
                        "ticker": {"type": "string", "example": "AAPL"},
                        "microstructure": {
                            "type": "object",
                            "properties": {
                                "corwin_schultz_spread": {"type": "number", "example": 0.0042},
                                "roll_effective_spread": {"type": "number", "example": 0.0038},
                                "amihud_illiquidity": {"type": "number"},
                                "vpin": {"type": "number", "example": 0.28},
                                "squeeze_risk_score": {"type": "number", "example": 45.0},
                                "squeeze_risk_level": {"type": "string", "example": "MODERATE"}
                            }
                        },
                        "macro_conditioning": {
                            "type": "object",
                            "properties": {
                                "regime": {"type": "string", "example": "NEUTRAL_EXPANSION"},
                                "fed_funds_rate": {"type": "number", "example": 4.50},
                                "yield_curve_2s10s": {"type": "number", "example": 0.35},
                                "is_inverted": {"type": "boolean", "example": False},
                                "cpi_yoy": {"type": "number", "example": 2.8},
                                "betas": {
                                    "type": "object",
                                    "properties": {
                                        "overall": {"type": "number", "example": 1.15},
                                        "bull_beta": {"type": "number", "example": 1.25},
                                        "bear_beta": {"type": "number", "example": 0.95}
                                    }
                                },
                                "upside_capture": {"type": "number", "example": 1.12},
                                "downside_capture": {"type": "number", "example": 0.88}
                            }
                        },
                        "signals_backtest": {
                            "type": "object",
                            "properties": {
                                "total_return_pct": {"type": "number", "example": 28.5},
                                "benchmark_return_pct": {"type": "number", "example": 15.2},
                                "annualized_sharpe": {"type": "number", "example": 1.65},
                                "max_drawdown_pct": {"type": "number", "example": -8.4},
                                "win_rate_pct": {"type": "number", "example": 58.2},
                                "trade_count": {"type": "integer", "example": 42}
                            }
                        },
                        "sec_8k_events": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "accession_number": {"type": "string"},
                                    "filing_date": {"type": "string"},
                                    "items": {"type": "array", "items": {"type": "string"}},
                                    "primary_category": {"type": "string"},
                                    "description": {"type": "string"}
                                }
                            }
                        }
                    }
                },
                "CorporateActionsResponse": {
                    "type": "object",
                    "required": ["ticker", "actions"],
                    "properties": {
                        "ticker": {"type": "string", "example": "AAPL"},
                        "entity": {
                            "type": "object",
                            "nullable": True,
                            "properties": {
                                "entity_id": {"type": "string", "example": "AAPL_INC"},
                                "cik": {"type": "string", "example": "0000320193"},
                                "figi": {"type": "string", "example": "BBG000B9XRY4"},
                                "cusip": {"type": "string", "example": "037833100"},
                                "legal_name": {"type": "string", "example": "Apple Inc."}
                            }
                        },
                        "symbol_timeline": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "valid_from": {"type": "string"},
                                    "valid_to": {"type": "string", "nullable": True}
                                }
                            }
                        },
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action_id": {"type": "string"},
                                    "action_type": {"type": "string", "example": "SPLIT"},
                                    "effective_date": {"type": "string", "example": "2020-08-31"},
                                    "announcement_date": {"type": "string", "nullable": True},
                                    "ratio": {"type": "number", "example": 4.0},
                                    "old_value": {"type": "string", "nullable": True},
                                    "new_value": {"type": "string", "nullable": True},
                                    "status": {"type": "string", "example": "EFFECTIVE"}
                                }
                            }
                        }
                    }
                },
                "OptionsGreeksResponse": {
                    "type": "object",
                    "required": ["ticker", "expirations", "selected_expiration", "spot_price", "days_to_expiration", "options"],
                    "properties": {
                        "ticker": {"type": "string", "example": "AAPL"},
                        "expirations": {"type": "array", "items": {"type": "string"}},
                        "selected_expiration": {"type": "string", "example": "2026-09-18"},
                        "spot_price": {"type": "number", "example": 225.50},
                        "days_to_expiration": {"type": "integer", "example": 33},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "strike": {"type": "number", "example": 225.0},
                                    "call_bid": {"type": "number"},
                                    "call_ask": {"type": "number"},
                                    "call_last": {"type": "number"},
                                    "call_iv": {"type": "number"},
                                    "call_delta": {"type": "number", "example": 0.52},
                                    "call_gamma": {"type": "number", "example": 0.035},
                                    "call_theta": {"type": "number", "example": -0.082},
                                    "call_vega": {"type": "number", "example": 0.24},
                                    "put_bid": {"type": "number"},
                                    "put_ask": {"type": "number"},
                                    "put_last": {"type": "number"},
                                    "put_iv": {"type": "number"},
                                    "put_delta": {"type": "number", "example": -0.48}
                                }
                            }
                        }
                    }
                },
                "OptionsAnalysisResponse": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "example": "AAPL"},
                        "spot_price": {"type": "number", "example": 225.50},
                        "selected_expiration": {"type": "string"},
                        "days_to_expiration": {"type": "integer"},
                        "hv30": {"type": "number", "example": 18.5},
                        "hv90": {"type": "number", "example": 21.2},
                        "atm_iv": {"type": "number", "example": 23.4},
                        "iv_rank": {"type": "number", "example": 62.5},
                        "iv_percentile": {"type": "number", "example": 68.0},
                        "expected_move_bs": {"type": "number", "example": 12.8},
                        "expected_move_straddle": {"type": "number", "example": 13.1},
                        "vol_pcr": {"type": "number", "example": 0.85},
                        "oi_pcr": {"type": "number", "example": 0.92},
                        "max_pain": {"type": "number", "example": 220.0},
                        "strategy_recommendation": {"type": "string", "example": "Sell Premium (Strangle / Iron Condor)"},
                        "strategy_rationale": {"type": "string"}
                    }
                }
            }
        }
    }


def get_swagger_ui_html(openapi_json_url: str = "/api/openapi.json", title: str = "Ticker-change Quantitative Engine API") -> str:
    """Return an interactive standalone Swagger UI HTML document."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
  <link rel="icon" type="image/png" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/favicon-32x32.png" sizes="32x32" />
  <link rel="icon" type="image/png" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/favicon-16x16.png" sizes="16x16" />
  <style>
    html {{
      box-sizing: border-box;
      overflow: -moz-scrollbars-vertical;
      overflow-y: scroll;
    }}
    *, *:before, *:after {{
      box-sizing: inherit;
    }}
    body {{
      margin:0;
      background: #0f172a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    .topbar-header {{
      background: #1e293b;
      padding: 16px 24px;
      border-bottom: 1px solid #334155;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .topbar-header h1 {{
      margin: 0;
      font-size: 1.25rem;
      font-weight: 700;
      color: #38bdf8;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .topbar-links a {{
      color: #94a3b8;
      text-decoration: none;
      margin-left: 16px;
      font-size: 0.875rem;
      font-weight: 500;
      transition: color 0.15s ease;
    }}
    .topbar-links a:hover {{
      color: #38bdf8;
    }}
    /* Swagger UI Overrides for clean readability */
    .swagger-ui {{
      background: #ffffff;
      padding-bottom: 40px;
    }}
  </style>
</head>
<body>
  <div class="topbar-header">
    <h1>
      <span>⚡</span> {title}
    </h1>
    <div class="topbar-links">
      <a href="/" target="_self">Dashboard</a>
      <a href="/glossary" target="_self">Glossary</a>
      <a href="/api/openapi.json" target="_blank">OpenAPI JSON</a>
    </div>
  </div>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
  <script>
    window.onload = function() {{
      window.ui = SwaggerUIBundle({{
        url: "{openapi_json_url}",
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset
        ],
        plugins: [
          SwaggerUIBundle.plugins.DownloadUrl
        ],
        layout: "BaseLayout",
        defaultModelsExpandDepth: 2,
        defaultModelExpandDepth: 2,
        docExpansion: "list",
        filter: true
      }});
    }};
  </script>
</body>
</html>
"""
