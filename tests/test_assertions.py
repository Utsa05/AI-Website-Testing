import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser.assertions import run_assertion, validate_assertion
from tests.fakes import FakePage


class TestValidateAssertion(unittest.TestCase):
    """Design-time gate: catches the exact ASSERT_URL '' false-positive bug."""

    def test_assert_url_empty_value_rejected(self):
        reason = validate_assertion({"type": "ASSERT_URL", "operator": "CONTAINS", "value": ""})
        self.assertIsNotNone(reason)

    def test_assert_url_missing_value_rejected(self):
        reason = validate_assertion({"type": "ASSERT_URL", "operator": "CONTAINS"})
        self.assertIsNotNone(reason)

    def test_assert_url_missing_operator_rejected(self):
        reason = validate_assertion({"type": "ASSERT_URL", "value": "/login"})
        self.assertIsNotNone(reason)

    def test_assert_url_invalid_operator_rejected(self):
        reason = validate_assertion({"type": "ASSERT_URL", "operator": "MATCHES", "value": "/login"})
        self.assertIsNotNone(reason)

    def test_assert_url_valid_contains_accepted(self):
        reason = validate_assertion({"type": "ASSERT_URL", "operator": "CONTAINS", "value": "/login"})
        self.assertIsNone(reason)

    def test_assert_url_valid_equals_accepted(self):
        reason = validate_assertion(
            {"type": "ASSERT_URL", "operator": "EQUALS", "value": "https://example.com/login"})
        self.assertIsNone(reason)

    def test_assert_url_valid_starts_with_accepted(self):
        reason = validate_assertion(
            {"type": "ASSERT_URL", "operator": "STARTS_WITH", "value": "https://example.com"})
        self.assertIsNone(reason)

    def test_assert_text_empty_value_rejected(self):
        reason = validate_assertion({"type": "ASSERT_TEXT", "value": ""})
        self.assertIsNotNone(reason)

    def test_assert_visible_missing_selector_rejected(self):
        reason = validate_assertion({"type": "ASSERT_VISIBLE"})
        self.assertIsNotNone(reason)

    def test_assert_count_non_integer_value_rejected(self):
        reason = validate_assertion({"type": "ASSERT_COUNT", "selector": "li", "value": "many"})
        self.assertIsNotNone(reason)

    def test_assert_count_valid_accepted(self):
        reason = validate_assertion({"type": "ASSERT_COUNT", "selector": "li", "value": "3"})
        self.assertIsNone(reason)

    def test_unknown_type_rejected(self):
        reason = validate_assertion({"type": "ASSERT_MAGIC"})
        self.assertIsNotNone(reason)


class TestRunAssertionUrl(unittest.TestCase):
    """Runtime backstop: even if a bad assertion slipped through, it can never pass."""

    def test_empty_value_never_passes(self):
        page = FakePage(start_url="https://example.com/anything")
        result = run_assertion(page, {"type": "ASSERT_URL", "operator": "CONTAINS", "value": ""})
        self.assertFalse(result["passed"])

    def test_missing_operator_never_passes(self):
        page = FakePage(start_url="https://example.com/login")
        result = run_assertion(page, {"type": "ASSERT_URL", "value": "/login"})
        self.assertFalse(result["passed"])

    def test_contains_pass(self):
        page = FakePage(start_url="https://example.com/auth/login")
        result = run_assertion(page, {"type": "ASSERT_URL", "operator": "CONTAINS", "value": "/login"})
        self.assertTrue(result["passed"])

    def test_contains_fail_on_incorrect_url(self):
        page = FakePage(start_url="https://example.com/home")
        result = run_assertion(page, {"type": "ASSERT_URL", "operator": "CONTAINS", "value": "/login"})
        self.assertFalse(result["passed"])

    def test_equals_pass(self):
        page = FakePage(start_url="https://example.com/login")
        result = run_assertion(page, {"type": "ASSERT_URL", "operator": "EQUALS", "value": "https://example.com/login"})
        self.assertTrue(result["passed"])

    def test_equals_fail_on_trailing_path(self):
        page = FakePage(start_url="https://example.com/login/extra")
        result = run_assertion(page, {"type": "ASSERT_URL", "operator": "EQUALS", "value": "https://example.com/login"})
        self.assertFalse(result["passed"])

    def test_starts_with_pass(self):
        page = FakePage(start_url="https://example.com/dashboard?x=1")
        result = run_assertion(page, {"type": "ASSERT_URL", "operator": "STARTS_WITH", "value": "https://example.com/dashboard"})
        self.assertTrue(result["passed"])


