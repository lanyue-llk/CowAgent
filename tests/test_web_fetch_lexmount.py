# encoding:utf-8
"""Contract tests for the Lexmount WebFetch provider."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.tools.web_fetch.web_fetch import WebFetch


def _response(payload, status_code=200):
    response = MagicMock()
    response.content = json.dumps(payload).encode("utf-8")
    response.encoding = "utf-8"
    response.status_code = status_code
    response.raise_for_status.return_value = None
    return response


class TestLexmountWebFetch(unittest.TestCase):
    def setUp(self):
        self.tool = WebFetch(
            {
                "provider": "lexmount",
                "lexmount": {
                    "api_key": "secret-key",
                    "project_id": "project-1",
                    "base_url": "https://api.lexmount.test",
                },
            }
        )

    def test_provider_schema_is_exposed_only_with_credentials(self):
        configured = self.tool.get_json_schema()
        self.assertIn("provider", configured["parameters"]["properties"])

        with patch.dict(os.environ, {}, clear=True):
            unconfigured = WebFetch({}).get_json_schema()
        self.assertNotIn("provider", unconfigured["parameters"]["properties"])

    @patch("agent.tools.web_fetch.web_fetch.validate_url_safe")
    @patch("agent.tools.web_fetch.web_fetch.requests.post")
    def test_success_maps_provider_response(self, post, validate):
        post.return_value = _response(
            {
                "request_id": "request-1",
                "result": {
                    "url": "https://example.com",
                    "final_url": "https://www.example.com/",
                    "title": "Example",
                    "main_text": "Rendered content",
                    "engine": "lightmount_dcl",
                    "dom_id": "dom-1",
                },
            }
        )

        result = self.tool.execute({"url": "https://example.com"})

        self.assertEqual(result.status, "success")
        self.assertIn("Rendered content", result.result)
        self.assertIn("Provider: lexmount", result.result)
        self.assertIn("Request ID: request-1", result.result)
        self.assertNotIn("secret-key", result.result)
        self.assertEqual(result.ext_data["provider"], "lexmount")
        self.assertEqual(result.ext_data["final_url"], "https://www.example.com/")
        self.assertEqual(result.ext_data["dom_id"], "dom-1")
        self.assertEqual(validate.call_count, 2)
        call = post.call_args
        self.assertEqual(call.args[0], "https://api.lexmount.test/v1/extract")
        self.assertEqual(call.kwargs["json"], {"extract": {"url": "https://example.com"}})
        self.assertEqual(call.kwargs["headers"]["X-API-Key"], "secret-key")
        self.assertEqual(call.kwargs["headers"]["X-Project-Id"], "project-1")

    @patch("agent.tools.web_fetch.web_fetch.validate_url_safe")
    @patch("agent.tools.web_fetch.web_fetch.requests.post")
    def test_provider_error_is_mapped_without_credentials(self, post, _validate):
        post.return_value = _response(
            {
                "request_id": "request-2",
                "error": {"code": "fetch_failed", "message": "target blocked"},
            }
        )
        result = self.tool.execute({"url": "https://example.com"})
        self.assertEqual(result.status, "error")
        self.assertIn("fetch_failed", result.result)
        self.assertNotIn("secret-key", result.result)

    @patch("agent.tools.web_fetch.web_fetch.validate_url_safe")
    @patch("agent.tools.web_fetch.web_fetch.requests.post")
    def test_unsafe_final_url_is_rejected(self, post, validate):
        post.return_value = _response(
            {
                "result": {
                    "final_url": "http://127.0.0.1/private",
                    "main_text": "secret",
                }
            }
        )
        validate.side_effect = [None, ValueError("non-public IP")]
        result = self.tool.execute({"url": "https://example.com"})
        self.assertEqual(result.status, "error")
        self.assertIn("unsafe final URL", result.result)
        self.assertNotIn("Content:\nsecret", result.result)

    @patch("agent.tools.web_fetch.web_fetch.validate_url_safe")
    @patch.object(WebFetch, "_fetch_document")
    def test_documents_keep_local_parser(self, fetch_document, _validate):
        fetch_document.return_value = MagicMock(status="success")
        self.tool.execute(
            {"url": "https://example.com/report.pdf", "provider": "lexmount"}
        )
        fetch_document.assert_called_once_with("https://example.com/report.pdf")

    @patch("agent.tools.web_fetch.web_fetch.validate_url_safe")
    def test_missing_credentials_is_actionable(self, _validate):
        tool = WebFetch({"provider": "lexmount"})
        with patch.dict(os.environ, {}, clear=True):
            result = tool.execute({"url": "https://example.com"})
        self.assertEqual(result.status, "error")
        self.assertIn("LEXMOUNT_API_KEY", result.result)

    @patch("agent.tools.web_fetch.web_fetch.validate_url_safe")
    def test_non_https_custom_provider_endpoint_is_rejected(self, _validate):
        tool = WebFetch(
            {
                "provider": "lexmount",
                "lexmount": {
                    "api_key": "key",
                    "project_id": "project",
                    "base_url": "http://api.example.com",
                },
            }
        )
        result = tool.execute({"url": "https://example.com"})
        self.assertEqual(result.status, "error")
        self.assertIn("must use HTTPS", result.result)


if __name__ == "__main__":
    unittest.main()
