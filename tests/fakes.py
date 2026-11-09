"""
tests/fakes.py
---------------
Minimal fake Playwright objects implementing just enough of the real API
surface (page.url, page.goto, page.locator(...).count()/.is_visible()/
.click()/.fill(), page.inner_text, page.screenshot, page.on, context
managers) to exercise browser/executor.py and browser/assertions.py
without a real browser — this sandbox has no network access to download
the Chromium binary, so these fakes stand in for it in the unit tests.
"""

from __future__ import annotations


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def _matches(self):
        return self.page.elements.get(self.selector, [])

    def count(self):
        return len(self._matches())

    def is_visible(self, timeout=None):
        matches = self._matches()
        if not matches:
            raise Exception(f"No element for {self.selector}")
        return matches[0].get("visible", True)

    def evaluate(self, script):
        matches = self._matches()
        if not matches:
            raise Exception(f"No element for {self.selector}")
        return matches[0].get("validity", {"valid": None, "valueMissing": None, "validationMessage": ""})

    def click(self, timeout=None):
        matches = self._matches()
        if not matches:
            raise Exception(f"No element for {self.selector}")
        self.page.clicked.append(self.selector)
        nav = matches[0].get("navigates_to")
        if nav:
            self.page.url = nav

    def fill(self, value, timeout=None):
        matches = self._matches()
        if not matches:
            raise Exception(f"No element for {self.selector}")
        self.page.filled[self.selector] = value

    def select_option(self, value, timeout=None):
        self.page.filled[self.selector] = value

    def press(self, key, timeout=None):
        matches = self._matches()
        if not matches:
            raise Exception(f"No element for {self.selector}")
        self.page.pressed.append((self.selector, key))
        nav = matches[0].get("navigates_to")
        if nav:
            self.page.url = nav

    def check(self, timeout=None):
        self.page.checked.append(self.selector)

    def uncheck(self, timeout=None):
        self.page.checked.append(self.selector)

    @property
    def first(self):
        return self


class FakePage:
    """
    `elements` maps a selector string -> list of element dicts (len>1 means
    ambiguous). Each element dict may have "visible": bool, "navigates_to": str.
    """

    def __init__(self, start_url: str, elements: dict | None = None, body_text: str = ""):
        self.url = start_url
        self.elements = elements or {}
        self.body_text = body_text
        self.clicked = []
        self.filled = {}
        self.pressed = []
        self.checked = []
        self._handlers = {}

    def goto(self, url, timeout=None, wait_until=None):
        self.url = url

    def locator(self, selector):
        return FakeLocator(self, selector)

    def inner_text(self, selector, timeout=None):
        return self.body_text

    def screenshot(self, full_page=True):
        return b"\x89PNG\r\n\x1a\nFAKE"

    def on(self, event, handler):
        self._handlers[event] = handler

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        pass


class FakeContext:
    def __init__(self, page: FakePage):
        self._page = page

    def new_page(self):
        return self._page

    def set_default_timeout(self, ms):
        pass

    def close(self):
        pass


class FakeBrowser:
    def __init__(self, context: FakeContext):
        self._context = context

    def new_context(self, viewport=None):
        return self._context

    def close(self):
        pass


class FakeChromium:
    def __init__(self, browser: FakeBrowser):
        self._browser = browser

    def launch(self, headless=True):
        return self._browser


class FakePlaywrightCM:
    """Stands in for `with sync_playwright() as p:`."""

    def __init__(self, page: FakePage):
        context = FakeContext(page)
        browser = FakeBrowser(context)
        self.chromium = FakeChromium(browser)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def make_sync_playwright(page: FakePage):
    """Returns a zero-arg callable suitable for monkeypatching sync_playwright."""
    def _factory():
        return FakePlaywrightCM(page)
    return _factory
