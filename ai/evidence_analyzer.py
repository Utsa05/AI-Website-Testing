"""
ai/evidence_analyzer.py
------------------------
Sends structured execution evidence (test definition, actions taken,
assertion results, console errors, network failures, final URL) to
OpenAI and asks it to classify outcomes as BUG, IMPROVEMENT, or
NOT_A_FINDING — never inventing evidence, always requiring a confidence
score, and explicitly allowed to say "not enough evidence".
"""

from __future__ import annotations

from .client import structured_completion, AIError, model_supports_vision
from .schemas import FINDINGS_SCHEMA
from .evidence_index import build_evidence_index, parse_reference, describe_evidence, is_evidence_related
from browser.evidence import load_screenshot_bytes

VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL", "NONE"}
VALID_TYPES = {"BUG", "IMPROVEMENT", "ASSERTION_ISSUE", "NOT_A_FINDING"}
# expected_outcome_source values that count as INDEPENDENT support for an
# expected outcome — i.e. not just "the AI guessed this should happen".
INDEPENDENT_OUTCOME_SOURCES = {
    "HTML_REQUIRED_ATTRIBUTE", "HTML_PATTERN", "HTML_MINLENGTH", "HTML_MAXLENGTH",
    "DISCOVERED_LINK_TARGET", "OBSERVED_APPLICATION_CONTRACT", "DOM_STATE",
}
# Assertion types that check exact rendered TEXT content. Unlike a
# discovered `required` attribute or a link's own href, the exact wording
# of a message the app renders (e.g. an error banner after a failed
# login) is never something discovery can independently confirm ahead of
# time — discovery only sees the page BEFORE the action that would
# produce that text. So a failed text-content assertion, however its
# expected_outcome_source got labeled, can never by itself satisfy the
# "independent" bar for a BUG; it still counts as ordinary negative
# evidence, but needs real corroboration (another failed assertion, or
# genuinely related console/network evidence).
TEXT_CONTENT_ASSERTION_TYPES = {"ASSERT_TEXT", "ASSERT_TEXT_VISIBLE"}
# Keywords identifying an authentication/login-flavoured test, used only
# to decide whether the "did the app actually let the user in?" signal
# below is meaningful for THIS test — never used to invent evidence.
_LOGIN_KEYWORDS = ("login", "log in", "sign in", "signin", "credential", "authenticat")
MAX_SCREENSHOTS_SENT = 4  # cost control — never send every screenshot for every step

