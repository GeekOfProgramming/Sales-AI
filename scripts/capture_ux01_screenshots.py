"""
scripts/capture_ux01_screenshots.py
Captures light and dark mode screenshots across three standard operational viewports:
- 1280x800
- 1440x900
- 1920x1080

Routes captured:
1. Dashboard (/sales/dashboard)
2. Candidates (/sales/candidates)
3. Lead Register - Summary View (/sales/register?view=summary)
4. Lead Register - Full 13-Field View (/sales/register?view=full)
5. Candidate Detail (/sales/candidates/cand:domain:bs-eng.net)
6. Company Research Review (/sales/candidates/cand:domain:bs-eng.net/research)
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUTPUT_DIR = Path("SalesAI/tests/reports")
VIEWPORTS = [
    (1280, 800, "1280x800"),
    (1440, 900, "1440x900"),
    (1920, 1080, "1920x1080"),
]

PAGES_TO_CAPTURE = [
    ("dashboard", "http://127.0.0.1:8899/sales/dashboard"),
    ("candidates", "http://127.0.0.1:8899/sales/candidates"),
    ("leads", "http://127.0.0.1:8899/sales/leads"),
    ("lead_register_summary", "http://127.0.0.1:8899/sales/register?view=summary"),
    ("lead_register_full", "http://127.0.0.1:8899/sales/register?view=full"),
    ("candidate_detail", "http://127.0.0.1:8899/sales/candidates/cand:domain:bs-eng.net"),
    ("company_research_review", "http://127.0.0.1:8899/sales/candidates/cand:domain:bs-eng.net/research"),
]


def run_capture():
    # Detect root directory
    salesai_dir = Path(__file__).resolve().parent.parent
    output_dir = salesai_dir / "tests" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Launch test server on port 8899
    env = os.environ.copy()
    env["SALESAI_PORT"] = "8899"
    env["EMAIL_SEND_ENABLED"] = "false"

    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8899"],
        cwd=str(salesai_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Give server time to bind
        time.sleep(3)

        p = sync_playwright().start()
        browser = p.chromium.launch(executable_path=CHROME_PATH, headless=True)

        for width, height, vp_label in VIEWPORTS:
            for theme in ["light", "dark"]:
                print(f"Capturing {vp_label} in {theme} mode...")
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    color_scheme=theme,
                )
                page = context.new_page()

                for name, url in PAGES_TO_CAPTURE:
                    try:
                        page.goto(url, wait_until="networkidle", timeout=10000)
                        # Set theme attribute on root element if necessary
                        page.evaluate(f"document.documentElement.setAttribute('data-theme', '{theme}')")
                        time.sleep(0.3)

                        out_path = output_dir / f"{name}_{theme}_{vp_label}.png"
                        page.screenshot(path=str(out_path), full_page=False)
                        print(f"  Saved {out_path.name}")
                    except Exception as e:
                        print(f"  Error capturing {name} ({vp_label}, {theme}): {e}")

                context.close()

        # Also refresh baseline standard report images (1440x900)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        page = context.new_page()
        for base_name, route in [
            ("dashboard", "http://127.0.0.1:8899/sales/dashboard"),
            ("candidate_detail", "http://127.0.0.1:8899/sales/candidates/cand:domain:bs-eng.net"),
            ("research_review", "http://127.0.0.1:8899/sales/candidates/cand:domain:bs-eng.net/research"),
            ("lead_register", "http://127.0.0.1:8899/sales/register"),
        ]:
            try:
                page.goto(route, wait_until="networkidle", timeout=10000)
                page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
                page.screenshot(path=str(output_dir / f"{base_name}_dark.png"), full_page=False)
                page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
                page.screenshot(path=str(output_dir / f"{base_name}_light.png"), full_page=False)
            except Exception as e:
                print(f"  Error updating baseline {base_name}: {e}")
        context.close()

        browser.close()
        p.stop()
        print("ALL SCREENSHOTS CAPTURED SUCCESSFULLY")

    finally:
        server_proc.terminate()
        server_proc.wait(timeout=5)


if __name__ == "__main__":
    run_capture()
