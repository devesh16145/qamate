#!/usr/bin/env python
"""Fast-engine authoring benchmark: the frozen gate's ten workflows on the fast engine.

Three apps -- the Atomic CRM demo (browser-local data), SauceDemo and the local Dispatch
Desk fixture -- and the same ten workflows as engine/frozen_benchmark.py, written as plain
requests (the classic prompts name classic-agent tools). For every task:

  1. a fresh browser context and a fresh test project; a cold app memory unless --warm
     (then tasks on the same app share what earlier tasks learned);
  2. the fast agent with the LIVE planner authors the test (plan -> execute -> fresh
     in-process replay -> save);
  3. the saved test is run independently through pytest with QAmate's conftest
     (--verify-runs, default 2). The agent's own replay is never the headline number.

Verdicts: reliable (every independent run green) | flaky | failing | not_delivered | error.
Timing per task: authoring (scan / planning / first step / browser / replay), planner
calls and tokens, re-plans. Requirement coverage is NOT audited automatically here --
read the generated tests (they are kept) before claiming a workflow is covered.

Scorecards land in results/_agent_bench/<timestamp>-fast/ (listed by the Benchmark tab).

    venv/bin/python engine/qm_bench.py --provider mimo                 # all ten
    venv/bin/python engine/qm_bench.py --provider mimo --only crm      # an app, or task ids
    venv/bin/python engine/qm_bench.py --provider mimo --warm          # shared app memory
    venv/bin/python engine/qm_bench.py --provider openrouter --model z-ai/glm-5.3-flash   # try another planner
--model swaps the model of the chosen profile for this run only (settings are not changed),
so planner models can be compared on the same ten workflows with one key.
Keys come from the environment (the app passes the ones stored in the keychain).
"""
import argparse
import copy
import datetime
import json
import os
import subprocess
import sys
import time

ENGINE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)

CRM = "https://marmelab.com/atomic-crm-demo/"
SAUCE = "https://www.saucedemo.com/"
SAUCE_LOGIN = "On SauceDemo, log in with the public demo account standard_user / secret_sauce. "
CRM_NOTE = "On the Atomic CRM demo (its data lives in this browser only; never reload the page -- the demo resets on reload): "

