"""
browser/executor.py
--------------------
Executes an AI-generated (or fallback) test plan against the live
application using a real Playwright browser session, evaluates outcome
assertions, and returns structured, evidence-rich results.

Distinguishes "test execution failure" (e.g. selector not found —
infrastructure/test problem) from "application failure" (an assertion
that ran successfully but returned an outcome the app should not have
produced). Only assertion outcomes decide PASS/FAIL; a selector timeout
is BLOCKED/ERROR, never an automatic "bug".
"""

from __future__ import annotations

import time
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .assertions import run_assertion, validate_assertion
from .evidence import save_screenshot
from .selectors import check_selector_on_page

ACTION_TIMEOUT_MS = 8000
NAV_TIMEOUT_MS = 15000

# Actions that interact with a specific element and therefore require a
# selector that resolves to exactly one element before we touch it. We
# never silently disambiguate with `.first` here — an ambiguous selector
# BLOCKS the step rather than risking interaction with the wrong element.
# PRESS is included unconditionally: for this MVP a PRESS action must
# target a specific element (Option A of the PRESS-safety fix) rather than
# falling back to a global `page.keyboard.press()`, whose target would be
# whatever element happened to have focus — not something we can verify.
_SELECTOR_ACTIONS = ("CLICK", "FILL", "SELECT", "CHECK", "UNCHECK", "PRESS")


def _resolve_selector(step: dict):
    return step.get("selector")


