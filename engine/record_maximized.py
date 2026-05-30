"""Custom Playwright recorder: Chrome MAXIMIZED, no viewport constraints.

Replaces `playwright codegen` to fix the issue where position:fixed elements
(sticky footers like 'Save and Next') are invisible in codegen's embedded
Inspector toolbar.

This script:
1. Launches real Chrome maximized with no_viewport=True
2. Opens Playwright Inspector in a SEPARATE window (not overlaid)
3. Uses HAR + tracing to capture all network/actions
4. After the user closes the browser, reads the trace to extract actions
5. Generates a pytest-compatible Python file identical to codegen output

Usage from ATS (main.js):
    python engine/record_maximized.py <output_file> <start_url> [--channel <channel>]
"""

import sys
import os
import json
import zipfile
import re


def _escape(s):
    """Escape a string for Python source code."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _generate_pytest_code(actions, start_url):
    """Convert a list of traced actions into pytest-compatible Python code."""
    lines = []
    lines.append("import re")
    lines.append("from playwright.sync_api import Playwright, sync_playwright, expect")
    lines.append("")
    lines.append("")
    lines.append("def run(playwright: Playwright) -> None:")
    lines.append("    browser = playwright.chromium.launch(headless=False)")
    lines.append("    context = browser.new_context()")
    lines.append("    page = context.new_page()")

    for action in actions:
        atype = action.get("type", "")
        selector = action.get("selector", "")
        url = action.get("url", "")
        value = action.get("value", "")
        key = action.get("key", "")

        if atype == "goto":
            lines.append(f'    page.goto("{_escape(url)}")')
        elif atype == "click":
            lines.append(f'    page.locator("{_escape(selector)}").click()')
        elif atype == "fill":
            lines.append(f'    page.locator("{_escape(selector)}").fill("{_escape(value)}")')
        elif atype == "press":
            lines.append(f'    page.locator("{_escape(selector)}").press("{_escape(key)}")')
        elif atype == "select_option":
            lines.append(f'    page.locator("{_escape(selector)}").select_option("{_escape(value)}")')
        elif atype == "check":
            lines.append(f'    page.locator("{_escape(selector)}").check()')
        elif atype == "dblclick":
            lines.append(f'    page.locator("{_escape(selector)}").dblclick()')
        elif atype == "hover":
            lines.append(f'    page.locator("{_escape(selector)}").hover()')

    lines.append("")
    lines.append("    # ---------------------")
    lines.append("    context.close()")
    lines.append("    browser.close()")
    lines.append("")
    lines.append("")
    lines.append("with sync_playwright() as playwright:")
    lines.append("    run(playwright)")
    lines.append("")
    return "\n".join(lines)


def _parse_trace(trace_path):
    """Parse a Playwright trace zip file and extract user actions."""
    actions = []
    try:
        with zipfile.ZipFile(trace_path, 'r') as zf:
            # The trace contains multiple files; we need trace.trace
            for name in zf.namelist():
                if name.endswith('.trace') or name == 'trace.trace':
                    data = zf.read(name).decode('utf-8')
                    for line in data.strip().split('\n'):
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Look for action entries
                        if entry.get("type") == "action":
                            params = entry.get("params", {})
                            action_type = entry.get("method", "")
                            
                            action = {"type": action_type}
                            if "selector" in params:
                                action["selector"] = params["selector"]
                            if "url" in params:
                                action["url"] = params["url"]
                            if "value" in params:
                                action["value"] = params["value"]
                            if "key" in params:
                                action["key"] = params["key"]
                            
                            # Filter out internal actions
                            if action_type in ("goto", "click", "fill", "press",
                                               "select_option", "check", "dblclick", "hover"):
                                actions.append(action)
    except Exception as e:
        print(f"[recorder] Warning: Could not parse trace: {e}", file=sys.stderr, flush=True)
    
    return actions


def main():
    if len(sys.argv) < 3:
        print("Usage: record_maximized.py <output_file> <start_url> [--channel <channel>]",
              file=sys.stderr)
        sys.exit(1)

    output_file = sys.argv[1]
    start_url = sys.argv[2]
    channel = "chrome"

    for i, arg in enumerate(sys.argv):
        if arg == "--channel" and i + 1 < len(sys.argv):
            channel = sys.argv[i + 1]

    from playwright.sync_api import sync_playwright

    # Create a temp dir for the trace
    trace_dir = os.path.join(os.path.dirname(os.path.abspath(output_file)), "_trace_temp")
    os.makedirs(trace_dir, exist_ok=True)
    trace_path = os.path.join(trace_dir, "trace.zip")

    print(f"[recorder] Launching Chrome maximized (channel={channel})...", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel=channel,
            args=["--start-maximized"],
        )
        context = browser.new_context(no_viewport=True)

        # Start tracing to capture all actions
        context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = context.new_page()
        page.goto(start_url, wait_until="domcontentloaded")

        print("[recorder] ================================================", flush=True)
        print("[recorder] Chrome opened MAXIMIZED - all elements visible!", flush=True)
        print("[recorder] ", flush=True)
        print("[recorder] Use the Playwright Inspector to RECORD actions:", flush=True)
        print("[recorder]   1. Click the red RECORD button in the Inspector", flush=True)
        print("[recorder]   2. Perform your test actions in the browser", flush=True)
        print("[recorder]   3. When done, close the browser window", flush=True)
        print("[recorder] ================================================", flush=True)

        # page.pause() opens Inspector in a SEPARATE window
        page.pause()

        # Save trace before closing
        context.tracing.stop(path=trace_path)
        browser.close()

    # Parse the trace to extract recorded actions
    print("[recorder] Parsing recorded actions from trace...", flush=True)
    actions = _parse_trace(trace_path)

    if not actions:
        # If trace parsing didn't find actions (e.g. user just browsed),
        # write a minimal file with just the goto
        actions = [{"type": "goto", "url": start_url}]
        print("[recorder] No actions captured from trace. Writing minimal file.", flush=True)
    else:
        print(f"[recorder] Captured {len(actions)} actions from trace.", flush=True)

    # Generate pytest-compatible code
    code = _generate_pytest_code(actions, start_url)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(code)

    # Clean up trace
    try:
        import shutil
        shutil.rmtree(trace_dir, ignore_errors=True)
    except Exception:
        pass

    print(json.dumps({"status": "success", "actions": len(actions)}), flush=True)


if __name__ == "__main__":
    main()
