"""
browser/discovery.py
---------------------
Uses a real Playwright browser session to open a target URL, inspect the
DOM, and build a structured picture of the application: pages, forms,
inputs, buttons, links, etc. This structured discovery data becomes the
context given to the AI test planner.

Scope is intentionally limited (same-origin, shallow depth, bounded page
count) as required by the spec — this is not a general-purpose crawler.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MAX_PAGES = 6
MAX_DEPTH = 2
NAV_TIMEOUT_MS = 15000
ACTION_TIMEOUT_MS = 8000


def _same_origin(base: str, candidate: str) -> bool:
    b, c = urlparse(base), urlparse(candidate)
    return b.scheme == c.scheme and b.netloc == c.netloc


def _clean_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


@dataclass
class PageRecord:
    url: str
    title: str = ""
    visible_text: str = ""
    links: list = field(default_factory=list)
    buttons: list = field(default_factory=list)
    forms: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    selects: list = field(default_factory=list)
    checkboxes: list = field(default_factory=list)
    radios: list = field(default_factory=list)
    console_errors: list = field(default_factory=list)
    failed_requests: list = field(default_factory=list)
    screenshot_path: str | None = None
    load_error: str | None = None

    def to_dict(self):
        d = dict(self.__dict__)
        return d


_ELEMENT_ATTRS = (
    "id", "name", "type", "placeholder", "value", "aria-label", "aria-labelledby",
    "aria-describedby", "href", "role", "required", "minlength", "maxlength", "pattern",
    "autocomplete", "disabled", "checked", "data-testid",
)

_BOOLEAN_ATTRS = ("required", "disabled", "checked")


def _find_label_text(handle, page) -> str | None:
    """Best-effort associated <label> text for a form control."""
    try:
        elem_id = handle.get_attribute("id")
        if elem_id:
            label = page.query_selector(f"label[for='{elem_id}']")
            if label:
                text = label.inner_text(timeout=500).strip()
                if text:
                    return text[:120]
    except Exception:
        pass
    try:
        # Fallback: an ancestor <label> wrapping the control directly.
        text = handle.evaluate(
            "el => { const l = el.closest('label'); return l ? l.innerText : null; }"
        )
        if text:
            return text.strip()[:120]
    except Exception:
        pass
    return None


def _extract_element_info(handle, page):
    """Best-effort structured info about a DOM element handle."""
    try:
        tag = handle.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        tag = None
    info = {"tag": tag}
    for attr in _ELEMENT_ATTRS:
        try:
            v = handle.get_attribute(attr)
        except Exception:
            v = None
        if v is None:
            continue
        if attr in _BOOLEAN_ATTRS:
            # Presence of the attribute (even as "") means true in HTML.
            info[attr] = True
        elif v:
            info[attr] = v

    try:
        text = handle.inner_text(timeout=1000).strip()
        if text:
            info["text"] = text[:120]
    except Exception:
        pass
    try:
        info["visible"] = handle.is_visible()
    except Exception:
        info["visible"] = None

    label_text = _find_label_text(handle, page)
    if label_text:
        info["label_text"] = label_text

    if tag == "select":
        try:
            options = handle.evaluate(
                "el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))"
            )
            if options:
                info["options"] = options[:20]
        except Exception:
            pass

    # Selector reliability policy (data-testid > id > name > aria-label >
    # role > unique CSS > text): build every candidate we can construct
    # from this element's own attributes, in priority order, pick the
    # best as the primary `selector`, and record the rest as
    # `selector_candidates` so a repair step has real alternatives to try
    # — never invented, only ones actually derived from this element.
    candidates = _build_selector_candidates(info, tag)
    info["selector_candidates"] = [sel for _kind, sel in candidates]
    if candidates:
        chosen_kind, chosen_selector = candidates[0]
        info["selector"] = chosen_selector
        info["selector_type"] = chosen_kind
        # Actually verify uniqueness against the live DOM — never just
        # claim a selector is reliable without checking it resolves to
        # exactly one element right now.
        try:
            match_count = page.locator(chosen_selector).count()
            info["selector_unique"] = (match_count == 1)
            info["selector_match_count"] = match_count
        except Exception:
            info["selector_unique"] = None
            info["selector_match_count"] = None
    else:
        info["selector"] = None
        info["selector_type"] = None
        info["selector_unique"] = None
        info["selector_match_count"] = None

    return info


def _build_selector_candidates(info: dict, tag: str | None) -> list[tuple[str, str]]:
    """
    Every selector we can honestly construct from THIS element's own
    discovered attributes, in preferred-reliability order:
      1. data-testid   2. unique id   3. unique name   4. aria-label
      5. role + accessible name   6. unique CSS   7. text (final fallback)
    Returns a list of (kind, selector) — never invents an attribute that
    wasn't actually observed.
    """
    candidates = []
    if info.get("data-testid"):
        candidates.append(("data-testid", f"[data-testid='{info['data-testid']}']"))
    if info.get("id"):
        candidates.append(("id", f"#{info['id']}"))
    if info.get("name") and tag:
        candidates.append(("name", f"{tag}[name='{info['name']}']"))
    if info.get("aria-label") and tag:
        candidates.append(("aria-label", f"{tag}[aria-label='{info['aria-label']}']"))
    if info.get("role") and tag:
        candidates.append(("role", f"{tag}[role='{info['role']}']"))
    if tag == "a" and info.get("href"):
        candidates.append(("css", f"a[href='{info['href']}']"))
    if info.get("text") and tag in ("button", "a"):
        safe_text = info["text"].replace('"', "'")
        candidates.append(("text", f"text={safe_text}"))
    return candidates


def _best_selector(info: dict, tag: str | None) -> str | None:
    """Kept for backward compatibility / direct unit testing — delegates
    to `_build_selector_candidates`, the single source of truth for
    selector priority, and returns just the top choice."""
    candidates = _build_selector_candidates(info, tag)
    return candidates[0][1] if candidates else None


def discover(start_url: str, headless: bool = True, max_pages: int = MAX_PAGES,
             max_depth: int = MAX_DEPTH) -> dict:
    """
    Crawl `start_url` (same-origin only, bounded) using Playwright and
    return a structured discovery report:
      {
        "start_url": ...,
        "pages": [PageRecord.to_dict(), ...],
        "errors": [...]
      }
    """
    visited = set()
    to_visit = [(start_url, 0)]
    pages_out = []
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(ACTION_TIMEOUT_MS)

        while to_visit and len(pages_out) < max_pages:
            url, depth = to_visit.pop(0)
            url = _clean_url(url)
            if url in visited:
                continue
            visited.add(url)

            page = context.new_page()
            console_errors = []
            failed_requests = []
            page.on("console", lambda msg: console_errors.append(
                {"type": msg.type, "text": msg.text}) if msg.type == "error" else None)
            page.on("requestfailed", lambda req: failed_requests.append(
                {"url": req.url, "method": req.method,
                 "failure": (req.failure or {}).get("errorText") if isinstance(req.failure, dict) else str(req.failure)}))
            page.on("response", lambda res: failed_requests.append(
                {"url": res.url, "status": res.status}) if res.status >= 400 else None)

            record = PageRecord(url=url)
            try:
                page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=4000)
                except PlaywrightTimeoutError:
                    pass

                record.title = page.title()
                final_url = _clean_url(page.url)
                record.url = final_url

                try:
                    record.visible_text = page.inner_text("body")[:2000]
                except Exception:
                    record.visible_text = ""

                # Links
                for a in page.query_selector_all("a[href]"):
                    info = _extract_element_info(a, page)
                    href = info.get("href")
                    if href:
                        absolute = urljoin(final_url, href)
                        info["absolute_href"] = absolute
                        record.links.append(info)
                        if (_same_origin(start_url, absolute) and depth < max_depth
                                and absolute not in visited
                                and not any(absolute == u for u, _ in to_visit)):
                            to_visit.append((absolute, depth + 1))

                # Buttons (real <button> + input[type=submit/button])
                for btn in page.query_selector_all("button, input[type='submit'], input[type='button']"):
                    record.buttons.append(_extract_element_info(btn, page))

                # Forms
                for form in page.query_selector_all("form"):
                    finfo = _extract_element_info(form, page)
                    finfo["action"] = form.get_attribute("action")
                    finfo["method"] = form.get_attribute("method") or "get"
                    record.forms.append(finfo)

                # Inputs
                for inp in page.query_selector_all("input:not([type='submit']):not([type='button'])"):
                    itype = (inp.get_attribute("type") or "text").lower()
                    info = _extract_element_info(inp, page)
                    if itype == "checkbox":
                        record.checkboxes.append(info)
                    elif itype == "radio":
                        record.radios.append(info)
                    else:
                        record.inputs.append(info)

                # Selects
                for sel in page.query_selector_all("select"):
                    record.selects.append(_extract_element_info(sel, page))

                # Screenshot
                shot_bytes = page.screenshot(full_page=True)
                record.screenshot_path = None  # filled by caller (needs evidence store)
                record._screenshot_bytes = shot_bytes  # stashed for evidence store

            except PlaywrightTimeoutError as e:
                record.load_error = f"Timeout loading page: {e}"
            except Exception as e:  # noqa: BLE001
                record.load_error = f"Error loading page: {e}"

            record.console_errors = console_errors
            record.failed_requests = failed_requests
            pages_out.append(record)
            page.close()

        context.close()
        browser.close()

    return {
        "start_url": start_url,
        "pages": pages_out,  # PageRecord objects (with _screenshot_bytes) — caller persists evidence
        "page_count": len(pages_out),
        "errors": errors,
    }