TASKS = [
    {"id": "crm-lifecycle", "app": "crm", "prompt": CRM_NOTE +
     "create company QAMATE Lifecycle 9f58c327 with website https://example.com, city Qamate Test City, "
     "description Qamate original description, sector Industrials and size 10-49 employees. Before creating it, "
     "check the name, website, city and description fields hold exactly those values. After creating it, check "
     "the company's detail page (not a toast) shows the name, city, description, sector and size. Edit the same "
     "company: change the city to Qamate Revised City and the description to Qamate revised description, save, and "
     "check the detail page shows all five correct values and no longer shows the old city or old description. Go "
     "back to Companies, search for the exact company name, check the search box holds it and the company is "
     "listed, open it and check the edited details again. Do not delete anything."},
    {"id": "crm-related-contact", "app": "crm", "prompt": CRM_NOTE +
     "create company QAMATE Relationship 9f58c327 with website https://example.com, city Qamate Test City, "
     "description Qamate original description and sector Industrials; check the name, website, city and "
     "description fields before creating it, then check its detail page shows the name, city, description and "
     "sector. Edit it: city Qamate Revised City, description Qamate revised description; save and check the detail "
     "page shows the revised city and description and no longer the original ones. Go to Companies, search for the "
     "exact name, check the search value and the listed company, open it and check the revised details again. Then "
     "create a contact for this same company: first name Qamate, last name RelationTest, email "
     "qamate.relation@example.com, title QA Engineer, company QAMATE Relationship 9f58c327; before saving check the "
     "first name, last name, email, title and selected company. On the saved contact check the full name Qamate "
     "RelationTest, the email, the title and the company name. Open the company from the contact, open its contacts "
     "tab and check Qamate RelationTest and QA Engineer are listed there. Do not delete anything."},
    {"id": "crm-search", "app": "crm", "prompt": CRM_NOTE +
     "open Companies and search for QAMATE-NOMATCH-9f58c327. Check the search box holds exactly that value, the "
     "list shows its empty-state message, and that this is the Companies page. Clear the search and check the "
     "company list is populated again. Do not create, edit or delete anything, and don't check specific company "
     "names or counts (demo data differs per session)."},
    {"id": "smoke", "app": "saucedemo", "prompt": SAUCE_LOGIN +
     "Sort products by Price (low to high) and check the sort dropdown's selected value is lohi. Add Sauce Labs "
     "Onesie to the cart, open the cart and check it lists Sauce Labs Onesie and the URL contains /cart.html. "
     "Do not check out."},
    {"id": "cart-edit", "app": "saucedemo", "destructive_ok": True, "prompt": SAUCE_LOGIN +
     "Open Sauce Labs Backpack's details and check the product name. Add it to the cart, go back to the products, "
     "add Sauce Labs Onesie, then open the cart and check both products are listed. Remove only Sauce Labs Backpack "
     "(removing this demo cart item is authorized), then check the cart page (/cart.html) no longer shows Sauce Labs "
     "Backpack but still shows Sauce Labs Onesie. Continue shopping, open the cart again and check the same final "
     "state. Do not check out."},
    {"id": "negative-login", "app": "saucedemo", "prompt":
     "On SauceDemo's login page, click Login with both fields empty and check the missing-username error message "
     "shown. Then fill only the username standard_user, click Login and check the missing-password error message. "
     "Then fill the password secret_sauce, click Login and check you reach /inventory.html showing Products. Do not "
     "use invalid passwords."},
    {"id": "sort-detail", "app": "saucedemo", "prompt": SAUCE_LOGIN +
     "Sort products by Name (Z to A) and check the sort dropdown's selected value is za. Open Test.allTheThings() "
     "T-Shirt (Red) and check its name, the price $15.99 and that the description contains Super-soft and comfy "
     "ringspun combed cotton. Go back to the products and check the URL is /inventory.html and Products is shown; "
     "open Sauce Labs Bike Light and check its name and price $9.99. Don't add anything to the cart."},
    {"id": "ops-transfer", "app": "dispatch", "prompt":
     "In Dispatch Desk, create a transfer named QA Dispatch Cedar with Region North, Destination Jaipur, Units 12, "
     "Notes Cedar delivery, and service Express chosen in the service dialog. Before saving, check the name, "
     "destination, units and notes fields hold exactly those values. After saving, check the transfer details show "
     "QA Dispatch Cedar, Jaipur, Units: 12, Service: Express, Cedar delivery and Status: Scheduled. Then search "
     "transfers for QA Dispatch Cedar, check the search box value and the matching record, open it and check the "
     "same saved details."},
    {"id": "ops-validation", "app": "dispatch", "prompt":
     "In Dispatch Desk, open a new transfer and try saving with every field empty; check the Transfer name is "
     "required error. Fill transfer name QA Validation Pine, Region South, Destination Kochi, Units 0 and Notes Pine "
     "validation, try saving, and check Units must be greater than zero is shown while the name field still holds "
     "QA Validation Pine. Change Units to 7, check the name, destination and units values, and save. Check the "
     "transfer details show QA Validation Pine, Kochi, Units: 7, Pine validation and Status: Scheduled, without "
     "Units must be greater than zero. Go back to Transfers, open the same transfer and check those details again."},
    {"id": "ops-allocation", "app": "dispatch", "prompt":
     "In Dispatch Desk, find Batch 024 through the Inventory pagination and check Page 4 of 4 and Batch 024 are "
     "shown. Open Batch 024 and check Fertilizer, Warehouse South, Available units: 24 and No allocation. Allocate "
     "quantity 3 to customer CityGrow with tier Priority in the allocation dialog; check the customer, tier and "
     "quantity values before confirming. Check the batch now shows Batch 024 and Allocated 3 to CityGrow (Priority) "
     "without No allocation. Go back to Inventory, reopen Batch 024 and check the allocation again and that No "
     "allocation is absent. Do not allocate other batches."},
]
APPS = {"crm": ("Atomic CRM demo", CRM), "saucedemo": ("SauceDemo", SAUCE), "dispatch": ("Dispatch Desk", None)}


def select_tasks(only):
    if not only:
        return list(TASKS)
    wanted = {w.strip() for part in only for w in part.split(",") if w.strip()}
    return [t for t in TASKS if t["id"] in wanted or t["app"] in wanted]