SYSTEM_PROMPT = """You are a senior QA analyst reviewing REAL browser test execution evidence \
from an automated test run. You must classify each notable outcome as one of:

- BUG: something is demonstrably broken (server error, unexpected redirect, broken navigation, \
incorrect form behavior, missing validation that should exist, invalid input silently accepted, \
a JS runtime error that affects functionality, or a failed network request that demonstrably \
causes incorrect behavior).
- IMPROVEMENT: the feature works, but something about it is suboptimal (unclear validation \
message, poor feedback, confusing UX, or a concrete, evidence-based accessibility gap such as a \
button with no visible text, no aria-label, and no accessible name).
- ASSERTION_ISSUE: the underlying application behaviour looks CORRECT, but the generated test's \
assertion was too strict or too specific to reliably confirm it (most commonly: a text assertion \
checked for one exact phrase, e.g. "Invalid credentials", and a different-but-equivalent message \
was shown, or none was shown at all, while other evidence — the final URL, session/auth state — \
shows the app actually did the right thing, such as correctly rejecting bad login credentials). \
Use this INSTEAD of BUG when the failure looks like a test-wording problem rather than an \
application problem.
- NOT_A_FINDING: the evidence does not clearly support a bug, improvement, or assertion-issue claim.

STRICT RULES:
- Only use the evidence provided. Never invent selectors, error text, behavior, or screenshots \
that were not in the evidence given to you. Every `test_id` you use MUST be copied exactly from \
one of the TEST IDs listed in the evidence — never invent or guess a test_id.
- A test execution ERROR (e.g. a selector timeout) is NOT automatically a bug — only report a \
BUG if the evidence shows the APPLICATION behaved incorrectly, not that the test infrastructure \
had trouble interacting with it. If you cannot tell the difference from the evidence given, \
report NOT_A_FINDING.
- A FAILED TEXT-CONTENT ASSERTION (ASSERT_TEXT / ASSERT_TEXT_VISIBLE checking for exact wording) \
IS ESPECIALLY NOT AUTOMATICALLY A BUG. For a login/authentication test in particular: before \
concluding the app is broken because a specific error string (e.g. "Invalid credentials") wasn't \
found, actively check the OTHER evidence for that same test — the final URL (did the user stay on \
the login page, i.e. authentication was in fact rejected, or did they reach an authenticated \
area?), any other assertion results, console errors, and network responses (e.g. the login POST's \
status code). If the final URL and other evidence show the login was correctly rejected and the \
only problem is that the exact wording didn't match what was expected, classify this as \
ASSERTION_ISSUE, not BUG — the test's wording was too specific, not the application.
- A same-app redirect (e.g. "/" -> "/login" or "/" -> "/dashboard") is NEVER, by itself, evidence \
of a bug — many applications redirect legitimately. Do not classify a changed/different URL as a \
BUG unless the EXPECTED URL itself is independently supported (e.g. `expected_outcome_source` is \
DISCOVERED_LINK_TARGET, an explicit form action, or another deterministic navigation rule) — a \
generic "the URL should stay the same" heuristic is not sufficient grounds on its own.
- A console or network error may only strengthen or establish a BUG if it is DEMONSTRABLY RELATED \
to the specific behaviour being tested — not merely "something failed somewhere during this \
test". An unrelated resource failure (analytics.js, favicon.ico, a third-party tracker, a static \
asset) must NEVER be cited as evidence for an unrelated finding (e.g. a favicon 404 has nothing to \
do with whether a login redirect works). Only cite a console/network reference when its URL or \
text is genuinely about the same operation the test is checking (e.g. a POST to /api/login \
failing with 500 IS related to a login test). A failed assertion can also mean: the AI \
generated the wrong expected behaviour, the test was ambiguous, there was a test-environment or \
selector/action issue, an authentication/session problem, or the application's behaviour is \
simply different-but-valid. Each test lists an `expected_outcome_source` telling you WHY its \
expected outcome is believed to be true:
    HTML_REQUIRED_ATTRIBUTE / HTML_PATTERN / HTML_MINLENGTH / HTML_MAXLENGTH — a discovered form
        constraint independently demands this behaviour.
    DISCOVERED_LINK_TARGET — the outcome is simply where a discovered link's own href points.
    OBSERVED_APPLICATION_CONTRACT / DOM_STATE — some other independently-observed fact about the
        app (e.g. an element that was directly seen during discovery).
    AI_INFERENCE — the expected outcome is only the AI's own reasoning about what SHOULD happen;
        it is NOT independently confirmed by anything discovered in the app.
  A failed assertion on a test whose `expected_outcome_source` is AI_INFERENCE is, BY ITSELF, \
NEVER enough to report a BUG — classify it as NOT_A_FINDING instead. The one exception is when \
you also have independent HARD evidence of application misbehaviour regardless of what was \
expected — e.g. an actual console runtime error or an actual failed/5xx network request tied to \
the tested action — in which case you may report a BUG citing THAT evidence (a console: or \
network: reference), not the AI-inferred expectation.
- Every BUG or IMPROVEMENT must include a numeric confidence score between 0 and 1, and cite \
evidence in the `evidence` field using ONLY the exact structured reference strings given to you \
for that test — e.g. "assertion:T1:A1", "console:T1:C1", "network:T1:N1", "screenshot:T1:after". \
Do not write free-text descriptions as evidence, do not paraphrase a reference, and do not cite a \
reference for a different test_id or one that wasn't listed. Never cite a screenshot reference \
unless an image was actually attached to this message for that test.
- Do not report speculative issues. "Accessibility may be poor" is not acceptable — only report \
what concrete DOM/attribute evidence demonstrates.
- A PASSing test can still surface an IMPROVEMENT finding (e.g. the flow works, but validation \
messaging is weak) if there is concrete evidence for it.
- If a test result is BLOCKED or ERROR purely due to test infrastructure issues with no evidence \
of application misbehavior, classify it as NOT_A_FINDING.
- Do not treat generic warnings, unrelated network requests, favicon failures, analytics \
failures, or other third-party resource failures as application bugs unless the evidence \
demonstrably shows they affected the tested behaviour.
- Reproduction steps must correspond to the steps actually executed for that test, in the \
evidence given — do not invent steps that were not executed.
"""


