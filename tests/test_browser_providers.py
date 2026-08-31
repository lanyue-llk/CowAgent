# encoding:utf-8
"""Unit tests for local Moli and Lexmount Cloud Browser providers."""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.tools.browser.browser_provider import (
    LexmountCloudProvider,
    LocalMoliProvider,
    find_moli_binary,
)
from agent.tools.browser.browser_env import browsers_download_dir
from agent.tools.browser.browser_service import BrowserService


class TestLocalMoliProvider(unittest.TestCase):
    def test_configured_binary_must_be_executable(self):
        with patch("os.path.isfile", return_value=True), patch(
            "os.access", return_value=True
        ):
            self.assertEqual(
                find_moli_binary({"moli_path": "/opt/moli"}), "/opt/moli"
            )

    @patch("agent.tools.browser.browser_provider.socket.create_connection")
    @patch("agent.tools.browser.browser_provider.subprocess.Popen")
    @patch("agent.tools.browser.browser_provider.find_moli_binary")
    def test_start_builds_layout_command_and_close_owns_process(
        self, find_binary, popen, create_connection
    ):
        find_binary.return_value = "/opt/moli"
        process = MagicMock()
        process.poll.return_value = None
        popen.return_value = process
        create_connection.return_value.__enter__.return_value = MagicMock()

        provider = LocalMoliProvider(
            {
                "moli_layout": True,
                "moli_resource": False,
                "moli_profile_dir": "/tmp/moli-profile",
                "moli_port": 9227,
            }
        )
        connection = provider.start()

        self.assertEqual(connection.cdp_endpoint, "http://127.0.0.1:9227")
        self.assertEqual(connection.provider, "moli")
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ["/opt/moli", "serve"])
        self.assertIn("--layout", command)
        self.assertNotIn("--resource", command)
        self.assertIn("/tmp/moli-profile", command)

        provider.close()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    @patch("agent.tools.browser.browser_provider.find_moli_binary", return_value=None)
    def test_missing_binary_has_actionable_error(self, _find_binary):
        with self.assertRaisesRegex(RuntimeError, "Moli is not installed"):
            LocalMoliProvider({}).start()

    @patch(
        "agent.tools.browser.browser_provider.find_moli_binary",
        return_value="/opt/moli",
    )
    def test_extra_args_cannot_expose_cdp_host(self, _find_binary):
        provider = LocalMoliProvider({"moli_args": ["--host=0.0.0.0"]})
        with self.assertRaisesRegex(ValueError, "cannot override"):
            provider.start()


class TestBrowserDownloadPath(unittest.TestCase):
    def test_explicit_playwright_path_is_used_for_probe_and_runtime(self):
        with patch.dict(
            os.environ,
            {"PLAYWRIGHT_BROWSERS_PATH": "/opt/cow/ms-playwright"},
        ):
            self.assertEqual(
                browsers_download_dir(), "/opt/cow/ms-playwright"
            )


class _FakeSession:
    id = "session-1"
    connect_url = "wss://browser.example/cdp?token=secret"
    inspect_url = "https://browser.example/inspect/session-1"

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSessions:
    def __init__(self, session):
        self.session = session
        self.create_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.session


class _FakeLexmount:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.session = _FakeSession()
        self.sessions = _FakeSessions(self.session)
        self.closed = False
        self.__class__.instances.append(self)

    def close(self):
        self.closed = True


class TestLexmountCloudProvider(unittest.TestCase):
    def setUp(self):
        _FakeLexmount.instances.clear()
        module = types.ModuleType("lexmount")
        module.Lexmount = _FakeLexmount
        self.module_patcher = patch.dict(sys.modules, {"lexmount": module})
        self.module_patcher.start()

    def tearDown(self):
        self.module_patcher.stop()

    def test_moli_session_uses_light_mode_and_closes(self):
        provider = LexmountCloudProvider(
            {
                "lexmount": {
                    "api_key": "key",
                    "project_id": "project",
                    "browser": "moli",
                    "layout": True,
                    "resource": False,
                    "downloads": False,
                }
            }
        )
        connection = provider.start()
        client = _FakeLexmount.instances[-1]

        self.assertEqual(connection.browser, "moli")
        self.assertEqual(connection.provider, "lexmount")
        self.assertEqual(
            client.sessions.create_kwargs["browser_mode"], "light"
        )
        self.assertTrue(
            client.sessions.create_kwargs["enable_lightmount_layout"]
        )
        self.assertFalse(
            client.sessions.create_kwargs["enable_lightmount_resource"]
        )
        self.assertEqual(
            client.sessions.create_kwargs["downloads"], {"enabled": False}
        )

        provider.close()
        self.assertTrue(client.session.closed)
        self.assertTrue(client.closed)

    def test_chromium_session_uses_normal_mode(self):
        provider = LexmountCloudProvider(
            {
                "lexmount_api_key": "key",
                "lexmount_project_id": "project",
                "lexmount_browser": "chromium",
            }
        )
        connection = provider.start()
        client = _FakeLexmount.instances[-1]
        self.assertEqual(connection.browser, "chromium")
        self.assertEqual(
            client.sessions.create_kwargs["browser_mode"], "normal"
        )
        self.assertNotIn(
            "enable_lightmount_resource", client.sessions.create_kwargs
        )
        provider.close()

    def test_invalid_browser_name_is_rejected_before_session_create(self):
        provider = LexmountCloudProvider(
            {
                "lexmount": {
                    "api_key": "key",
                    "project_id": "project",
                    "browser": "firefox",
                }
            }
        )
        with self.assertRaisesRegex(ValueError, "chromium.*moli"):
            provider.start()


class TestBrowserServiceProviderLifecycle(unittest.TestCase):
    def test_cdp_log_redacts_query_credentials(self):
        service = BrowserService(
            {"cdp_endpoint": "wss://browser.example/cdp?token=top-secret"}
        )
        page = MagicMock()
        context = MagicMock()
        context.pages = [page]
        browser = MagicMock()
        browser.contexts = [context]
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        service._playwright = playwright

        with patch("agent.tools.browser.browser_service.logger.info") as info:
            service._connect_cdp({"width": 1280, "height": 720})

        rendered_logs = " ".join(
            str(call.args[0]) for call in info.call_args_list if call.args
        )
        self.assertIn("browser.example", rendered_logs)
        self.assertNotIn("top-secret", rendered_logs)
        self.assertNotIn("/cdp", rendered_logs)

    def test_provider_runtime_is_closed_after_cdp_disconnect(self):
        service = BrowserService({"engine": "moli"})
        provider = MagicMock()
        browser = MagicMock()
        playwright = MagicMock()
        service._provider = provider
        service._browser = browser
        service._playwright = playwright
        service._launch_mode = "provider-cdp"

        service._shutdown_browser()

        browser.close.assert_called_once()
        provider.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertIsNone(service._provider)


if __name__ == "__main__":
    unittest.main()
