import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.test_planner import build_fallback_plan


def make_discovery():
    return {
        "start_url": "https://example.com/",
        "pages": [
            {
                "url": "https://example.com/",
                "links": [
                    {"absolute_href": "https://example.com/dashboard", "selector": "a[href='/dashboard']"},
                ],
                "forms": [{"selector": "form#register"}],
                "inputs": [
                    {"selector": "#email", "required": True, "type": "email"},
                ],
                "buttons": [
                    {"selector": "#submit", "text": "Submit"},
                ],
            }
        ],
    }


class TestFallbackPlanF1(unittest.TestCase):
    """Issue 1: home-page URL stability must never be an independent
    bug-detection rule (a legitimate redirect is not a bug)."""

    def test_f1_uses_weak_heuristic_not_independent_source(self):
        plan = build_fallback_plan(make_discovery())
        f1 = next(t for t in plan["tests"] if t["id"] == "F1")
        self.assertEqual(f1["expected_outcome_source"], "WEAK_HEURISTIC")

    def test_f1_is_flagged_diagnostic_only(self):
        plan = build_fallback_plan(make_discovery())
        f1 = next(t for t in plan["tests"] if t["id"] == "F1")
        self.assertTrue(any("diagnostic" in w.lower() or "never" in w.lower()
                             for w in f1["validation_warnings"]))


class TestFallbackPlanF2(unittest.TestCase):
    """F2 (discovered link target) legitimately IS independent evidence —
    untouched by this correction pass."""

    def test_f2_uses_discovered_link_target_source(self):
        plan = build_fallback_plan(make_discovery())
        f2 = next(t for t in plan["tests"] if t["id"] == "F2")
        self.assertEqual(f2["expected_outcome_source"], "DISCOVERED_LINK_TARGET")


class TestFallbackPlanF3(unittest.TestCase):
    """Issue 2: the required-field fallback tests the browser's own
    constraint-validation state, never a URL-unchanged assumption."""

    def test_f3_uses_field_validity_assertion_not_url(self):
        plan = build_fallback_plan(make_discovery())
        f3 = next(t for t in plan["tests"] if t["id"] == "F3")
        self.assertEqual(f3["assertions"][0]["type"], "ASSERT_FIELD_VALIDITY")
        # The independent, HTML_REQUIRED_ATTRIBUTE-sourced test must not
        # contain any ASSERT_URL assertion at all — that would smuggle the
        # "URL must not change" assumption back into independent evidence.
        self.assertFalse(any(a["type"] == "ASSERT_URL" for a in f3["assertions"]))

    def test_f3_source_is_html_required_attribute(self):
        plan = build_fallback_plan(make_discovery())
        f3 = next(t for t in plan["tests"] if t["id"] == "F3")
        self.assertEqual(f3["expected_outcome_source"], "HTML_REQUIRED_ATTRIBUTE")

    def test_f3_does_not_click_submit(self):
        # F3 only checks constraint-validation state on load — it must not
        # attempt a submission (that risk belongs to the diagnostic F3b).
        plan = build_fallback_plan(make_discovery())
        f3 = next(t for t in plan["tests"] if t["id"] == "F3")
        self.assertFalse(any(s["action"] == "CLICK" for s in f3["steps"]))

    def test_f3b_diagnostic_test_uses_weak_heuristic(self):
        plan = build_fallback_plan(make_discovery())
        f3b = next(t for t in plan["tests"] if t["id"] == "F3b")
        self.assertEqual(f3b["expected_outcome_source"], "WEAK_HEURISTIC")
        self.assertTrue(any("diagnostic" in w.lower() for w in f3b["validation_warnings"]))

    def test_f3b_still_present_for_investigation(self):
        # The submission-behaviour observation is preserved (Preserve
        # Existing Functionality) — just correctly downgraded to
        # non-independent, diagnostic-only status.
        plan = build_fallback_plan(make_discovery())
        ids = [t["id"] for t in plan["tests"]]
        self.assertIn("F3b", ids)

    def test_no_required_input_blocks_instead_of_guessing(self):
        discovery = make_discovery()
        discovery["pages"][0]["inputs"] = [{"selector": "#email", "required": False, "type": "email"}]
        plan = build_fallback_plan(discovery)
        ids = [t["id"] for t in plan["tests"]]
        self.assertNotIn("F3", ids)
        self.assertTrue(any(b["id"] == "FB3" for b in plan["blocked"]))


if __name__ == "__main__":
    unittest.main()
