# encoding:utf-8
"""Optional live contract tests for Lexmount services.

These tests create paid/remote resources and access the public internet. Run only
when explicitly enabled:

    RUN_LEXMOUNT_LIVE=1 LEXMOUNT_API_KEY=... LEXMOUNT_PROJECT_ID=... \
      python -m unittest tests.test_lexmount_live_contract
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.tools.browser.browser_tool import BrowserTool
from agent.tools.web_fetch.web_fetch import WebFetch


LIVE_ENABLED = (
    os.environ.get("RUN_LEXMOUNT_LIVE") == "1"
    and bool(os.environ.get("LEXMOUNT_API_KEY"))
    and bool(os.environ.get("LEXMOUNT_PROJECT_ID"))
)


@unittest.skipUnless(
    LIVE_ENABLED,
    "set RUN_LEXMOUNT_LIVE=1 and Lexmount credentials to run live tests",
)
class TestLexmountLiveContract(unittest.TestCase):
    def test_web_fetch(self):
        result = WebFetch({"provider": "lexmount"}).execute(
            {"url": "https://example.com/"}
        )
        self.assertEqual(result.status, "success", result.result)
        self.assertIn("Example Domain", result.result)
        self.assertEqual(result.ext_data["provider"], "lexmount")

    def test_cloud_browser_chromium_and_moli(self):
        for browser in ("chromium", "moli"):
            with self.subTest(browser=browser):
                tool = BrowserTool(
                    {
                        "engine": "lexmount",
                        "lexmount": {
                            "browser": browser,
                            "layout": True,
                            "resource": False,
                            "downloads": False,
                            "recording": False,
                        },
                    }
                )
                try:
                    result = tool.execute(
                        {"action": "navigate", "url": "https://example.com/"}
                    )
                    self.assertEqual(result.status, "success", result.result)
                    self.assertIn("Example Domain", result.result)

                    snapshot = tool.execute({"action": "snapshot"})
                    self.assertEqual(snapshot.status, "success", snapshot.result)
                    self.assertIn("Example Domain", snapshot.result)
                finally:
                    tool.close()


if __name__ == "__main__":
    unittest.main()