class TestRunAssertionOthers(unittest.TestCase):
    def test_assert_text_visible_found(self):
        page = FakePage(start_url="https://example.com", body_text="Welcome back, please log in")
        result = run_assertion(page, {"type": "ASSERT_TEXT_VISIBLE", "value": "log in"})
        self.assertTrue(result["passed"])

    def test_assert_text_visible_not_found(self):
        page = FakePage(start_url="https://example.com", body_text="Welcome back")
        result = run_assertion(page, {"type": "ASSERT_TEXT_VISIBLE", "value": "invalid password"})
        self.assertFalse(result["passed"])

    def test_assert_visible_true(self):
        page = FakePage(start_url="https://example.com",
                         elements={"#error": [{"visible": True}]})
        result = run_assertion(page, {"type": "ASSERT_VISIBLE", "selector": "#error"})
        self.assertTrue(result["passed"])

    def test_assert_count_matches(self):
        page = FakePage(start_url="https://example.com",
                         elements={"li.item": [{}, {}, {}]})
        result = run_assertion(page, {"type": "ASSERT_COUNT", "selector": "li.item", "value": "3"})
        self.assertTrue(result["passed"])

    def test_assert_count_mismatch(self):
        page = FakePage(start_url="https://example.com",
                         elements={"li.item": [{}, {}]})
        result = run_assertion(page, {"type": "ASSERT_COUNT", "selector": "li.item", "value": "3"})
        self.assertFalse(result["passed"])


class TestRunAssertionElementAmbiguity(unittest.TestCase):
    """Part 2 of the second correction pass: never silently use `.first`."""

    def test_assert_visible_ambiguous_selector_is_blocked_not_first(self):
        page = FakePage(start_url="https://example.com",
                         elements={"button": [{"visible": True}, {"visible": False}]})
        result = run_assertion(page, {"type": "ASSERT_VISIBLE", "selector": "button"})
        self.assertTrue(result["blocked"])
        self.assertFalse(result["passed"])

    def test_assert_visible_zero_matches_fails(self):
        page = FakePage(start_url="https://example.com", elements={})
        result = run_assertion(page, {"type": "ASSERT_VISIBLE", "selector": "#missing"})
        self.assertFalse(result["blocked"])
        self.assertFalse(result["passed"])

    def test_assert_visible_single_match_evaluates_normally(self):
        page = FakePage(start_url="https://example.com", elements={"#ok": [{"visible": True}]})
        result = run_assertion(page, {"type": "ASSERT_VISIBLE", "selector": "#ok"})
        self.assertFalse(result["blocked"])
        self.assertTrue(result["passed"])

    def test_assert_not_visible_ambiguous_selector_is_blocked(self):
        page = FakePage(start_url="https://example.com",
                         elements={".item": [{"visible": True}, {"visible": True}]})
        result = run_assertion(page, {"type": "ASSERT_NOT_VISIBLE", "selector": ".item"})
        self.assertTrue(result["blocked"])
        self.assertFalse(result["passed"])

    def test_assert_not_visible_zero_matches_passes_trivially(self):
        # An element that doesn't exist at all is trivially "not visible" —
        # documented special case, distinct from ASSERT_VISIBLE/ASSERT_ELEMENT.
        page = FakePage(start_url="https://example.com", elements={})
        result = run_assertion(page, {"type": "ASSERT_NOT_VISIBLE", "selector": "#gone"})
        self.assertFalse(result["blocked"])
        self.assertTrue(result["passed"])

    def test_assert_element_ambiguous_selector_is_blocked(self):
        page = FakePage(start_url="https://example.com", elements={"li": [{}, {}, {}]})
        result = run_assertion(page, {"type": "ASSERT_ELEMENT", "selector": "li"})
        self.assertTrue(result["blocked"])
        self.assertFalse(result["passed"])

    def test_assert_element_zero_matches_fails(self):
        page = FakePage(start_url="https://example.com", elements={})
        result = run_assertion(page, {"type": "ASSERT_ELEMENT", "selector": "#nope"})
        self.assertFalse(result["blocked"])
        self.assertFalse(result["passed"])

    def test_assert_element_single_match_passes(self):
        page = FakePage(start_url="https://example.com", elements={"#thing": [{}]})
        result = run_assertion(page, {"type": "ASSERT_ELEMENT", "selector": "#thing"})
        self.assertFalse(result["blocked"])
        self.assertTrue(result["passed"])


