"""Warm-session comparison for CowAgent local Chromium and Moli.

Browser startup and a neutral warm-up page are excluded. Each engine stays
alive for all ten public, no-login tasks and all configured rounds. Between
tasks, site data, cookies, HTTP cache and navigation history are cleared while
the browser process, renderer runtime and network pools remain warm.

Run from the repository root::

    MOLI_PATH=/path/to/moli \
      PLAYWRIGHT_BROWSERS_PATH=/path/to/ms-playwright \
      python scripts/benchmark_local_browsers.py

Set COW_BROWSER_BENCHMARK_ROUNDS to change the default three rounds and
COW_BROWSER_BENCHMARK_DIR to choose the disposable working directory.
"""

import json
import os
import statistics
import time
from pathlib import Path

import tiktoken

from agent.tools.browser.browser_tool import BrowserTool


MOLI_PATH = os.environ.get("MOLI_PATH", "")
if not MOLI_PATH:
    raise SystemExit("Set MOLI_PATH to the local Moli executable")
ROOT = Path(os.environ.get("COW_BROWSER_BENCHMARK_DIR", "/tmp/cowagent-browser-benchmark"))
ROOT.mkdir(parents=True, exist_ok=True)
ENCODING = tiktoken.get_encoding("o200k_base")
ROUNDS = max(1, int(os.environ.get("COW_BROWSER_BENCHMARK_ROUNDS", "3")))


TASKS = [
    {
        "id": "wikipedia-python",
        "description": "读取 Wikipedia 的 Python 词条首段",
        "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "actions": [("evaluate", {"script": "() => (document.querySelector('#mw-content-text') || document.body).innerText.slice(0, 5000)"})],
        "expect": "programming language",
        "min_chars": 100,
    },
    {
        "id": "github-trending-readme",
        "description": "打开 GitHub Trending 第一项并读取 README",
        "url": "https://github.com/trending",
        "actions": [
            ("click", {"selector": "article.Box-row h2 a"}),
            ("wait", {"selector": "article.markdown-body", "timeout": 20000}),
            ("get_text", {"selector": "article.markdown-body"}),
        ],
        "min_chars": 500,
    },
    {
        "id": "hacker-news-top-story",
        "description": "读取 Hacker News 当前第一条新闻标题",
        "url": "https://news.ycombinator.com/",
        "actions": [("evaluate", {"script": "() => document.querySelector('.athing .titleline > a')?.innerText || ''"})],
        "min_chars": 5,
    },
    {
        "id": "python-tutorial",
        "description": "读取 Python 官方教程标题和正文",
        "url": "https://docs.python.org/3/tutorial/index.html",
        "actions": [("evaluate", {"script": "() => (document.querySelector('[role=main]') || document.querySelector('main') || document.body).innerText.slice(0, 5000)"})],
        "expect": "Python Tutorial",
        "min_chars": 500,
    },
    {
        "id": "mdn-fetch-api",
        "description": "读取 MDN Fetch API 文档概述",
        "url": "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API",
        "actions": [("evaluate", {"script": "() => (document.querySelector('main') || document.body).innerText.slice(0, 5000)"})],
        "expect": "Fetch API",
        "min_chars": 500,
    },
    {
        "id": "pypi-requests-version",
        "description": "读取 PyPI requests 包当前版本",
        "url": "https://pypi.org/project/requests/",
        "actions": [("evaluate", {"script": "() => document.querySelector('h1')?.innerText || document.body.innerText.slice(0, 500)"})],
        "expect": "requests",
        "min_chars": 10,
    },
    {
        "id": "arxiv-attention-abstract",
        "description": "读取 arXiv Attention Is All You Need 摘要",
        "url": "https://arxiv.org/abs/1706.03762",
        "actions": [("evaluate", {"script": "() => (document.querySelector('blockquote.abstract') || document.body).innerText.slice(0, 5000)"})],
        "expect": "Transformer",
        "min_chars": 300,
    },
    {
        "id": "rust-vec-doc",
        "description": "读取 Rust Vec 标准库文档",
        "url": "https://doc.rust-lang.org/std/vec/struct.Vec.html",
        "actions": [("evaluate", {"script": "() => (document.querySelector('main') || document.body).innerText.slice(0, 5000)"})],
        "expect": "Vec",
        "min_chars": 500,
    },
    {
        "id": "sqlite-window-functions",
        "description": "读取 SQLite Window Functions 文档",
        "url": "https://www.sqlite.org/windowfunctions.html",
        "actions": [("evaluate", {"script": "() => document.body.innerText.slice(0, 5000)"})],
        "expect": "Window Functions",
        "min_chars": 500,
    },
    {
        "id": "rfc-http-semantics",
        "description": "读取 RFC 9110 HTTP Semantics 标准正文",
        "url": "https://www.rfc-editor.org/rfc/rfc9110.html",
        "actions": [("evaluate", {"script": "() => document.body.innerText.slice(0, 5000)"})],
        "expect": "HTTP Semantics",
        "min_chars": 500,
    },
]


def call(tool, action, **kwargs):
    started = time.perf_counter()
    result = tool.execute({"action": action, "timeout": 30000, **kwargs})
    seconds = time.perf_counter() - started
    output = str(result.result or "")
    return {
        "action": action,
        "success": result.status == "success",
        "seconds": seconds,
        "tokens": len(ENCODING.encode(output)),
        "chars": len(output),
        "output": output,
    }


