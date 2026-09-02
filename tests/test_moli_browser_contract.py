# encoding:utf-8
"""Optional real Moli contract test.

Set MOLI_PATH (or put moli on PATH) to run. No external network is used.
"""

import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.tools.browser.browser_tool import BrowserTool


MOLI_BINARY = os.environ.get("MOLI_PATH") or shutil.which("moli")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


@unittest.skipUnless(MOLI_BINARY, "set MOLI_PATH to run the real Moli contract")
class TestMoliBrowserContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="cow-moli-contract-")
        cls.root = Path(cls.temp.name)
        (cls.root / "index.html").write_text(
            """<!doctype html><html><head><title>Moli Contract</title></head>
            <body style="min-height:1400px"><h1 id="heading">Moli Contract</h1>
            <a id="details" href="/details.html">Details</a>
            <input id="name" placeholder="Your name">
            <select id="mode"><option value="fast">Fast</option>
            <option value="safe">Safe</option></select>
            <button id="submit"
              onclick="document.querySelector('#result').textContent='Hello '
                +document.querySelector('#name').value+' / '
                +document.querySelector('#mode').value">Submit</button>
            <p id="result">Waiting</p><p id="bottom" style="margin-top:1000px">Bottom</p>
            </body></html>""",
            encoding="utf-8",
        )
        (cls.root / "details.html").write_text(
            "<!doctype html><title>Details</title><h1>Details Page</h1>",
            encoding="utf-8",
        )

        handler = lambda *args, **kwargs: _QuietHandler(
            *args, directory=str(cls.root), **kwargs
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)
        cls.temp.cleanup()

    def setUp(self):
        self.tool = BrowserTool(
            {
                "engine": "moli",
                "moli_path": MOLI_BINARY,
                "moli_profile_dir": str(self.root / "profile"),
                "moli_layout": True,
                "startup_timeout": 20,
                "idle_timeout": 120,
                "cwd": str(self.root),
            }
        )

    def tearDown(self):
        self.tool.close()

    def _call(self, action, **kwargs):
        result = self.tool.execute({"action": action, **kwargs})
        self.assertEqual(result.status, "success", result.result)
        return str(result.result)

    def test_all_browser_actions(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        navigated = self._call("navigate", url=base + "/index.html")
        self.assertIn("Moli Contract", navigated)

        snapshot = self._call("snapshot")
        input_ref = int(
            re.search(r"\[(\d+)\] input[^\n]*placeholder=\"Your name\"", snapshot).group(1)
        )
        button_ref = int(
            re.search(r"\[(\d+)\] button[^\n]*Submit", snapshot).group(1)
        )
        self._call("fill", ref=input_ref, text="Alice")
        self._call("select", selector="#mode", value="safe")
        self._call("click", ref=button_ref)
        self.assertEqual(
            self._call("get_text", selector="#result"), "Hello Alice / safe"
        )
        self.assertEqual(
            self._call(
                "evaluate",
                script="() => document.querySelector('#heading').textContent",
            ),
            "Moli Contract",
        )
        self._call("scroll", direction="down", amount=700)
        self._call("wait", selector="#bottom", timeout=2000)
        screenshot = self._call("screenshot")
        screenshot_path = Path(screenshot.split("Screenshot saved to: ", 1)[1])
        self.assertGreater(screenshot_path.stat().st_size, 100)
        self._call("click", selector="#details")
        self.assertIn("Details Page", self._call("snapshot"))
        self.assertIn("index.html", self._call("back"))
        self.assertIn("details.html", self._call("forward"))
        self._call("press", key="Tab")


if __name__ == "__main__":
    unittest.main()
