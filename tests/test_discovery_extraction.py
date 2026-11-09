import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser.discovery import _extract_element_info, _best_selector


class FakeLabel:
    def __init__(self, text):
        self._text = text

    def inner_text(self, timeout=None):
        return self._text


class FakeHandle:
    """Mimics just enough of a Playwright ElementHandle for _extract_element_info."""

    def __init__(self, tag, attrs=None, text="", visible=True, options=None, label_eval=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.text = text
        self.visible = visible
        self.options = options
        self.label_eval = label_eval

    def evaluate(self, script):
        if "tagName" in script:
            return self.tag
        if "options" in script:
            return self.options or []
        if "closest('label')" in script:
            return self.label_eval
        return None

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self, timeout=None):
        return self.text

    def is_visible(self):
        return self.visible


class FakeCountLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class FakePageForLabels:
    def __init__(self, label_map=None, selector_counts=None):
        self._label_map = label_map or {}
        # Maps selector -> match count for the live-uniqueness check that
        # _extract_element_info now performs. Defaults to 1 (unique) for
        # any selector not explicitly configured.
        self._selector_counts = selector_counts or {}

    def query_selector(self, selector):
        # selector looks like "label[for='email']"
        for elem_id, label_text in self._label_map.items():
            if f"'{elem_id}'" in selector:
                return FakeLabel(label_text)
        return None

    def locator(self, selector):
        return FakeCountLocator(self._selector_counts.get(selector, 1))


class TestExtractElementInfo(unittest.TestCase):
    def test_required_and_constraints_captured(self):
        handle = FakeHandle("input", attrs={
            "id": "username", "name": "username", "type": "text",
            "required": "", "minlength": "3", "maxlength": "20",
            "pattern": "[a-z]+", "autocomplete": "username",
        })
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertTrue(info.get("required"))
        self.assertEqual(info.get("minlength"), "3")
        self.assertEqual(info.get("maxlength"), "20")
        self.assertEqual(info.get("pattern"), "[a-z]+")
        self.assertEqual(info.get("autocomplete"), "username")
        self.assertEqual(info.get("selector"), "#username")

    def test_boolean_attrs_disabled_checked(self):
        handle = FakeHandle("input", attrs={"id": "agree", "type": "checkbox",
                                             "disabled": "", "checked": ""})
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertTrue(info.get("disabled"))
        self.assertTrue(info.get("checked"))

    def test_aria_attrs_captured(self):
        handle = FakeHandle("input", attrs={
            "id": "email", "aria-label": "Email address",
            "aria-labelledby": "email-label", "aria-describedby": "email-help",
        })
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertEqual(info.get("aria-label"), "Email address")
        self.assertEqual(info.get("aria-labelledby"), "email-label")
        self.assertEqual(info.get("aria-describedby"), "email-help")

    def test_associated_label_text_via_for_attribute(self):
        handle = FakeHandle("input", attrs={"id": "email"})
        page = FakePageForLabels(label_map={"email": "Email Address"})
        info = _extract_element_info(handle, page)
        self.assertEqual(info.get("label_text"), "Email Address")

    def test_associated_label_text_via_ancestor(self):
        handle = FakeHandle("input", attrs={}, label_eval="Full Name")
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertEqual(info.get("label_text"), "Full Name")

    def test_select_options_captured(self):
        handle = FakeHandle("select", attrs={"id": "country", "name": "country"},
                             options=[{"value": "us", "text": "United States"},
                                      {"value": "nz", "text": "New Zealand"}])
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertEqual(len(info.get("options", [])), 2)
        self.assertEqual(info["options"][0]["value"], "us")

    def test_data_testid_selector_priority(self):
        handle = FakeHandle("button", attrs={"data-testid": "submit-btn", "id": "submit"})
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertEqual(info["selector"], "[data-testid='submit-btn']")

    def test_selector_priority_falls_back_to_id_then_name(self):
        self.assertEqual(_best_selector({"id": "x"}, "input"), "#x")
        self.assertEqual(_best_selector({"name": "y"}, "input"), "input[name='y']")
        self.assertEqual(_best_selector({"href": "/about"}, "a"), "a[href='/about']")


    def test_selector_marked_unique_when_dom_confirms_it(self):
        handle = FakeHandle("input", attrs={"id": "email"})
        page = FakePageForLabels(selector_counts={"#email": 1})
        info = _extract_element_info(handle, page)
        self.assertTrue(info["selector_unique"])
        self.assertEqual(info["selector_type"], "id")
        self.assertEqual(info["selector_match_count"], 1)

    def test_selector_marked_not_unique_when_dom_shows_multiple_matches(self):
        # Reliability is never just asserted — it's actually checked
        # against the live DOM, so a technically-derived "id" selector
        # that happens to match more than one element must be flagged.
        handle = FakeHandle("button", attrs={"name": "submit"})
        page = FakePageForLabels(selector_counts={"button[name='submit']": 3})
        info = _extract_element_info(handle, page)
        self.assertFalse(info["selector_unique"])
        self.assertEqual(info["selector_match_count"], 3)

    def test_selector_candidates_recorded_in_priority_order(self):
        handle = FakeHandle("input", attrs={"id": "email", "name": "email",
                                             "aria-label": "Email address"})
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertEqual(info["selector"], "#email")
        self.assertIn("input[name='email']", info["selector_candidates"])
        self.assertIn("input[aria-label='Email address']", info["selector_candidates"])
        # id candidate ranks ahead of name and aria-label candidates.
        self.assertLess(info["selector_candidates"].index("#email"),
                         info["selector_candidates"].index("input[name='email']"))

    def test_no_candidates_selector_is_none(self):
        handle = FakeHandle("div", attrs={})
        page = FakePageForLabels()
        info = _extract_element_info(handle, page)
        self.assertIsNone(info["selector"])
        self.assertIsNone(info["selector_type"])
        self.assertIsNone(info["selector_unique"])
        self.assertEqual(info["selector_candidates"], [])


if __name__ == "__main__":
    unittest.main()