def engine_config(engine):
    workdir = ROOT / engine
    workdir.mkdir(parents=True, exist_ok=True)
    if engine == "chromium":
        return {
            "engine": "chromium",
            "persistent": False,
            "idle_timeout": 900,
            "cwd": str(workdir),
        }
    return {
        "engine": "moli",
        "moli_path": MOLI_PATH,
        "moli_profile_dir": str(workdir / "profile"),
        "moli_layout": True,
        "moli_resource": False,
        "idle_timeout": 900,
        "cwd": str(workdir),
    }


def reset_task_state(tool):
    """Clear task state without restarting the browser process.

    The active page is first purged for origin-scoped data, then moved to a
    blank document. Browser-wide cookies/cache and the page's navigation
    history are cleared through the context/CDP. Reset time is excluded.
    """
    service = tool._get_service()

    def _reset():
        page = service._page
        context = service._context
        current_url = page.url
        try:
            page.evaluate("""async () => {
                try { localStorage.clear(); } catch (_) {}
                try { sessionStorage.clear(); } catch (_) {}
                try {
                    for (const key of await caches.keys()) await caches.delete(key);
                } catch (_) {}
                try {
                    for (const registration of await navigator.serviceWorker.getRegistrations())
                        await registration.unregister();
                } catch (_) {}
                try {
                    const databases = await indexedDB.databases();
                    for (const database of databases) indexedDB.deleteDatabase(database.name);
                } catch (_) {}
            }""")
        except Exception:
            pass

        context.clear_cookies()
        context.clear_permissions()
        cdp = context.new_cdp_session(page)
        try:
            cdp.send("Network.enable")
            cdp.send("Network.clearBrowserCache")
            cdp.send("Network.clearBrowserCookies")
            if current_url.startswith(("http://", "https://")):
                from urllib.parse import urlsplit
                parts = urlsplit(current_url)
                origin = f"{parts.scheme}://{parts.netloc}"
                cdp.send("Storage.clearDataForOrigin", {
                    "origin": origin,
                    "storageTypes": "all",
                })
        finally:
            page.goto("about:blank", wait_until="load", timeout=10000)
            try:
                cdp.send("Page.resetNavigationHistory")
            except Exception:
                pass
            cdp.detach()
        return True

    started = time.perf_counter()
    service._submit(_reset)
    return time.perf_counter() - started


def run_engine(engine):
    tool = BrowserTool(engine_config(engine))
    rows = []
    try:
        warmup = call(tool, "navigate", url="https://example.com/")
        warmup_check = call(tool, "get_text", selector="h1")
        print(json.dumps({
            "type": "warmup",
            "engine": engine,
            "excluded": True,
            "success": warmup["success"] and warmup_check["success"],
            "seconds": round(warmup["seconds"] + warmup_check["seconds"], 3),
        }, ensure_ascii=False), flush=True)

        for round_number in range(1, ROUNDS + 1):
            for task in TASKS:
                reset_seconds = reset_task_state(tool)
                action_rows = [call(tool, "navigate", url=task["url"])]
                if action_rows[-1]["success"]:
                    for action, kwargs in task["actions"]:
                        action_rows.append(call(tool, action, **kwargs))
                        if not action_rows[-1]["success"]:
                            break

                last_output = action_rows[-1]["output"] if action_rows else ""
                expected = task.get("expect")
                success = (
                    len(action_rows) == 1 + len(task["actions"])
                    and all(row["success"] for row in action_rows)
                    and len(last_output) >= task["min_chars"]
                    and (not expected or expected.lower() in last_output.lower())
                )
                row = {
                    "type": "task",
                    "engine": engine,
                    "round": round_number,
                    "task": task["id"],
                    "description": task["description"],
                    "success": success,
                    "reset_seconds_excluded": round(reset_seconds, 3),
                    "seconds": round(sum(item["seconds"] for item in action_rows), 3),
                    "tool_output_tokens": sum(item["tokens"] for item in action_rows),
                    "actions": [
                        {
                            "action": item["action"],
                            "success": item["success"],
                            "seconds": round(item["seconds"], 3),
                            "tokens": item["tokens"],
                            "chars": item["chars"],
                        }
                        for item in action_rows
                    ],
                    "failure": "" if success else last_output[:300],
                }
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        tool.close()
    return rows


all_rows = []
for selected_engine in ("chromium", "moli"):
    all_rows.extend(run_engine(selected_engine))

summary = {}
for selected_engine in ("chromium", "moli"):
    engine_rows = [row for row in all_rows if row["engine"] == selected_engine]
    per_task = {}
    for task in TASKS:
        task_rows = [row for row in engine_rows if row["task"] == task["id"]]
        per_task[task["id"]] = {
            "successes": sum(row["success"] for row in task_rows),
            "runs": len(task_rows),
            "median_seconds": round(statistics.median(row["seconds"] for row in task_rows), 3),
            "median_tool_output_tokens": round(statistics.median(row["tool_output_tokens"] for row in task_rows)),
        }
    summary[selected_engine] = {
        "successes": sum(row["success"] for row in engine_rows),
        "runs": len(engine_rows),
        "success_rate": round(sum(row["success"] for row in engine_rows) / len(engine_rows), 4),
        "median_task_seconds": round(statistics.median(row["seconds"] for row in engine_rows), 3),
        "median_task_tool_output_tokens": round(statistics.median(row["tool_output_tokens"] for row in engine_rows)),
        "total_seconds": round(sum(row["seconds"] for row in engine_rows), 3),
        "per_task": per_task,
    }

print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
