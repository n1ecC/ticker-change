"""Unit tests for OpenAPI 3.0 Documentation & Swagger UI integration.

Verifies:
1. `api_docs.py` generates valid OpenAPI 3.0 schema with all expected routes and models.
2. `/api/docs` serves interactive Swagger UI HTML page.
3. `/api/openapi.json` returns valid JSON matching the OpenAPI spec.
"""
import unittest
import json
import api_docs
from app import app


class TestApiDocs(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_openapi_spec_structure(self):
        """Verify OpenAPI spec contains required 3.0 metadata, tags, and schemas."""
        spec = api_docs.get_openapi_spec()
        self.assertIsInstance(spec, dict)
        self.assertEqual(spec.get("openapi"), "3.0.3")
        self.assertIn("info", spec)
        self.assertIn("paths", spec)
        self.assertIn("components", spec)

        paths = spec["paths"]
        # Required core API endpoints
        expected_endpoints = [
            "/api/institutional/{ticker}",
            "/api/corporate-actions/{ticker}",
            "/api/options-greeks/{ticker}",
            "/api/options-analysis/{ticker}",
            "/api/options-ai-report/{ticker}",
            "/api/stock/{ticker}",
            "/api/chart-data/{ticker}",
            "/api/analytics/{ticker}",
            "/api/positioning/{ticker}",
            "/api/raw-sec-filings/{ticker}",
            "/api/config",
            "/api/active-tickers",
            "/api/warm-cache",
            "/health",
        ]
        for ep in expected_endpoints:
            self.assertIn(ep, paths, f"Missing endpoint {ep} in OpenAPI specification")

        # Required Component Schemas
        schemas = spec["components"]["schemas"]
        expected_schemas = [
            "ErrorResponse",
            "InstitutionalAnalyticsResponse",
            "CorporateActionsResponse",
            "OptionsGreeksResponse",
            "OptionsAnalysisResponse",
            "SECFiling",
        ]
        for s in expected_schemas:
            self.assertIn(s, schemas, f"Missing schema {s} in OpenAPI components")

    def test_swagger_ui_html_generation(self):
        """Verify swagger UI HTML template includes Swagger-UI bundle and configuration."""
        html = api_docs.get_swagger_ui_html(openapi_json_url="/api/openapi.json", title="Test Title")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("swagger-ui", html)
        self.assertIn("/api/openapi.json", html)
        self.assertIn("Test Title", html)

    def test_api_openapi_json_endpoint(self):
        """Verify GET /api/openapi.json returns 200 and valid JSON schema."""
        response = self.app.get("/api/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/json")
        data = response.get_json()
        self.assertEqual(data.get("openapi"), "3.0.3")
        self.assertIn("/api/institutional/{ticker}", data.get("paths", {}))

    def test_api_docs_swagger_ui_route(self):
        """Verify GET /api/docs returns 200 and rendered HTML."""
        response = self.app.get("/api/docs")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"swagger-ui", response.data)
        self.assertIn(b"Ticker-change Quantitative Engine API", response.data)


if __name__ == "__main__":
    unittest.main()