def _run_action(page, base_url: str, step: dict) -> dict:
    action = (step.get("action") or "").upper()
    selector = _resolve_selector(step)
    value = step.get("value")
    started = time.time()
    out = {"action": action, "selector": selector, "value": value, "status": "success", "error": None}

    try:
        if action in _SELECTOR_ACTIONS:
            check = check_selector_on_page(page, selector, expect_unique=True)
            if not check.ok:
                out["status"] = "blocked"
                out["error"] = f"Selector reliability check failed: {check.reason}"
                out["duration_ms"] = int((time.time() - started) * 1000)
                return out

        if action == "OPEN":
            url = step.get("url") or value
            if not url:
                raise ValueError("OPEN action missing 'url'")
            full_url = urljoin(base_url, url)
            page.goto(full_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")

        elif action == "CLICK":
            page.locator(selector).click(timeout=ACTION_TIMEOUT_MS)

        elif action == "FILL":
            page.locator(selector).fill(value or "", timeout=ACTION_TIMEOUT_MS)

        elif action == "SELECT":
            page.locator(selector).select_option(value, timeout=ACTION_TIMEOUT_MS)

        elif action == "PRESS":
            key = value or step.get("key")
            page.locator(selector).press(key, timeout=ACTION_TIMEOUT_MS)

        elif action == "CHECK":
            page.locator(selector).check(timeout=ACTION_TIMEOUT_MS)

        elif action == "UNCHECK":
            page.locator(selector).uncheck(timeout=ACTION_TIMEOUT_MS)

        elif action == "WAIT":
            ms = int(value) if value else 1000
            page.wait_for_timeout(ms)

        elif action == "SCREENSHOT":
            pass  # handled by caller after every step if requested

        else:
            raise ValueError(f"Unsupported action: {action}")

    except PlaywrightTimeoutError as e:
        out["status"] = "error"
        out["error"] = f"Timeout: {e}"
    except Exception as e:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = str(e)

    out["duration_ms"] = int((time.time() - started) * 1000)
    return out


def execute_test(base_url: str, run_id: str, test: dict, headless: bool = True) -> dict:
    """
    Execute a single structured test (steps + assertions) against
    base_url. Returns a full result record including PASS/FAIL/BLOCKED/
    ERROR status, per-step evidence, assertion results, and screenshots.
    """
    test_id = test.get("id", "unknown")
    steps = test.get("steps", []) or []
    assertions = test.get("assertions", []) or []

    result = {
        "id": test_id,
        "name": test.get("name", test_id),
        "purpose": test.get("purpose", ""),
        "steps_executed": [],
        "assertion_results": [],
        "console_errors": [],
        "failed_requests": [],
        "final_url": None,
        "screenshot_before": None,
        "screenshot_after": None,
        "status": "ERROR",
        "error": None,
        "duration_ms": 0,
    }

    if not assertions:
        result["status"] = "BLOCKED"
        result["error"] = "Test has no outcome assertions and cannot be validated. Marked BLOCKED per policy."
        return result

    # Runtime backstop: even though the planner validates assertions before
    # a test reaches the executor, never trust that blindly — re-validate
    # here too, so a malformed assertion (e.g. ASSERT_URL with no operator)
    # can never silently pass.
    assertion_problems = [validate_assertion(a) for a in assertions]
    assertion_problems = [p for p in assertion_problems if p]
    if assertion_problems:
        result["status"] = "BLOCKED"
        result["error"] = "One or more assertions are invalid and cannot be evaluated: " + "; ".join(assertion_problems)
        return result

    started = time.time()
    console_errors = []
    failed_requests = []
    # Mutable box so the event handlers below (fired asynchronously by
    # Playwright) can see which step was executing when the event occurred
    # — this is the "action being executed" correlation data Issue 3 of the
    # third correction pass asks for, used later to decide whether a
    # console/network event is actually related to what a test is checking.
    current_step = [-1]
    event_order = [0]

    def _next_order():
        event_order[0] += 1
        return event_order[0]

    def _safe_route():
        try:
            return page.url
        except Exception:
            return None

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=headless)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
                    raise RuntimeError(
                        "Playwright's Chromium browser is not installed. Run "
                        "`python -m playwright install chromium` (see the README) and try again."
                    ) from e
                raise
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.set_default_timeout(ACTION_TIMEOUT_MS)
            page = context.new_page()

            def _on_console(msg):
                if msg.type == "error":
                    console_errors.append({
                        "type": msg.type, "text": msg.text,
                        "step_index": current_step[0], "route": _safe_route(),
                        "order": _next_order(), "timestamp": time.time(),
                    })

            def _on_request_failed(req):
                failed_requests.append({
                    "url": req.url, "method": req.method,
                    "failure": (req.failure or {}).get("errorText") if isinstance(req.failure, dict) else str(req.failure),
                    "step_index": current_step[0], "route": _safe_route(),
                    "order": _next_order(), "timestamp": time.time(),
                })

            def _on_response(res):
                if res.status >= 400:
                    try:
                        method = res.request.method
                    except Exception:
                        method = None
                    failed_requests.append({
                        "url": res.url, "status": res.status, "method": method,
                        "step_index": current_step[0], "route": _safe_route(),
                        "order": _next_order(), "timestamp": time.time(),
                    })

            page.on("console", _on_console)
            page.on("requestfailed", _on_request_failed)
            page.on("response", _on_response)

            # Always start by opening the base page so relative OPEN steps resolve.
            try:
                page.goto(base_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                result["status"] = "ERROR"
                result["error"] = f"Could not open base URL before test: {e}"
                context.close()
                browser.close()
                result["duration_ms"] = int((time.time() - started) * 1000)
                return result

            before_bytes = page.screenshot(full_page=True)
            result["screenshot_before"] = save_screenshot(run_id, f"{test_id}_before", before_bytes)

            execution_failed = False
            execution_blocked = False
            for i, step in enumerate(steps):
                current_step[0] = i
                step_result = _run_action(page, base_url, step)
                result["steps_executed"].append(step_result)
                if step_result["status"] == "blocked":
                    execution_blocked = True
                    break
                if step_result["status"] == "error":
                    execution_failed = True
                    # Keep going is risky; stop executing further steps once
                    # the DOM state is no longer trustworthy.
                    break

            after_bytes = page.screenshot(full_page=True)
            result["screenshot_after"] = save_screenshot(run_id, f"{test_id}_after", after_bytes)
            result["final_url"] = page.url

            if execution_blocked:
                result["status"] = "BLOCKED"
                result["error"] = ("A step's selector was missing, invalid, or ambiguous, and was not "
                                    "silently disambiguated. Test BLOCKED rather than risking interaction "
                                    "with the wrong element.")
            elif execution_failed:
                result["status"] = "ERROR"
                result["error"] = "One or more browser actions failed to execute (selector/timeout/navigation issue)."
            else:
                assertion_results = [run_assertion(page, a) for a in assertions]
                result["assertion_results"] = assertion_results
                any_assertion_blocked = any(a.get("blocked") for a in assertion_results)
                if any_assertion_blocked:
                    result["status"] = "BLOCKED"
                    result["error"] = ("One or more assertions targeted an ambiguous selector (matched "
                                        "more than one element) and could not be safely evaluated against "
                                        "a specific element.")
                else:
                    all_passed = all(a["passed"] for a in assertion_results) if assertion_results else False
                    result["status"] = "PASS" if all_passed else "FAIL"

            context.close()
            browser.close()

    except Exception as e:  # noqa: BLE001
        result["status"] = "ERROR"
        result["error"] = f"Unhandled executor error: {e}"

    # Structured evidence references (Part 3 of the second correction
    # pass): every collected piece of evidence gets a stable ref (A1, C1,
    # N1, ...) so findings can cite `assertion:T1:A1` etc. instead of free
    # text that has to be fuzzy-matched back to "something that sounds
    # similar". See ai/evidence_index.py for how these are resolved.
    for i, a in enumerate(result["assertion_results"], start=1):
        a["ref"] = f"A{i}"
    for i, c in enumerate(console_errors, start=1):
        c["ref"] = f"C{i}"
    for i, n in enumerate(failed_requests, start=1):
        n["ref"] = f"N{i}"

    result["console_errors"] = console_errors
    result["failed_requests"] = failed_requests
    result["duration_ms"] = int((time.time() - started) * 1000)
    return result
