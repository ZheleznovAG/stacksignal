"""Browser smoke tests: the real server (uvicorn, real static frontend) on a scratch database, driven
by Chromium through Playwright. No AI, no scheduler, no discovery -- nothing leaves the machine.

    pip install -r backend/requirements-e2e.txt -c backend/constraints.txt
    python -m playwright install chromium
    cd backend && python -m pytest e2e -q
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    db = tmp_path_factory.mktemp("e2e") / "e2e.db"
    env = {
        **os.environ,
        "STACKSIGNAL_DATABASE_URL": f"sqlite:///{db}",
        # Empty values win over the developer's .env (load_dotenv never overrides what is set).
        "AI_ENABLED": "0", "AI_API_KEY": "", "EMBEDDINGS_ENABLED": "0", "SCHEDULER_ENABLED": "0",
        "DIGEST_ENABLED": "0", "DISCOVERY_ENABLED": "0", "TELEGRAM_BOT_TOKEN": "", "GITHUB_TOKEN": "",
    }
    subprocess.run([sys.executable, str(BACKEND / "e2e" / "seed.py")], env=env, check=True)
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(BACKEND),
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            if httpx.get(f"{base}/api/health", timeout=1, trust_env=False).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if process.poll() is not None or time.monotonic() > deadline:
            process.kill()
            raise RuntimeError("server did not start: " + process.stderr.read().decode(errors="replace")[-2000:])
        time.sleep(0.2)
    yield base
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture()
def page(browser, server):
    """A fresh page that fails the test on any console error or uncaught exception."""
    context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ru-RU")
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.base = server
    page.go = lambda route: page.goto(f"{server}/#/{route}")
    yield page
    context.close()
    assert errors == [], f"browser errors: {errors}"
