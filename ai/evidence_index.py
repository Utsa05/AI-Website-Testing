"""
ai/evidence_index.py
---------------------
Structured evidence references, replacing fuzzy/token-overlap evidence
matching (Part 3 of the second correction pass).

Every piece of evidence collected during test execution — each assertion
result, each console error, each failed network request, each screenshot
— gets a stable reference string of the form:

    assertion:<test_id>:<ref>      e.g. assertion:T1:A1
    console:<test_id>:<ref>        e.g. console:T1:C1
    network:<test_id>:<ref>        e.g. network:T1:N1
    screenshot:<test_id>:before|after

The AI (and the rule-based fallback) must cite ONLY these exact strings
as `evidence` for a finding. A finding whose evidence does not resolve
to something that was actually collected is rejected outright — there is
no partial credit for "a similar word appeared somewhere".
"""

from __future__ import annotations

import re

VALID_KINDS = ("assertion", "console", "network", "screenshot")

# URL/text patterns that are essentially never relevant to whatever
# behaviour a test is actually checking — static assets, analytics,
# trackers, monitoring beacons. A hit on the underlying page failing to
# load its favicon or analytics script is NOT evidence that (say) a login
# redirect is broken, no matter how confidently an LLM might want to cite
# it (Issue 3 of the third correction pass).
_IRRELEVANT_URL_PATTERNS = (
    "favicon.ico", "favicon.png", "analytics", "gtag", "google-analytics",
    "googletagmanager", "doubleclick", "segment.io", "segment.com", "sentry.io",
    "hotjar", "mixpanel", "facebook.net", "fbevents", "newrelic", ".woff", ".woff2",
    ".ttf", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".map",
    ".js.map", ".css.map", "apple-touch-icon", "manifest.json", "robots.txt",
)

_STOPWORDS = {"http", "https", "www", "com", "org", "net", "html", "true", "false"}


def _keywords(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


def is_evidence_related(ref: str, data: dict, test: dict) -> bool:
    """
    Correlate a piece of evidence with the SPECIFIC behaviour a test is
    checking, rather than accepting "something failed somewhere during
    this test" as sufficient (the exact false-positive pattern Issue 3
    calls out: an unrelated analytics.js/favicon 404 must never be able
    to upgrade a failed login assertion into a "login is broken" bug).

    - assertion / screenshot evidence is always considered related — it
      IS the test's own outcome/state, not a side effect.
    - console / network evidence must pass two checks:
        1. It must not match a known-irrelevant static/third-party
           resource pattern (favicon, analytics, trackers, static assets),
           regardless of timing — these are never evidence of anything
           the test is checking.
        2. It must share at least one meaningful keyword with the test's
           own context (its name, purpose, expected outcome, or the route
           it ended up on) — e.g. a request to "/api/login" overlaps with
           a test named "Invalid login shows no error".
      An event that occurred during a *different* step of the SAME test
      (tracked via `step_index`/`route` on the event, captured by the
      executor) is still eligible — this function checks topical
      relatedness, not exact step alignment, since a login failure's
      console error may fire slightly before/after the assertion runs.
    """
    kind = data.get("kind")
    if kind in ("assertion", "screenshot"):
        return True

    haystack = (data.get("url") or "") if kind == "network" else (data.get("text") or "")
    haystack_lower = haystack.lower()
    if kind == "network" and any(p in haystack_lower for p in _IRRELEVANT_URL_PATTERNS):
        return False

    context = " ".join([
        test.get("name") or "", test.get("purpose") or "",
        test.get("expected_outcome") or "", test.get("final_url") or "",
    ])
    event_keywords = _keywords(haystack)
    context_keywords = _keywords(context)
    return bool(event_keywords & context_keywords)


def build_evidence_index(test_result: dict) -> dict:
    """
    Build {reference_string: raw_evidence_dict} for everything actually
    collected for this one test. `ref` fields (A1/C1/N1/...) are assigned
    by the executor at collection time (see browser/executor.py).
    """
    tid = test_result.get("id")
    index = {}

    for a in test_result.get("assertion_results", []) or []:
        ref = a.get("ref")
        if ref:
            index[f"assertion:{tid}:{ref}"] = {"kind": "assertion", **a}

    for c in test_result.get("console_errors", []) or []:
        ref = c.get("ref")
        if ref:
            index[f"console:{tid}:{ref}"] = {"kind": "console", **c}

    for n in test_result.get("failed_requests", []) or []:
        ref = n.get("ref")
        if ref:
            index[f"network:{tid}:{ref}"] = {"kind": "network", **n}

    if test_result.get("screenshot_before"):
        index[f"screenshot:{tid}:before"] = {"kind": "screenshot", "artifact": test_result["screenshot_before"]}
    if test_result.get("screenshot_after"):
        index[f"screenshot:{tid}:after"] = {"kind": "screenshot", "artifact": test_result["screenshot_after"]}

    return index


def parse_reference(ref: str):
    """Returns (kind, test_id, item_id) or None if `ref` isn't well-formed."""
    if not isinstance(ref, str):
        return None
    parts = ref.split(":", 2)
    if len(parts) != 3:
        return None
    kind, test_id, item_id = parts
    if kind not in VALID_KINDS or not test_id or not item_id:
        return None
    return kind, test_id, item_id


def describe_evidence(ref: str, data: dict) -> str:
    """Human-readable rendering of one resolved evidence item, for the report."""
    kind = data.get("kind")
    if kind == "assertion":
        return (f"[{ref}] {data.get('type')} {data.get('operator') or ''} "
                f"{data.get('value') or data.get('selector') or ''} -> "
                f"passed={data.get('passed')}: {data.get('detail')}").strip()
    if kind == "console":
        return f"[{ref}] console.{data.get('type')}: {data.get('text')}"
    if kind == "network":
        status = data.get("status", data.get("failure"))
        return f"[{ref}] {data.get('url')} -> {status}"
    if kind == "screenshot":
        return f"[{ref}] screenshot artifact {data.get('artifact')}"
    return f"[{ref}] {data}"
