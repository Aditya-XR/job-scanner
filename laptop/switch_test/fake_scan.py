"""A stand-in for a laptop scan, for run_tests.ps1: the same process tree as a real one (.venv
Python -> Playwright's driver -> Chrome on a profile under browser-profile\\) without opening any
site. Writes "ready" to the file given once Chrome is up, then waits to be killed.

    python fake_scan.py <ready file> chrome|headed|plain
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ready, mode = Path(sys.argv[1]), sys.argv[2]

if mode == "plain":   # a board read without a browser (Foundit)
    ready.write_text(f"ready {os.getpid()}")
    time.sleep(900)
else:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(ROOT / "browser-profile" / f"switch-test-{os.getpid()}"), channel="chrome",
            headless=(mode == "chrome"), args=["--window-position=40,40", "--window-size=500,300"])
        ctx.pages[0].goto("about:blank") if ctx.pages else ctx.new_page().goto("about:blank")
        ready.write_text(f"ready {os.getpid()}")
        time.sleep(900)