def _summarize_execution(test_results: list, sent_screenshot_labels: set) -> str:
    lines = [f"TEST IDs in this evidence set (only use these as `test_id`): "
             f"{[r.get('id') for r in test_results]}"]
    for r in test_results:
        tid = r.get("id")
        lines.append(f"\n--- TEST {tid}: {r.get('name')} ---")
        lines.append(f"Purpose: {r.get('purpose')}")
        lines.append(f"Expected outcome (as stated when the test was planned): {r.get('expected_outcome', '')}")
        lines.append(f"Expected outcome source: {r.get('expected_outcome_source', 'AI_INFERENCE')} "
                     f"{'(NOT independently confirmed — see rules above)' if r.get('expected_outcome_source', 'AI_INFERENCE') == 'AI_INFERENCE' else '(independently supported)'}")
        lines.append(f"Result status: {r.get('status')}")
        if r.get("error"):
            lines.append(f"Execution error: {r['error']}")
        lines.append(f"Final URL: {r.get('final_url')}")
        lines.append(f"Duration: {r.get('duration_ms')}ms")

        steps = r.get("steps_executed", [])
        if steps:
            lines.append("Steps executed:")
            for s in steps:
                lines.append(f"  - {s.get('action')} selector={s.get('selector')} "
                              f"value={s.get('value')} status={s.get('status')} error={s.get('error')}")

        assertions = r.get("assertion_results", [])
        if assertions:
            lines.append("Assertion results (cite as assertion:%s:<ref>):" % tid)
            for a in assertions:
                lines.append(f"  - [ref={a.get('ref')}] {a.get('type')} operator={a.get('operator')} "
                              f"value={a.get('value')} passed={a.get('passed')} detail={a.get('detail')}")

        if r.get("console_errors"):
            lines.append(f"Console errors (cite as console:{tid}:<ref>):")
            for c in r["console_errors"]:
                lines.append(f"  - [ref={c.get('ref')}] {c.get('type')}: {c.get('text')}")
        if r.get("failed_requests"):
            lines.append(f"Failed/error network requests (cite as network:{tid}:<ref>):")
            for n in r["failed_requests"]:
                lines.append(f"  - [ref={n.get('ref')}] {n.get('url')} status={n.get('status', n.get('failure'))}")

        if r.get("screenshot_before"):
            tag = f"an actual IMAGE is attached below, cite as screenshot:{tid}:before" \
                if f"{tid}_before" in sent_screenshot_labels else "not sent to you as an image — do not cite it"
            lines.append(f"A 'before' screenshot was captured ({tag}).")
        if r.get("screenshot_after"):
            tag = f"an actual IMAGE is attached below, cite as screenshot:{tid}:after" \
                if f"{tid}_after" in sent_screenshot_labels else "not sent to you as an image — do not cite it"
            lines.append(f"An 'after' screenshot was captured ({tag}).")

    return "\n".join(lines)


