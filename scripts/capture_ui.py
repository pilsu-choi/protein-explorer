#!/usr/bin/env python3
"""Exercise the real local UI and capture desktop/mobile documentation images."""

import argparse
import json
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--output", type=Path, default=Path("docs/assets"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--browser", help="Existing Chromium executable; otherwise use Playwright's installed browser")
    parser.add_argument("--job-id", help="Select a genuine existing prediction for the capture")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"source": args.url, "checks": [], "screenshots": [], "page_errors": []}
    with sync_playwright() as playwright:
        options = {"headless": True, "args": ["--no-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disable-dev-shm-usage"]}
        if args.browser:
            options["executable_path"] = args.browser
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        if args.job_id:
            context.add_init_script("localStorage.setItem('protein-explorer-active-job', " + json.dumps(args.job_id) + ");")
        page = context.new_page()
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        page.get_by_test_id("sample-button").click()
        assert "76 residues" in page.locator("#residue-count").inner_text()
        assert page.get_by_test_id("predict-button").is_disabled()
        report["checks"].append("public MSA choice gates prediction")
        page.locator("#msa-consent").check()
        page.get_by_test_id("predict-button").click()
        page.wait_for_function("document.querySelector('#job-status').textContent === 'Prediction complete'", timeout=240000)
        page.locator("#prediction-viewer canvas").wait_for(state="visible")
        report["checks"].append("real completed prediction and WebGL canvas loaded")
        page.get_by_test_id("search-button").click()
        page.get_by_test_id("search-result").first.wait_for(state="visible", timeout=90000)
        assert page.get_by_test_id("search-result").count() == 10
        assert "1UBQ" in page.get_by_test_id("search-result").first.inner_text()
        page.get_by_test_id("search-result").first.click()
        page.wait_for_function("document.querySelector('#reference-placeholder').hidden", timeout=60000)
        page.locator("#reference-viewer canvas").wait_for(state="visible")
        report["checks"].append("1000-record search returned 10 results and real experimental structure")
        page.wait_for_function("!document.querySelector('#toast').classList.contains('visible')")
        page.wait_for_timeout(900)
        page.screenshot(path=str(args.output / "workspace.png"), full_page=True)
        report["screenshots"].append("workspace.png")
        page.get_by_test_id("compare-button").click()
        page.wait_for_timeout(600)
        predicted = page.locator(".prediction-card").bounding_box()
        reference = page.locator(".reference-card").bounding_box()
        assert abs(predicted["y"] - reference["y"]) < 2 and predicted["x"] < reference["x"]
        page.screenshot(path=str(args.output / "comparison.png"), full_page=True)
        report["screenshots"].append("comparison.png")
        report["checks"].append("prediction and reference displayed side by side in comparison mode")
        page.get_by_test_id("compare-button").click()
        with page.expect_download() as event:
            page.locator("#download-pdb").click()
        download = event.value
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "prediction.pdb"
            download.save_as(target)
            assert "ATOM" in target.read_text()
        report["checks"].append("PDB download contains actual atom records")
        page.reload(wait_until="networkidle")
        page.wait_for_function("document.querySelector('#job-status').textContent === 'Prediction complete'", timeout=20000)
        report["checks"].append("completed job restored after reload")
        page.get_by_test_id("search-button").click()
        page.get_by_test_id("search-result").first.wait_for(state="visible", timeout=60000)
        page.get_by_test_id("search-result").first.click()
        page.wait_for_function("document.querySelector('#reference-placeholder').hidden", timeout=60000)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(700)
        overflow = page.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => {
            const r=e.getBoundingClientRect(); return r.width && r.right > innerWidth+1;
          }).map(e => ({tag:e.tagName,id:e.id,classes:String(e.className),right:e.getBoundingClientRect().right})).slice(0,12)""")
        mobile_size = page.evaluate("({viewport:innerWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth})")
        assert mobile_size["document"] <= mobile_size["viewport"] + 1, {"size": mobile_size, "overflow": overflow}
        page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
        report["screenshots"].append("mobile.png")
        page.get_by_test_id("sequence-input").fill("X" * 40)
        assert page.get_by_test_id("predict-button").is_disabled()
        assert page.get_by_test_id("search-button").is_disabled()
        report["checks"].append("invalid sequence rejected; mobile has no horizontal overflow")
        assert not report["page_errors"], report["page_errors"]
        browser.close()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
