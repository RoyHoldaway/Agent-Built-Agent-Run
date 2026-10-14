"""Serve the built site briefly and screenshot it (optional: needs `pip install siteagent[review]`)."""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve(root: Path):
    """Run the production build on a free port with a throwaway database."""
    port = _free_port()
    tmp = tempfile.mkdtemp(prefix="siteagent-db-")
    env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port), "DB_PATH": f"{tmp}/preview.db",
           "ADMIN_PASSWORD": "preview", "SESSION_SECRET": "preview" * 6}
    proc = subprocess.Popen(
        ["node", "--disable-warning=ExperimentalWarning", "dist/server/entry.mjs"],
        cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                httpx.get(base + "/api/services", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.25)
        else:
            raise RuntimeError("preview server did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def screenshots(root: Path, route: str, out_dir: Path, stem: str) -> dict:
    """Return {'desktop': Path, 'mobile': Path, 'mobile_overflow': bool}."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("review needs Playwright: pip install 'siteagent[review]' && playwright install chromium") from e

    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {}
    with serve(root) as base, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for name, size in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 390, "height": 800})):
                page = browser.new_page(viewport=size)
                page.goto(base + route, wait_until="networkidle")
                path = out_dir / f"{stem}-{name}.png"
                page.screenshot(path=str(path), full_page=True)
                result[name] = path
                if name == "mobile":
                    result["mobile_overflow"] = page.evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
                page.close()
        finally:
            browser.close()
    return result