def _select_screenshots_to_send(test_results: list) -> list[tuple[str, bytes]]:
    """
    Pick a small, cost-bounded set of screenshots to actually send as
    images: prioritise failure screenshots, then the final screenshot of
    each remaining test, up to MAX_SCREENSHOTS_SENT. Never sends every
    screenshot for every step.
    """
    candidates = []
    for r in test_results:
        if r.get("status") in ("FAIL", "ERROR") and r.get("screenshot_after"):
            candidates.append((f"{r.get('id')}_after", r["screenshot_after"], r.get("id"), r.get("name")))
    for r in test_results:
        if r.get("status") not in ("FAIL", "ERROR") and r.get("screenshot_after"):
            candidates.append((f"{r.get('id')}_after", r["screenshot_after"], r.get("id"), r.get("name")))

    selected = []
    for label, artifact_id, test_id, name in candidates[:MAX_SCREENSHOTS_SENT]:
        png_bytes = load_screenshot_bytes(artifact_id)
        if png_bytes:
            selected.append((f"{label} — final screenshot for test {test_id} ({name})", png_bytes))
    return selected


def _is_login_flavoured_test(test: dict) -> bool:
    context = ((test.get("name") or "") + " " + (test.get("purpose") or "") + " "
               + (test.get("expected_outcome") or "")).lower()
    return any(k in context for k in _LOGIN_KEYWORDS)


def _looks_like_login_was_rejected(test: dict) -> bool:
    """
    Deterministic, app-agnostic signal that an authentication attempt did
    NOT succeed: the page the test ended up on still looks like a login
    page (its own URL contains "login"/"signin"), rather than having
    navigated away to some other (presumably authenticated) area. This
    never invents knowledge of the target app's routes — it only reads
    the test's own recorded final_url — and is only consulted for tests
    that are themselves about login/authentication (see
    `_is_login_flavoured_test`), never applied generically.
    """
    final_url = (test.get("final_url") or "").lower()
    return any(k in final_url for k in ("login", "signin", "sign-in", "sign_in"))


