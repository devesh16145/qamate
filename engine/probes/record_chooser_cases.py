#!/usr/bin/env python
"""Record chooser cases with pictures: real ambiguous decisions, with the right answer known.

Each situation runs some steps, then asks the engine the deciding step in two ways: with its
`within` (the engine's own exact, verified choice -- the right answer) and without it (the
candidate list a chooser model would be shown). The screenshot with the candidates marked is
kept beside the case, so a model that can see is scored on the same decisions as one that
cannot. Pattern pages give deterministic cases; a few come from live demo sites.

    venv/bin/python engine/probes/record_chooser_cases.py            # writes chooser_cases_vision.json + chooser_images/
"""
import base64
import functools
import http.server
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from playwright.sync_api import sync_playwright
from qm_explorer import Explorer
from qm_ground import decide, rank
from qm_observe import observe
from qm_planner import normalize_step
from qm_selectors import handle
from qm_vision import marked_screenshot

OUT = os.path.join(HERE, "chooser_cases_vision.json")
IMAGES = os.path.join(HERE, "chooser_images")

# (id, kind, url, setup steps, the deciding step, gold: "engine" = the engine's choice with `within`; None = nothing fits)
SITUATIONS = [
    ("rows-edit-bach", "within", "pattern:rows", [], {"do": "click", "target": "Edit", "within": "Bach"}, "engine"),
    ("namesakes-s20", "within", "pattern:namesakes", [], {"do": "click", "target": "Add to cart", "within": "Galaxy S20"}, "engine"),
    ("namesakes-ultra", "within", "pattern:namesakes", [], {"do": "click", "target": "Add to cart", "within": "Galaxy S20 Ultra"}, "engine"),
    ("dup-shipping-city", "within", "pattern:dup", [], {"do": "fill", "target": "City", "within": "Shipping address", "value": "Pune"}, "engine"),
    ("dup-billing-save", "within", "pattern:dup", [], {"do": "click", "target": "Save", "within": "Billing address"}, "engine"),
    ("inline-viewer-bach", "within", "pattern:inlineedit", [], {"do": "dblclick", "target": "Viewer", "within": "Bach"}, "engine"),
    ("checkbox-sms", "wording", "pattern:checkboxes", [], {"do": "check", "target": "SMS alerts"}, "engine"),
    ("rows-no-edit-profile", "none", "pattern:rows", [], {"do": "click", "target": "Edit profile"}, None),
    ("namesakes-no-wishlist", "none", "pattern:namesakes", [], {"do": "click", "target": "Add to wishlist"}, None),
    ("slowswap-withdraw", "wording", "pattern:slowswap", [{"do": "click", "target": "Withdraw"}],
     {"do": "click", "target": "Withdraw money"}, "engine"),
    ("bank-deposit-button", "within", "https://www.globalsqa.com/angularJs-protractor/BankingProject/#/login",
     [{"do": "click", "target": "Customer Login"}, {"do": "select", "target": "Your Name", "value": "Harry Potter"},
      {"do": "click", "target": "Login"}, {"do": "click", "target": "Deposit"}],
     {"do": "click", "target": "Deposit", "within": "Amount to be Deposited"}, "engine"),
    ("opencart-add-main", "within", "https://ecommerce-playground.lambdatest.io/index.php?route=product/product&product_id=40",
     [], {"do": "click", "target": "Add to Cart", "within": "iPhone"}, "engine"),
    ("opencart-home-imac", "within", "https://ecommerce-playground.lambdatest.io/", [],
     {"do": "click", "target": "Add to Cart", "within": "iMac"}, "engine"),
    ("ngmat-favorite-food", "within", "https://material.angular.dev/components/select/examples", [],
     {"do": "select", "target": "Favorite food", "within": "Select in a form", "value": "Pizza"}, "engine"),
    ("ngdate-popup-day", "opened", "https://material.angular.dev/components/datepicker/examples",
     [{"do": "fill", "target": "Start date", "within": "Basic date range picker", "value": "10/05/2026"},
      {"do": "click", "target": "Open calendar", "within": "Basic date range picker"}],
     {"do": "click", "target": "October 20, 2026"}, "engine"),
    ("antd-item2-radio", "wording", "https://ant.design/~demos/form-demo-validate-other", [],
     {"do": "check", "target": "item 2"}, "engine"),
]


def main():
    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if "/slow" in self.path:
                import time
                time.sleep(min(int(self.path.rpartition("ms=")[2] or 0), 10000) / 1000)
                self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(b"ok")
                return
            return super().do_GET()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=HERE))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.makedirs(IMAGES, exist_ok=True)
    cases = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for case_id, kind, url, setup, step, gold in SITUATIONS:
            if url.startswith("pattern:"):
                url = f"http://127.0.0.1:{srv.server_port}/patterns.html?p={url[8:]}"
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            try:
                ex = Explorer(page, base_url=None, confirm=lambda *a: True)
                ok = ex.run_intent({"do": "goto", "url": url})["ok"]
                for s in setup:
                    ok = ok and ex.run_intent(normalize_step(s))["ok"]
                if not ok:
                    print(f"  {case_id}: setup failed, skipped")
                    continue
                obs = observe(page)
                op = step["do"]
                blind = {"op": op, "target": step["target"], "value": step.get("value")}
                shown = rank(blind, obs, limit=12)
                if len(shown) < 2:
                    print(f"  {case_id}: only {len(shown)} candidate(s), skipped")
                    continue
                gold_index = None
                if gold == "engine":
                    aimed = {**blind, "within": step.get("within")}
                    aimed_candidates = rank(aimed, obs, limit=12)
                    ex._mark_opened(aimed_candidates, obs) if hasattr(ex, "_mark_opened") else None
                    pick = decide(aimed_candidates, aimed)
                    if pick is None:
                        print(f"  {case_id}: the engine itself could not decide, skipped")
                        continue
                    gold_index = next((i for i, c in enumerate(shown) if c.element.ref == pick.element.ref), None)
                    if gold_index is None:
                        print(f"  {case_id}: the right element is not among the shown candidates, skipped")
                        continue
                image, numbers = marked_screenshot(page, [page.locator(handle(c.element)) for c in shown])
                if not image:
                    print(f"  {case_id}: fewer than two candidates on screen, skipped")
                    continue
                path = os.path.join(IMAGES, case_id + ".png")
                with open(path, "wb") as f:
                    f.write(base64.b64decode(image.split(",", 1)[1]))
                options = [(f"box {numbers[i]}: " if i in numbers else "not on screen: ") + c.element.summary()[:240]
                           for i, c in enumerate(shown)]
                text = f'{op} "{step["target"]}"' + (f' for/in "{step["within"]}"' if step.get("within") else "") \
                    + (f' with value "{step["value"]}"' if step.get("value") and op in ("fill", "select") else "")
                cases.append({"id": case_id, "kind": kind, "step": {k: v for k, v in step.items() if k != "do"} | {"op": op},
                              "options": options, "gold": gold_index, "image": os.path.relpath(path, HERE),
                              "page": {"title": obs.title, "path": page.url}})
                print(f"  {case_id}: {len(shown)} candidates, gold {gold_index}, {os.path.getsize(path) // 1024} KB")
            finally:
                context.close()
        browser.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"note": "Chooser cases with the candidates marked on a screenshot; gold is the engine's own verified "
                           "choice given the step's `within` (None: nothing listed is meant). See record_chooser_cases.py.",
                   "cases": cases}, f, indent=1, ensure_ascii=False)
    print(f"{len(cases)} cases -> {OUT}")


if __name__ == "__main__":
    main()
