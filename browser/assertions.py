"""
browser/assertions.py
----------------------
Evaluates outcome assertions against the live Playwright page. Every
executed test must be judged PASS/FAIL by real assertion evaluation —
never by "the actions ran without throwing".
"""

from __future__ import annotations


class AssertionError_(Exception):
    """Raised internally; converted into a structured result by run_assertion."""


VALID_URL_OPERATORS = ("EQUALS", "CONTAINS", "STARTS_WITH")
VALID_FIELD_VALIDITY_VALUES = ("valid", "invalid")
KNOWN_ASSERTION_TYPES = (
    "ASSERT_TEXT", "ASSERT_TEXT_VISIBLE", "ASSERT_VISIBLE", "ASSERT_NOT_VISIBLE",
    "ASSERT_URL", "ASSERT_COUNT", "ASSERT_ELEMENT", "ASSERT_FIELD_VALIDITY",
)


def validate_assertion(assertion: dict) -> str | None:
    """
    Statically validate an assertion dict BEFORE execution. Returns None if
    the assertion is well-formed and meaningful, or a human-readable reason
    it must be rejected/repaired otherwise.

    This is what stops a meaningless assertion (e.g. ASSERT_URL with an
    empty value, or a missing operator) from ever reaching a test plan in
    the first place — `run_assertion` above is the runtime backstop, this
    is the design-time gate.
    """
    if not isinstance(assertion, dict):
        return "Assertion is not a valid object."

    a_type = (assertion.get("type") or "").upper()
    if a_type not in KNOWN_ASSERTION_TYPES:
        return f"Unknown or missing assertion type: {assertion.get('type')!r}"

    if a_type == "ASSERT_URL":
        value = assertion.get("value")
        operator = (assertion.get("operator") or "").upper()
        if not value:
            return "ASSERT_URL must have a non-empty expected value."
        if operator not in VALID_URL_OPERATORS:
            return (f"ASSERT_URL must have an explicit operator "
                    f"(one of {VALID_URL_OPERATORS}); got {assertion.get('operator')!r}.")
        return None

    if a_type in ("ASSERT_TEXT", "ASSERT_TEXT_VISIBLE"):
        if not assertion.get("value"):
            return f"{a_type} must have a non-empty expected text value."
        return None

    if a_type in ("ASSERT_VISIBLE", "ASSERT_NOT_VISIBLE", "ASSERT_ELEMENT"):
        if not assertion.get("selector"):
            return f"{a_type} must have a selector."
        return None

    if a_type == "ASSERT_FIELD_VALIDITY":
        if not assertion.get("selector"):
            return "ASSERT_FIELD_VALIDITY must have a selector."
        value = (assertion.get("value") or "").lower()
        if value not in VALID_FIELD_VALIDITY_VALUES:
            return (f"ASSERT_FIELD_VALIDITY must have value 'valid' or 'invalid' "
                    f"(browser constraint-validation state), got {assertion.get('value')!r}.")
        return None

    if a_type == "ASSERT_COUNT":
        if not assertion.get("selector"):
            return "ASSERT_COUNT must have a selector."
        if assertion.get("value") in (None, ""):
            return "ASSERT_COUNT must have an expected count value."
        try:
            int(assertion.get("value"))
        except (TypeError, ValueError):
            return f"ASSERT_COUNT value must be an integer, got {assertion.get('value')!r}."
        return None

    return None


def _locate(page, selector: str):
    """
    Count how many live elements match `selector`. Returns the count; the
    caller decides what 0 / 1 / >1 means for its assertion type. This is
    the single place that replaces every `.locator(selector).first` call
    that used to silently pick an element without checking how many there
    actually were.
    """
    return page.locator(selector).count()