def validate_findings(findings: list, test_results: list) -> list:
    """
    Deterministic finding validator. The AI is never trusted blindly:
    every finding is cross-checked against the actual execution evidence
    before it reaches the report. A finding that fails any check is
    dropped entirely rather than surfaced as an unsupported bug — the
    system prefers NOT_A_FINDING (i.e. nothing) over an unsupported claim.

    Evidence checking is fully structured (no fuzzy/token-overlap
    matching): every string in `evidence` must parse as an
    `assertion:<test_id>:<ref>` / `console:<test_id>:<ref>` /
    `network:<test_id>:<ref>` / `screenshot:<test_id>:before|after`
    reference whose test_id matches the finding's own test_id AND whose
    ref actually exists in that test's evidence index. If ANY cited
    reference fails to resolve, the WHOLE finding is rejected — no
    partial credit for "some other evidence string happened to match".

    A BUG additionally requires INDEPENDENT support: either the test's
    own `expected_outcome_source` is not AI_INFERENCE, or at least one
    cited reference is hard evidence (a console error or network
    failure) rather than just the (possibly AI-guessed) assertion. A
    failed assertion on an AI_INFERENCE expectation, with no other hard
    evidence, is never allowed through as a BUG.
    """
    by_id = {r.get("id"): r for r in test_results}
    valid = []

    for f in findings:
        # test_id must reference a test that actually exists and executed.
        test_id = f.get("test_id")
        test = by_id.get(test_id)
        if test is None:
            continue  # e.g. AI said T99 but only T1-T6 exist — reject
        if not test.get("status"):
            continue  # test never actually executed

        # confidence must be a real number in [0, 1].
        conf = f.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= conf <= 1.0):
            continue

        # severity and type must be from the known enums.
        if f.get("severity") not in VALID_SEVERITIES:
            continue
        if f.get("type") not in VALID_TYPES:
            continue

        # route/URL must be supported by execution (the test's own final
        # URL, or empty if the finding didn't claim a specific route).
        route = (f.get("route") or "").strip()
        final_url = (test.get("final_url") or "")
        if route and final_url and route not in final_url and final_url not in route:
            continue

        # reproduction steps should correspond to steps actually executed.
        repro = f.get("reproduction_steps") or []
        executed_actions = {(s.get("action") or "").upper() for s in test.get("steps_executed", [])}
        executed_actions.add("OPEN")  # every test implicitly opens the base URL first
        if repro:
            matched = any(
                any(action in (step or "").upper() for action in executed_actions)
                for step in repro
            )
            if not matched:
                continue

        # Structured evidence resolution — replaces fuzzy word-overlap
        # matching entirely. Every cited reference must parse, must belong
        # to THIS test_id, and must resolve to something actually collected.
        cited = f.get("evidence") or []
        if not cited:
            continue  # BUG/IMPROVEMENT with zero cited evidence is unsupported

        evidence_index = build_evidence_index(test)
        resolved = []
        all_resolved = True
        for ref in cited:
            parsed = parse_reference(ref)
            if not parsed:
                all_resolved = False
                break
            kind, ref_test_id, _item_id = parsed
            if ref_test_id != test_id:
                all_resolved = False  # citing evidence from a different test — reject
                break
            data = evidence_index.get(ref)
            if data is None:
                all_resolved = False  # reference doesn't exist — REJECT FINDING
                break
            if not is_evidence_related(ref, data, test):
                # Issue 3 (third correction pass): a console/network event
                # that isn't demonstrably related to what THIS test is
                # checking (e.g. an unrelated analytics/favicon 404) can't
                # be used to support this finding at all — reject the whole
                # finding rather than silently dropping just this citation,
                # since a finding built partly on an irrelevant citation is
                # not trustworthy as stated.
                all_resolved = False
                break
            resolved.append((ref, kind, data))
        if not all_resolved:
            continue

        # BUG-specific gating: a failed assertion on an AI-inferred
        # expectation is not enough by itself. Either the expected outcome
        # was independently supported, or at least one cited reference is
        # hard evidence (console/network) rather than just the assertion.
        if f.get("type") == "BUG":
            outcome_independent = test.get("expected_outcome_source") in INDEPENDENT_OUTCOME_SOURCES
            has_hard_evidence = any(kind in ("console", "network") for _ref, kind, _data in resolved)

            # Text-content assertions (exact wording of a rendered message)
            # can never themselves satisfy "independent" — see
            # TEXT_CONTENT_ASSERTION_TYPES above. This closes the gap where
            # an AI-generated invalid-login test asserts a specific error
            # string, mislabels its own expected_outcome_source as
            # something that sounds independent, and would otherwise sail
            # through this gate on wording alone.
            failed_assertions = [data for _ref, kind, data in resolved
                                  if kind == "assertion" and data.get("passed") is False]
            non_text_failed_assertions = [a for a in failed_assertions
                                           if a.get("type") not in TEXT_CONTENT_ASSERTION_TYPES]
            only_text_assertion_evidence = (
                failed_assertions and not non_text_failed_assertions and not has_hard_evidence
            )
            if only_text_assertion_evidence:
                outcome_independent = False

            if not outcome_independent and not has_hard_evidence:
                # One more chance before dropping entirely: if this is a
                # login/auth-flavoured test whose ONLY negative evidence is
                # a failed text-content assertion, and the test's own
                # final_url shows the login attempt was in fact rejected
                # (still on the login page, never reached an authenticated
                # area), the underlying security behaviour is correct —
                # the generated assertion's exact wording just didn't
                # match. That's a test/assertion problem, not an
                # application bug, so surface it transparently as
                # ASSERTION_ISSUE instead of either a false BUG or silence.
                if (only_text_assertion_evidence and _is_login_flavoured_test(test)
                        and _looks_like_login_was_rejected(test)):
                    f = dict(f)
                    f["type"] = "ASSERTION_ISSUE"
                    note = (" [Auto-reclassified from BUG: the assertion checked for specific wording "
                            "that was not found, but the final URL indicates the login attempt was "
                            "correctly rejected (the user never reached an authenticated page) — this "
                            "looks like the generated assertion being too strict about exact phrasing, "
                            "not a broken application.]")
                    f["recommendation"] = (f.get("recommendation") or "") + note
                    valid.append(f)
                continue

            # A BUG must be backed by at least one genuinely NEGATIVE
            # signal — a failed assertion, or a console/network
            # error/failure event. A passing assertion or a screenshot
            # alone proves nothing is wrong (Item 5 of the presentation
            # readiness pass: "Get Started -> Registration" is correct
            # behaviour, so nothing about it — including a screenshot of
            # it happening successfully, or its own passing assertion —
            # can be cited as evidence that something is broken).
            has_negative_evidence = any(
                (kind == "assertion" and data.get("passed") is False) or kind in ("console", "network")
                for _ref, kind, data in resolved
            )
            if not has_negative_evidence:
                continue

        valid.append(f)

    return valid


