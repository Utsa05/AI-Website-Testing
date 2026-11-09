"""
browser/selectors.py
---------------------
Selector reliability policy shared by the AI test planner (design-time
validation against discovery data) and the executor (runtime validation
against the live DOM before interacting with an element).

Preferred selector priority (best to worst):
  1. data-testid
  2. unique id
  3. unique name
  4. aria-label
  5. role + accessible name
  6. unique CSS selector
  7. text selector (final fallback only)

The executor never silently uses `.first` to paper over an ambiguous
selector — an ambiguous or missing selector either gets repaired (by
falling back to another discovered, reliable selector) or the test is
BLOCKED. A test interacting with the wrong element is worse than a
blocked test.
"""

from __future__ import annotations


def selector_reliability(selector: str | None) -> int:
    """
    Score a selector's reliability, higher = more reliable. Used to choose
    between candidate discovered selectors when repairing a test.
    """
    if not selector:
        return 0
    if "data-testid" in selector:
        return 6
    if selector.startswith("#"):
        return 5
    if "[name=" in selector:
        return 4
    if "aria-label" in selector:
        return 3
    if selector.startswith("role="):
        return 2
    if selector.startswith("text="):
        return 1
    return 2  # plain CSS selector: reasonably reliable, below name/id/testid


def classify_selector(selector: str | None) -> str | None:
    """
    Name which tier of the preferred selector priority a selector belongs
    to: data-testid > id > name > aria-label > role > css > text. Used so
    discovery/report data can say WHY a selector is considered reliable,
    not just assert that it is.
    """
    if not selector:
        return None
    if "data-testid" in selector:
        return "data-testid"
    if selector.startswith("#"):
        return "id"
    if "[name=" in selector:
        return "name"
    if "aria-label" in selector:
        return "aria-label"
    if selector.startswith("role="):
        return "role"
    if selector.startswith("text="):
        return "text"
    return "css"


def find_repair_candidate(known_selectors: set, group_hint: str | None = None) -> str | None:
    """Pick the most reliable selector out of a pool of known/discovered ones."""
    if not known_selectors:
        return None
    return sorted(known_selectors, key=selector_reliability, reverse=True)[0]


class SelectorCheckResult:
    def __init__(self, ok: bool, count: int = 0, reason: str = ""):
        self.ok = ok
        self.count = count
        self.reason = reason


def check_selector_on_page(page, selector: str, expect_unique: bool = True) -> SelectorCheckResult:
    """
    Runtime check: does `selector` resolve on the live page, and is it
    unambiguous? Never silently resolves ambiguity with `.first` — the
    caller decides whether to repair or BLOCK when this reports a problem.
    """
    if not selector:
        return SelectorCheckResult(False, 0, "No selector provided.")
    try:
        count = page.locator(selector).count()
    except Exception as e:  # noqa: BLE001
        return SelectorCheckResult(False, 0, f"Selector is invalid/unusable: {e}")

    if count == 0:
        return SelectorCheckResult(False, 0, f"Selector '{selector}' matched no elements.")
    if expect_unique and count > 1:
        return SelectorCheckResult(False, count, f"Selector '{selector}' is ambiguous — matched {count} elements.")
    return SelectorCheckResult(True, count, "")
