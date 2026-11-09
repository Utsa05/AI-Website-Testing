import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser.executor as executor
from tests.fakes import FakePage, make_sync_playwright


class TestExecutorBlockedBeforeBrowser(unittest.TestCase):
    """These paths return before touching Playwright at all, so no fake needed."""

    def test_no_assertions_is_blocked(self):
        test = {"id": "T1", "name": "no assertions", "steps": [], "assertions": []}
        result = executor.execute_test("https://example.com", "run1", test)
        self.assertEqual(result["status"], "BLOCKED")

    def test_invalid_assertion_is_blocked_not_a_fake_pass(self):
        # The exact regression this whole correction pass is about: an
        # ASSERT_URL with an empty value must never be able to produce PASS.
        test = {
            "id": "T2", "name": "bad assertion",
            "steps": [{"action": "OPEN", "url": "/", "selector": None, "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": None, "selector": None, "value": ""}],
        }
        result = executor.execute_test("https://example.com", "run1", test)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertNotEqual(result["status"], "PASS")


class TestExecutorPassFail(unittest.TestCase):
    def _run_with_fake_page(self, page, test):
        with mock.patch.object(executor, "sync_playwright", make_sync_playwright(page)), \
             mock.patch.object(executor, "save_screenshot", return_value="fake_artifact.png"):
            return executor.execute_test("https://example.com", "run1", test, headless=True)

    def test_pass_when_assertion_holds(self):
        page = FakePage(start_url="https://example.com")
        test = {
            "id": "T3", "name": "reach login",
            "steps": [{"action": "OPEN", "url": "/login", "selector": None, "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "/login"}],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["status"], "PASS")

    def test_fail_when_assertion_does_not_hold(self):
        page = FakePage(start_url="https://example.com")
        test = {
            "id": "T4", "name": "reach dashboard but actually stays home",
            "steps": [{"action": "OPEN", "url": "/", "selector": None, "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "/dashboard"}],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["status"], "FAIL")

    def test_browser_action_succeeding_is_not_automatically_pass(self):
        # A CLICK that succeeds but whose assertion fails must still be FAIL,
        # never PASS just because "the button was clicked" (Part 16).
        page = FakePage(start_url="https://example.com/login",
                         elements={"#submit": [{"navigates_to": "https://example.com/login?error=1"}]})
        test = {
            "id": "T5", "name": "invalid login should redirect to dashboard (it won't)",
            "steps": [{"action": "CLICK", "url": None, "selector": "#submit", "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "/dashboard"}],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(page.clicked), 1)  # the action DID execute

    def test_error_on_selector_timeout_not_bug_like_status(self):
        page = FakePage(start_url="https://example.com", elements={})  # #missing not present
        test = {
            "id": "T6", "name": "click something that isn't there",
            "steps": [{"action": "CLICK", "url": None, "selector": "#missing", "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "/x"}],
        }
        result = self._run_with_fake_page(page, test)
        # Missing selector on a SELECTOR_ACTION is caught by the reliability
        # check first, which reports BLOCKED (never a false ERROR-as-bug and
        # never a false PASS).
        self.assertEqual(result["status"], "BLOCKED")

    def test_ambiguous_selector_blocks_rather_than_using_first(self):
        page = FakePage(start_url="https://example.com", elements={"button": [{}, {}]})
        test = {
            "id": "T7", "name": "ambiguous submit button",
            "steps": [{"action": "CLICK", "url": None, "selector": "button", "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "/x"}],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(page.clicked), 0)  # never silently clicked .first


    def test_press_without_selector_is_blocked(self):
        # Part 6 (PRESS action safety, Option A): PRESS must target a
        # specific, verified element rather than falling back to a global
        # page.keyboard.press() whose target we can't confirm.
        page = FakePage(start_url="https://example.com")
        test = {
            "id": "T8", "name": "press enter globally",
            "steps": [{"action": "PRESS", "url": None, "selector": None, "value": "Enter"}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "/x"}],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["status"], "BLOCKED")

    def test_press_with_valid_selector_executes(self):
        page = FakePage(start_url="https://example.com",
                         elements={"#search": [{"navigates_to": "https://example.com/results"}]})
        test = {
            "id": "T9", "name": "press enter in search box",
            "steps": [{"action": "PRESS", "url": None, "selector": "#search", "value": "Enter"}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "/results"}],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(page.pressed, [("#search", "Enter")])

    def test_ambiguous_assertion_selector_blocks_test_not_fail(self):
        # An ambiguous ASSERT_VISIBLE selector must produce test-level
        # BLOCKED, not a false FAIL (or worse, a lucky PASS on .first()).
        page = FakePage(start_url="https://example.com",
                         elements={".error": [{"visible": True}, {"visible": False}]})
        test = {
            "id": "T10", "name": "ambiguous visible check",
            "steps": [{"action": "OPEN", "url": "/", "selector": None, "value": None}],
            "assertions": [{"type": "ASSERT_VISIBLE", "selector": ".error", "operator": None, "value": None}],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["status"], "BLOCKED")

    def test_structured_evidence_refs_assigned(self):
        page = FakePage(start_url="https://example.com")
        test = {
            "id": "T11", "name": "ref assignment check",
            "steps": [{"action": "OPEN", "url": "/", "selector": None, "value": None}],
            "assertions": [
                {"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "example.com"},
            ],
        }
        result = self._run_with_fake_page(page, test)
        self.assertEqual(result["assertion_results"][0]["ref"], "A1")


class TestChromiumMissingErrorMessage(unittest.TestCase):
    def test_missing_chromium_gives_actionable_error(self):
        class BrokenChromium:
            def launch(self, headless=True):
                raise Exception("Executable doesn't exist at /fake/path/chromium")

        class BrokenPlaywrightCM:
            def __init__(self):
                self.chromium = BrokenChromium()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        test = {
            "id": "T20", "name": "any test",
            "steps": [{"action": "OPEN", "url": "/", "selector": None, "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": "CONTAINS", "selector": None, "value": "x"}],
        }
        with mock.patch.object(executor, "sync_playwright", lambda: BrokenPlaywrightCM()):
            result = executor.execute_test("https://example.com", "run1", test)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("playwright install chromium", result["error"])


if __name__ == "__main__":
    unittest.main()