def verify_independently(root, project_id, test_path, tc_id, runs, out_dir):
    """Run the saved test through pytest + QAmate's conftest, `runs` times."""
    from verification import verified_junit
    outcomes = []
    for n in range(runs):
        junit = os.path.join(out_dir, f"{tc_id}-run{n + 1}.xml")
        env = {**os.environ, "ATS_ROOT": root, "ATS_APP_ROOT": APP_ROOT, "ATS_PROJECT_ID": project_id,
               "PYTHONPATH": APP_ROOT, "ATS_VIDEO": "off", "ATS_TRACING": "off", "ATS_NETWORK": "off",
               "ATS_RESULTS_DIR": os.path.join(out_dir, "pytest-results"), "ATS_NO_MANUAL_INPUT": "1"}
        started = time.monotonic()
        try:
            proc = subprocess.run([sys.executable, "-m", "pytest", os.path.dirname(test_path), "-q",
                                   "-p", "no:cacheprovider", "-c", os.path.join(APP_ROOT, "pytest.ini"),
                                   "--rootdir", root, f"--junitxml={junit}", "--browser", "chromium",
                                   "-k", tc_id.replace("-", "_")],
                                  cwd=root, env=env, capture_output=True, text=True, timeout=300)
            ok = proc.returncode == 0 and bool(verified_junit(junit, tc_id))
            tail = "" if ok else (proc.stdout[-1500:] + proc.stderr[-500:])
        except Exception as exc:
            ok, tail = False, str(exc)[:500]
        outcomes.append({"ok": ok, "s": round(time.monotonic() - started, 1), "tail": tail})
    return outcomes


def run_task(task, *, browser, config, provider, base_url, root, project, page_map, out_dir, verify_runs, emit):
    import project_store
    from qm_agent import FastAgent
    tests_root = project_store.ensure_tests_scaffold(root, project["id"])
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    events = []
    confirms = []

    def confirm(intent, element):
        confirms.append(element)
        return bool(task.get("destructive_ok"))
    row = {"task_id": task["id"], "app": task["app"], "difficulty": task["app"]}
    started = time.monotonic()
    try:
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        agent = FastAgent(page, browser, config, provider_name=provider, base_url=base_url, tests_root=tests_root,
                          emit=events.append, confirm=confirm, log_dir=os.path.join(out_dir, "logs"),
                          page_map=page_map)
        result = agent.run_task(task["prompt"])
    except Exception as exc:
        context.close()
        return {**row, "verdict": "error", "error": f"{type(exc).__name__}: {exc}"[:500],
                "wall_s": round(time.monotonic() - started, 1)}
    context.close()
    usage = [e for e in events if e.get("event") == "model_usage"]
    planner = [e for e in usage if e.get("role") == "planner"]
    decisions = [e for e in usage if e.get("role") != "planner"]
    tokens = {"planner_input": sum(int(e.get("input") or 0) for e in planner),
              "planner_output": sum(int(e.get("output") or 0) for e in planner),
              "planner_reasoning": sum(int(e.get("reasoning_tokens") or 0) for e in planner),
              "decision": sum(int(e.get("input") or 0) + int(e.get("output") or 0) for e in decisions)}
    tokens["total"] = tokens["planner_input"] + tokens["planner_output"] + tokens["decision"]
    timing = result.get("timing") or {}
    row.update({"authoring_s": result.get("authoring_s"), "timing": timing, "tokens": tokens,
                "steps": result.get("steps"), "tool_calls": result.get("steps"),
                "run_attempts": timing.get("planner_calls"), "replans": len(result.get("replanned") or []),
                "replanned": result.get("replanned"), "stop_reason": result.get("stop_reason"),
                "agent_replay": result.get("replay"), "confirmations": confirms,
                "saved": result.get("saved"), "decision_calls": len(decisions)})
    if not result.get("saved"):
        row["verdict"] = "not_delivered"
    else:
        runs = verify_independently(root, project["id"], result["saved"]["path"], result["saved"]["tc_id"],
                                    verify_runs, out_dir) if verify_runs else []
        row["independent"] = {"runs": [r["ok"] for r in runs], "seconds": [r["s"] for r in runs],
                              "failure": next((r["tail"] for r in runs if not r["ok"]), "")}
        green = sum(r["ok"] for r in runs)
        row["verdict"] = ("reliable" if runs and green == len(runs) else "flaky" if green else
                          "failing" if runs else "delivered")
    row["wall_s"] = round(time.monotonic() - started, 1)
    emit(f"  {task['id']}: {row['verdict']} -- authoring {row['authoring_s']} s, "
         f"{row['run_attempts']} planner call(s), {row['replans']} re-plan(s), {tokens['total']} tokens")
    return row