def run_assertion(page, assertion: dict) -> dict:
    """
    Evaluate a single assertion dict of the form:
      {"type": "ASSERT_TEXT_VISIBLE", "value": "..."} etc.

    Returns:
      {"type": ..., "value": ..., "passed": bool, "blocked": bool, "detail": str}

    `blocked` is set when an element-identity assertion (ASSERT_VISIBLE /
    ASSERT_NOT_VISIBLE / ASSERT_ELEMENT) matched more than one element —
    we never silently evaluate `.first` in that case, since that would
    mean the assertion might be checking the wrong element. A blocked
    assertion always has `passed = False` and the executor treats a test
    with any blocked assertion as test-status BLOCKED, not FAIL.
    """
    a_type = (assertion.get("type") or "").upper()
    value = assertion.get("value")
    selector = assertion.get("selector")
    operator = assertion.get("operator")
    result = {"type": a_type, "value": value, "selector": selector, "operator": operator,
              "passed": False, "blocked": False, "detail": ""}

    try:
        if a_type in ("ASSERT_TEXT", "ASSERT_TEXT_VISIBLE"):
            if not value:
                result["detail"] = "No expected text value provided."
                return result
            body_text = page.inner_text("body")
            found = value.lower() in body_text.lower()
            result["passed"] = found
            result["detail"] = (f"Text '{value}' {'found' if found else 'NOT found'} on page.")

        elif a_type in ("ASSERT_VISIBLE", "ASSERT_NOT_VISIBLE", "ASSERT_ELEMENT"):
            if not selector:
                result["detail"] = "No selector provided."
                return result

            count = _locate(page, selector)

            if count > 1:
                # Never guess which of several matches is "the" element —
                # this is an ambiguous assertion, not a pass or a fail.
                result["blocked"] = True
                result["passed"] = False
                result["detail"] = (f"Selector '{selector}' is ambiguous — matched {count} elements. "
                                     f"Cannot evaluate {a_type} against a specific element without "
                                     f"risking checking the wrong one.")
                return result

            if count == 0:
                if a_type == "ASSERT_NOT_VISIBLE":
                    # An element that doesn't exist at all is trivially "not
                    # visible" — this is the one case where zero matches is
                    # a legitimate PASS, not a failure to evaluate.
                    result["passed"] = True
                    result["detail"] = f"Selector '{selector}' matched no elements (trivially not visible)."
                else:
                    result["passed"] = False
                    result["detail"] = (f"Selector '{selector}' matched no elements — "
                                         f"{'it cannot be visible' if a_type == 'ASSERT_VISIBLE' else 'it does not exist'}.")
                return result

            # Exactly one match: safe to evaluate directly, no .first needed.
            locator = page.locator(selector)
            if a_type == "ASSERT_VISIBLE":
                visible = locator.is_visible(timeout=3000)
                result["passed"] = visible
                result["detail"] = f"Selector '{selector}' visible={visible}"
            elif a_type == "ASSERT_NOT_VISIBLE":
                try:
                    visible = locator.is_visible(timeout=3000)
                except Exception:
                    visible = False
                result["passed"] = not visible
                result["detail"] = f"Selector '{selector}' visible={visible}"
            else:  # ASSERT_ELEMENT
                result["passed"] = True
                result["detail"] = f"Selector '{selector}' exists=True (exactly one match)"

        elif a_type == "ASSERT_FIELD_VALIDITY":
            if not selector:
                result["detail"] = "No selector provided."
                return result

            count = _locate(page, selector)
            if count > 1:
                result["blocked"] = True
                result["passed"] = False
                result["detail"] = (f"Selector '{selector}' is ambiguous — matched {count} elements. "
                                     f"Cannot evaluate constraint-validation state against a specific element.")
                return result
            if count == 0:
                result["passed"] = False
                result["detail"] = f"Selector '{selector}' matched no elements — cannot check its validity state."
                return result

            # Read the browser's OWN native constraint-validation state —
            # this is deterministic DOM/browser behaviour (element.validity,
            # element.checkValidity()), not an assumption we're making about
            # what the application does with that state. A `required`
            # attribute genuinely guarantees this check, independent of the
            # application's own JS/redirect behaviour — which is exactly the
            # gap the old "required => URL must not change" fallback logic
            # incorrectly assumed.
            try:
                validity = page.locator(selector).evaluate(
                    "el => ({valid: (typeof el.checkValidity === 'function') ? el.checkValidity() : null, "
                    "valueMissing: (el.validity ? el.validity.valueMissing : null), "
                    "validationMessage: el.validationMessage || ''})"
                )
            except Exception as e:  # noqa: BLE001
                result["detail"] = f"Could not read constraint-validation state for '{selector}': {e}"
                return result

            is_valid = validity.get("valid") if isinstance(validity, dict) else None
            if is_valid is None:
                result["detail"] = (f"Element '{selector}' does not support native constraint validation "
                                     f"(no checkValidity()) — cannot establish deterministic evidence here.")
                return result

            expected_valid = (value or "").lower() == "valid"
            result["passed"] = (is_valid == expected_valid)
            result["detail"] = (f"Selector '{selector}' constraint-validation state: valid={is_valid}, "
                                 f"valueMissing={validity.get('valueMissing')}, "
                                 f"validationMessage='{validity.get('validationMessage')}' "
                                 f"(expected {'valid' if expected_valid else 'invalid'})")

        elif a_type == "ASSERT_URL":
            operator = (assertion.get("operator") or "").upper()
            current = page.url

            # Hard rule: an ASSERT_URL with no meaningful expected value must
            # NEVER pass — this is exactly the false-positive bug where
            # `"" in current_url` was always True.
            if not value:
                result["detail"] = ("ASSERT_URL has no expected value — rejected. "
                                     "An empty URL assertion can never pass.")
                return result

            if operator not in ("EQUALS", "CONTAINS", "STARTS_WITH"):
                result["detail"] = (f"ASSERT_URL is missing a valid operator "
                                     f"(got '{assertion.get('operator')!r}'; must be one of "
                                     f"EQUALS, CONTAINS, STARTS_WITH) — rejected.")
                return result

            if operator == "EQUALS":
                passed = current == value
            elif operator == "CONTAINS":
                passed = value in current
            else:  # STARTS_WITH
                passed = current.startswith(value)

            result["operator"] = operator
            result["passed"] = passed
            result["detail"] = (f"Current URL '{current}' {'satisfies' if passed else 'does NOT satisfy'} "
                                 f"{operator} '{value}'")

        elif a_type == "ASSERT_COUNT":
            if not selector:
                result["detail"] = "No selector provided."
                return result
            count = page.locator(selector).count()
            expected = int(value) if value is not None else None
            passed = (count == expected) if expected is not None else count > 0
            result["passed"] = passed
            result["detail"] = f"Selector '{selector}' matched {count} elements (expected {expected})"

        else:
            result["detail"] = f"Unknown assertion type: {a_type}"

    except Exception as e:  # noqa: BLE001
        result["passed"] = False
        result["detail"] = f"Assertion evaluation error: {e}"

    return result