def analyze_evidence(api_key: str, model: str, test_results: list) -> dict:
    """
    Returns {"findings": [...], "source": "ai"|"fallback", "ai_error": str|None,
              "vision_used": bool}
    On AI failure, falls back to a conservative, purely rule-based pass
    that only reports findings directly supported by FAILed assertions AND
    a reliable, independently-supported expected outcome (a failed
    assertion alone is never automatically a bug — see SYSTEM_PROMPT).
    Every finding — from either path — is re-checked by `validate_findings`
    before being returned; the AI is never trusted blindly.
    """
    if not test_results:
        return {"findings": [], "source": "fallback", "ai_error": None, "vision_used": False}

    vision_capable = model_supports_vision(model)
    images = _select_screenshots_to_send(test_results) if vision_capable else []
    sent_labels = {label.split(" — ")[0] for label, _ in images}

    summary = _summarize_execution(test_results, sent_labels)
    user_prompt = (
        "Here is the structured execution evidence from a real Playwright test run. "
        "Classify findings as JSON per the schema.\n\n" + summary
    )
    if images:
        user_prompt += ("\n\nA small set of actual screenshots are attached as images below, each "
                         "labeled with the test it belongs to. Use them for visual/DOM-adjacent "
                         "evidence where relevant.")
    elif not vision_capable:
        user_prompt += (f"\n\nNote: model '{model}' is not used for visual image analysis in this "
                         f"run, so screenshots are DOM/evidence-only (not actually viewed) — do not "
                         f"claim you inspected a screenshot's visual content.")

    last_error = None
    for attempt in range(2):
        try:
            raw = structured_completion(
                api_key=api_key,
                model=model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_schema=FINDINGS_SCHEMA,
                schema_name="findings",
                images=images if attempt == 0 else None,  # keep the repair retry text-only/simple
            )
            findings = [f for f in raw.get("findings", [])
                        if f.get("type") in ("BUG", "IMPROVEMENT", "ASSERTION_ISSUE")]
            findings = validate_findings(findings, test_results)
            raw["findings"] = findings
            raw["source"] = "ai" if attempt == 0 else "ai_repaired"
            raw["ai_error"] = None
            raw["vision_used"] = bool(images) and attempt == 0
            return raw
        except AIError as e:
            last_error = str(e)
            if e.kind == "auth":
                break
            user_prompt += ("\n\nIMPORTANT: Your previous response was invalid. Return STRICT "
                             "JSON matching the schema. Only include BUG or IMPROVEMENT findings "
                             "that are directly supported by the evidence given, with a valid "
                             "test_id copied exactly from the TEST IDs listed above.")

    # Fallback: conservative, rule-based. A FAILED ASSERTION ALONE IS NEVER
    # ENOUGH TO CREATE A BUG — this is the exact contradiction the second
    # correction pass called out. We only create a BUG when the test's
    # `expected_outcome_source` is one of the INDEPENDENT sources (a real
    # HTML constraint, a discovered link target, an observed application
    # contract/DOM state) — never when it's AI_INFERENCE, no matter how
    # confidently the assertion failed. If no independent source is
    # recorded, this loop produces NO FINDING for that test at all.
    findings = []
    for r in test_results:
        tid = r.get("id")

        # Deterministic hard-evidence path, independent of PASS/FAIL: the
        # test's OWN destination page returned an HTTP error status (e.g.
        # a discovered nav link leading to a 404). This is reliable even
        # when the test's own assertion only checked "did we navigate to
        # X" and technically PASSED — reaching a page is not the same as
        # that page responding successfully. A same-origin page returning
        # 4xx/5xx is an objective, deterministic fact, not a guess.
        own_page_failure = next(
            (n for n in r.get("failed_requests", [])
             if n.get("ref") and n.get("url") and r.get("final_url")
             and n["url"].rstrip("/") == r["final_url"].rstrip("/") and n.get("status")),
            None
        )
        if own_page_failure:
            findings.append({
                "type": "BUG",
                "title": f"{r.get('final_url')} returned HTTP {own_page_failure.get('status')}",
                "severity": "HIGH" if (own_page_failure.get("status") or 0) >= 500 else "MEDIUM",
                "confidence": 0.85,
                "route": r.get("final_url") or "",
                "test_id": tid,
                "expected": f"Navigating to {r.get('final_url')} should return a successful response.",
                "expected_outcome_source": "OBSERVED_APPLICATION_CONTRACT",
                "actual": f"Server responded with HTTP {own_page_failure.get('status')}.",
                "reproduction_steps": [f"{s.get('action')} {s.get('selector') or s.get('url') or ''}"
                                        for s in r.get("steps_executed", [])],
                "evidence": [f"network:{tid}:{own_page_failure['ref']}"],
                "recommendation": f"Fix or remove the link/route at {r.get('final_url')} — it currently "
                                   f"responds with HTTP {own_page_failure.get('status')}.",
                "source": "fallback_rule_based",
            })
            continue  # already have a solid, hard-evidence finding for this test

        if r.get("status") != "FAIL":
            continue
        outcome_source = r.get("expected_outcome_source")
        if outcome_source not in INDEPENDENT_OUTCOME_SOURCES:
            continue  # AI_INFERENCE (or unset) alone => NOT_A_FINDING, never BUG
        if not (r.get("expected_outcome") or "").strip():
            continue
        failed_assertions = [a for a in r.get("assertion_results", []) if not a.get("passed")]
        for a in failed_assertions:
            ref = a.get("ref")
            if not ref:
                continue
            findings.append({
                "type": "BUG",
                "title": f"Assertion failed: {r.get('name')}",
                "severity": "MEDIUM",
                "confidence": 0.5,
                "route": r.get("final_url") or "",
                "test_id": tid,
                "expected": r.get("expected_outcome") or f"{a.get('type')} {a.get('operator') or ''} {a.get('value')}",
                "expected_outcome_source": outcome_source,
                "actual": a.get("detail", ""),
                "reproduction_steps": [f"{s.get('action')} {s.get('selector') or s.get('url') or ''}"
                                        for s in r.get("steps_executed", [])],
                "evidence": [f"assertion:{tid}:{ref}"],
                "recommendation": "Investigate this flow manually; AI analysis was unavailable "
                                   "so this is a conservative, rule-based finding based only on "
                                   "a failed assertion whose expected outcome is independently "
                                   f"supported by discovered evidence ({outcome_source}).",
                "source": "fallback_rule_based",
            })
    findings = validate_findings(findings, test_results)
    return {"findings": findings, "source": "fallback", "ai_error": last_error, "vision_used": False}