class TestAssertFieldValidity(unittest.TestCase):
    """Issue 2 of the third correction pass: test the browser's OWN native
    constraint-validation state (validity.valueMissing / checkValidity()),
    never an assumption that a required field implies the URL won't change."""

    def test_empty_required_field_reports_value_missing(self):
        page = FakePage(start_url="https://example.com/register", elements={
            "#email": [{"validity": {"valid": False, "valueMissing": True,
                                      "validationMessage": "Please fill out this field."}}],
        })
        result = run_assertion(page, {"type": "ASSERT_FIELD_VALIDITY", "selector": "#email", "value": "invalid"})
        self.assertTrue(result["passed"])

    def test_filled_required_field_reports_value_missing_false(self):
        page = FakePage(start_url="https://example.com/register", elements={
            "#email": [{"validity": {"valid": True, "valueMissing": False, "validationMessage": ""}}],
        })
        result = run_assertion(page, {"type": "ASSERT_FIELD_VALIDITY", "selector": "#email", "value": "valid"})
        self.assertTrue(result["passed"])

    def test_expecting_invalid_but_field_is_actually_valid_fails(self):
        page = FakePage(start_url="https://example.com/register", elements={
            "#email": [{"validity": {"valid": True, "valueMissing": False, "validationMessage": ""}}],
        })
        result = run_assertion(page, {"type": "ASSERT_FIELD_VALIDITY", "selector": "#email", "value": "invalid"})
        self.assertFalse(result["passed"])

    def test_ambiguous_selector_is_blocked_not_first(self):
        page = FakePage(start_url="https://example.com/register", elements={
            "input": [{"validity": {"valid": False, "valueMissing": True, "validationMessage": "x"}},
                      {"validity": {"valid": True, "valueMissing": False, "validationMessage": ""}}],
        })
        result = run_assertion(page, {"type": "ASSERT_FIELD_VALIDITY", "selector": "input", "value": "invalid"})
        self.assertTrue(result["blocked"])
        self.assertFalse(result["passed"])

    def test_zero_matches_fails(self):
        page = FakePage(start_url="https://example.com/register", elements={})
        result = run_assertion(page, {"type": "ASSERT_FIELD_VALIDITY", "selector": "#missing", "value": "invalid"})
        self.assertFalse(result["blocked"])
        self.assertFalse(result["passed"])

    def test_element_without_constraint_validation_support_does_not_pass_or_fail_falsely(self):
        # Custom JS validation / a non-form-control element: checkValidity
        # is unavailable, so we must not manufacture an unsupported claim
        # either way — the assertion simply cannot be evaluated as PASS.
        page = FakePage(start_url="https://example.com/register", elements={
            "#custom-widget": [{"validity": {"valid": None, "valueMissing": None, "validationMessage": ""}}],
        })
        result = run_assertion(page, {"type": "ASSERT_FIELD_VALIDITY", "selector": "#custom-widget", "value": "invalid"})
        self.assertFalse(result["passed"])

    def test_validate_assertion_rejects_missing_selector(self):
        self.assertIsNotNone(validate_assertion({"type": "ASSERT_FIELD_VALIDITY", "value": "invalid"}))

    def test_validate_assertion_rejects_bad_value(self):
        self.assertIsNotNone(
            validate_assertion({"type": "ASSERT_FIELD_VALIDITY", "selector": "#x", "value": "maybe"}))

    def test_validate_assertion_accepts_valid_and_invalid(self):
        self.assertIsNone(validate_assertion({"type": "ASSERT_FIELD_VALIDITY", "selector": "#x", "value": "invalid"}))
        self.assertIsNone(validate_assertion({"type": "ASSERT_FIELD_VALIDITY", "selector": "#x", "value": "valid"}))


if __name__ == "__main__":
    unittest.main()
