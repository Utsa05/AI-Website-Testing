import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser.selectors import check_selector_on_page, selector_reliability, find_repair_candidate
from tests.fakes import FakePage


class TestCheckSelectorOnPage(unittest.TestCase):
    def test_missing_selector_string(self):
        page = FakePage(start_url="https://example.com")
        result = check_selector_on_page(page, None)
        self.assertFalse(result.ok)

    def test_selector_not_found(self):
        page = FakePage(start_url="https://example.com", elements={})
        result = check_selector_on_page(page, "#does-not-exist")
        self.assertFalse(result.ok)
        self.assertEqual(result.count, 0)

    def test_selector_unique_ok(self):
        page = FakePage(start_url="https://example.com", elements={"#submit": [{}]})
        result = check_selector_on_page(page, "#submit")
        self.assertTrue(result.ok)
        self.assertEqual(result.count, 1)

    def test_selector_ambiguous_rejected_by_default(self):
        page = FakePage(start_url="https://example.com", elements={"button": [{}, {}, {}]})
        result = check_selector_on_page(page, "button")
        self.assertFalse(result.ok)
        self.assertEqual(result.count, 3)
        self.assertIn("ambiguous", result.reason.lower())

    def test_ambiguous_allowed_when_uniqueness_not_required(self):
        page = FakePage(start_url="https://example.com", elements={"button": [{}, {}]})
        result = check_selector_on_page(page, "button", expect_unique=False)
        self.assertTrue(result.ok)


class TestSelectorReliability(unittest.TestCase):
    def test_priority_order(self):
        scores = {
            "[data-testid='submit']": selector_reliability("[data-testid='submit']"),
            "#submit": selector_reliability("#submit"),
            "input[name='email']": selector_reliability("input[name='email']"),
            "button[aria-label='Submit']": selector_reliability("button[aria-label='Submit']"),
            "role=button": selector_reliability("role=button"),
            "text=Submit": selector_reliability("text=Submit"),
        }
        ordered = sorted(scores, key=scores.get, reverse=True)
        self.assertEqual(ordered[0], "[data-testid='submit']")
        self.assertEqual(ordered[-1], "text=Submit")

    def test_none_selector_lowest(self):
        self.assertEqual(selector_reliability(None), 0)

    def test_find_repair_candidate_picks_best(self):
        pool = {"text=Submit", "#submit-btn", "button[aria-label='Submit']"}
        candidate = find_repair_candidate(pool)
        self.assertEqual(candidate, "#submit-btn")

    def test_find_repair_candidate_empty_pool(self):
        self.assertIsNone(find_repair_candidate(set()))


if __name__ == "__main__":
    unittest.main()
