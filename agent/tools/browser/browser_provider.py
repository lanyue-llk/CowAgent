"""Browser provider adapters for local Moli and Lexmount Cloud Browser.

Both providers expose a Chrome DevTools Protocol endpoint.  BrowserService
continues to own all Playwright actions; providers only own runtime lifecycle.
"""

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from common.log import logger
from common.utils import expand_path


@dataclass
class BrowserConnection:
    """A running provider connection consumed by BrowserService."""

    cdp_endpoint: str
    provider: str
    browser: str
    inspect_url: str = ""
    session_id: str = ""


class BrowserProvider:
    """Minimal provider lifecycle contract."""

    def start(self) -> BrowserConnection:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def find_moli_binary(config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve a configured or PATH-installed Moli binary."""
    config = config or {}
    configured = config.get("moli_path") or os.environ.get("MOLI_PATH")
    if configured:
        path = expand_path(str(configured))
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        return None
    return shutil.which("moli")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LocalMoliProvider(BrowserProvider):
    """Launch an installed Moli binary and expose its local CDP endpoint."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._process: Optional[subprocess.Popen] = None
        self._connection: Optional[BrowserConnection] = None

    def start(self) -> BrowserConnection:
        if self._connection is not None:
            return self._connection

        binary = find_moli_binary(self._config)
        if not binary:
            raise RuntimeError(
                "Moli is not installed or executable. Install it from "
                "https://github.com/lexmount/moli, add 'moli' to PATH, or set "
                "tools.browser.moli_path."
            )

        extra_args = self._config.get("moli_args") or []
        if not isinstance(extra_args, list):
            raise ValueError("tools.browser.moli_args must be a list")
        reserved = {"--host", "--port", "--profile-dir"}
        if any(str(arg).split("=", 1)[0] in reserved for arg in extra_args):
            raise ValueError(
                "tools.browser.moli_args cannot override --host, --port, "
                "or --profile-dir"
            )

        port = int(self._config.get("moli_port") or 0) or _free_loopback_port()
        profile = expand_path(
            str(self._config.get("moli_profile_dir") or "~/.cow/moli_profile")
        )
        os.makedirs(profile, exist_ok=True)

        command = [
            binary,
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--profile-dir",
            profile,
        ]
        if self._config.get("moli_layout", True):
            command.append("--layout")
        if self._config.get("moli_resource", False):
            command.append("--resource")
        command.extend(str(arg) for arg in extra_args)

        logger.info(
            "[Browser] Starting local Moli provider "
            f"(layout={self._config.get('moli_layout', True)}, "
            f"resource={self._config.get('moli_resource', False)})"
        )
        popen_kwargs: Dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        self._process = subprocess.Popen(command, **popen_kwargs)

        timeout = float(self._config.get("moli_startup_timeout", 15))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                code = self._process.returncode
                self.close()
                raise RuntimeError(f"Moli exited during startup (code {code})")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    endpoint = f"http://127.0.0.1:{port}"
                    self._connection = BrowserConnection(
                        cdp_endpoint=endpoint,
                        provider="moli",
                        browser="moli",
                    )
                    return self._connection
            except OSError:
                time.sleep(0.05)

        self.close()
        raise RuntimeError(f"Moli did not become ready within {timeout:.0f}s")

    def close(self) -> None:
        process = self._process
        self._process = None
        self._connection = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        except Exception as exc:
            logger.debug(f"[Browser] Moli shutdown error: {exc}")


class LexmountCloudProvider(BrowserProvider):
    """Create and close one Lexmount Cloud Browser session."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        nested = self._config.get("lexmount") or {}
        self._options = nested if isinstance(nested, dict) else {}
        self._client = None
        self._session = None
        self._connection: Optional[BrowserConnection] = None

    def _option(self, name: str, default=None):
        top_level = self._config.get(f"lexmount_{name}")
        if top_level is not None:
            return top_level
        return self._options.get(name, default)

    def start(self) -> BrowserConnection:
        if self._connection is not None:
            return self._connection
        try:
            from lexmount import Lexmount
        except ImportError as exc:
            raise RuntimeError(
                "Lexmount Cloud Browser requires the 'lexmount>=0.5.18' package"
            ) from exc

        browser_name = str(
            self._option("browser", self._option("browser_mode", "chromium"))
        ).strip().lower()
        aliases = {
            "chromium": "normal",
            "chrome": "normal",
            "normal": "normal",
            "moli": "light",
            "light": "light",
        }
        if browser_name not in aliases:
            raise ValueError(
                "tools.browser.lexmount.browser must be 'chromium' or 'moli'"
            )
        browser_mode = aliases[browser_name]

        client_kwargs: Dict[str, Any] = {
            "api_key": self._option("api_key") or os.environ.get("LEXMOUNT_API_KEY"),
            "project_id": self._option("project_id")
            or os.environ.get("LEXMOUNT_PROJECT_ID"),
            "base_url": self._option("base_url")
            or os.environ.get("LEXMOUNT_BASE_URL"),
            "region": self._option("region"),
            "timeout": float(self._option("timeout", 60)),
        }
        client_kwargs = {key: value for key, value in client_kwargs.items() if value}
        self._client = Lexmount(**client_kwargs)

        create_kwargs: Dict[str, Any] = {
            "browser_mode": browser_mode,
            "poll_timeout_sec": float(self._option("startup_timeout", 600)),
        }
        if browser_mode == "light":
            create_kwargs["enable_lightmount_layout"] = bool(
                self._option("layout", True)
            )
            resource = self._option("resource")
            if resource is not None:
                create_kwargs["enable_lightmount_resource"] = bool(resource)

        official_proxy = self._option("official_proxy")
        if official_proxy is not None:
            create_kwargs["official_proxy"] = bool(official_proxy)
        proxy = self._option("proxy")
        if proxy:
            create_kwargs["proxy"] = proxy
        downloads = self._option("downloads")
        if downloads is not None:
            create_kwargs["downloads"] = {"enabled": bool(downloads)}
        recording = self._option("recording")
        if recording is not None:
            create_kwargs["recording"] = {"persistent": bool(recording)}
        window_size = self._option("window_size")
        if window_size:
            create_kwargs["window_size"] = str(window_size)

        logger.info(
            "[Browser] Creating Lexmount Cloud Browser session "
            f"(browser={'moli' if browser_mode == 'light' else 'chromium'})"
        )
        try:
            self._session = self._client.sessions.create(**create_kwargs)
            self._connection = BrowserConnection(
                cdp_endpoint=self._session.connect_url,
                inspect_url=self._session.inspect_url or "",
                session_id=self._session.id,
                provider="lexmount",
                browser="moli" if browser_mode == "light" else "chromium",
            )
            return self._connection
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        session = self._session
        client = self._client
        self._session = None
        self._client = None
        self._connection = None
        if session is not None:
            try:
                session.close()
            except Exception as exc:
                logger.warning(f"[Browser] Lexmount session cleanup failed: {exc}")
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.debug(f"[Browser] Lexmount client cleanup failed: {exc}")


def create_browser_provider(config: Dict[str, Any]) -> BrowserProvider:
    """Build the configured provider without starting it."""
    engine = str(config.get("engine", "")).strip().lower()
    if engine == "moli":
        return LocalMoliProvider(config)
    if engine == "lexmount":
        return LexmountCloudProvider(config)
    raise ValueError(f"Unsupported browser provider engine: {engine}")