def summarize(rows):
    n = len(rows) or 1
    reliable = [r for r in rows if r.get("verdict") == "reliable"]
    delivered = [r for r in rows if r.get("saved")]
    times = sorted(r["authoring_s"] for r in reliable if r.get("authoring_s") is not None)
    return {"total": len(rows), "independent_reliable": len(reliable),
            "benchmark_score": round(100 * len(reliable) / n, 1),
            "delivered_pct": round(100 * len(delivered) / n, 1),
            "first_try_pct": round(100 * sum(1 for r in rows if r.get("saved") and not r.get("replans")) / n, 1),
            "tokens_total": sum((r.get("tokens") or {}).get("total", 0) for r in rows),
            "median_authoring_s_reliable": times[len(times) // 2] if times else None,
            "verdicts": {v: sum(1 for r in rows if r.get("verdict") == v) for v in
                         ("reliable", "flaky", "failing", "not_delivered", "error")},
            "coverage_audited": False}


def with_model(config, provider, model):
    """(config, profile name, model id) for the run. `model` replaces the profile's model in
    a copy of the configuration; the user's settings stay as they are."""
    from model_profiles import resolve_profile
    name, cfg = resolve_profile(config, provider or None, "planner")
    if not model:
        return config, name, cfg.get("model")
    config = copy.deepcopy(config)
    config["llm"]["providers"][name]["model"] = model
    return config, name, model


def main(argv=None):
    ap = argparse.ArgumentParser(description="Benchmark the fast engine's test authoring.")
    ap.add_argument("--provider", help="planner profile from config.json (default: the configured default)")
    ap.add_argument("--model", help="use this model id with that profile's endpoint and key, for this run only")
    ap.add_argument("--only", action="append", default=[], help="task ids or apps (crm, saucedemo, dispatch)")
    ap.add_argument("--warm", action="store_true", help="tasks on the same app share the app memory")
    ap.add_argument("--verify-runs", type=int, default=2)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--ats-root", default=os.environ.get("ATS_ROOT") or APP_ROOT)
    args = ap.parse_args(argv)

    import project_store
    from disposable_operations import operations_app
    from playwright.sync_api import sync_playwright
    from qm_map import PageMap

    config = json.load(open(os.path.join(args.ats_root, "config.json"), encoding="utf-8"))
    try:
        config, profile, model = with_model(config, args.provider, args.model)
    except Exception as exc:
        print(f"Cannot run: {exc}", flush=True)
        return 2
    tasks = select_tasks(args.only)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(args.ats_root, "results", "_agent_bench", f"{stamp}-fast")
    root = os.path.join(out_dir, "root")
    os.makedirs(root, exist_ok=True)
    emit = lambda line: print(line, flush=True)
    emit(f"Fast-engine benchmark: {len(tasks)} task(s), planner {profile} ({model}), "
         f"{'warm' if args.warm else 'cold'} app memory, {args.verify_runs} independent run(s) each")
    rows, maps, projects = [], {}, {}
    with operations_app() as dispatch_url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        for task in tasks:
            name, base_url = APPS[task["app"]]
            base_url = base_url or dispatch_url
            if task["app"] not in projects:
                projects[task["app"]] = project_store.create_project(root, name, base_url)
            project = projects[task["app"]]
            if args.warm:
                page_map = maps.setdefault(task["app"], PageMap(project_store.page_map_path(root, project["id"])))
            else:
                page_map = PageMap()
            emit(f"- {task['id']} ({name})")
            rows.append(run_task(task, browser=browser, config=config, provider=profile, base_url=base_url,
                                 root=root, project=project, page_map=page_map, out_dir=out_dir,
                                 verify_runs=args.verify_runs, emit=emit))
            with open(os.path.join(out_dir, "scorecard.json"), "w", encoding="utf-8") as f:   # partial results survive a stop
                json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                           "provider": profile, "model": model, "engine": "fast",
                           "env": "warm" if args.warm else "cold", "scorecard": summarize(rows),
                           "tasks": rows}, f, indent=2, default=str)
        browser.close()
    card = summarize(rows)
    emit(f"\nScore: {card['independent_reliable']}/{card['total']} independently reliable "
         f"({card['benchmark_score']}%); delivered {card['delivered_pct']}%; first try {card['first_try_pct']}%; "
         f"median authoring (reliable) {card['median_authoring_s_reliable']} s; tokens {card['tokens_total']}")
    emit(f"Scorecard: {os.path.join(out_dir, 'scorecard.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
