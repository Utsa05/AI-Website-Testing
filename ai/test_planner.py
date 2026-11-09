"""
ai/test_planner.py
-------------------
Turns the structured discovery report into an AI-generated, structured
test plan (via OpenAI Structured Outputs), validates it against what was
actually discovered, and — if the AI call fails even after a retry and a
repair attempt — falls back to a small deterministic plan built only
from discovered elements (never invented ones).
"""

from __future__ import annotations

import json
import uuid

from .client import structured_completion, AIError
from .schemas import TEST_PLAN_SCHEMA
from browser.assertions import validate_assertion

VALID_OUTCOME_SOURCES = {
    "HTML_REQUIRED_ATTRIBUTE", "HTML_PATTERN", "HTML_MINLENGTH", "HTML_MAXLENGTH",
    "DISCOVERED_LINK_TARGET", "OBSERVED_APPLICATION_CONTRACT", "DOM_STATE", "AI_INFERENCE",
}

SYSTEM_PROMPT = """You are an expert QA engineer generating automated browser test cases \
for a web application, based only on discovery data captured by a real browser crawl.

Rules you MUST follow:
- Only reference selectors that appear in the provided discovery data's `selector` fields, \
UNLESS you have strong reason to believe a selector is reliable (e.g. an input with a `name` \
attribute you can see), in which case build it as an idiomatic CSS/Playwright selector. Prefer, \
in this order: data-testid, a unique id, a unique name attribute, aria-label, role + accessible \
name, a unique CSS selector, and only as a last resort a text selector. Discovery data marks \
each selector with `selector_type` and `unique` (whether it was actually confirmed to match \
exactly one element on the live page) — avoid a selector marked `unique=False` if a `unique=True` \
alternative exists for the same element.
- Every test MUST include at least one outcome assertion. A test with only actions and no \
assertions is invalid and must not be produced.
- Every test MUST include a non-empty `expected_outcome` field stating, in plain language, what \
should happen — written BEFORE you decide the assertions, and the assertions must actually test \
that stated outcome.
- Every test MUST include `expected_outcome_source`, honestly stating WHY the expected outcome is \
believed to be true. Use an independent source whenever the discovery data actually supports it: \
HTML_REQUIRED_ATTRIBUTE, HTML_PATTERN, HTML_MINLENGTH, HTML_MAXLENGTH (a discovered form \
constraint), DISCOVERED_LINK_TARGET (the outcome is simply where a discovered link's own href \
points), or OBSERVED_APPLICATION_CONTRACT / DOM_STATE (some other independently-observed fact). \
Only use AI_INFERENCE when the expectation is genuinely your own reasoning with no discovered \
constraint backing it — and know that a failed assertion on an AI_INFERENCE test can NEVER be \
reported as a BUG on its own later in the pipeline, only as NOT_A_FINDING, unless independent \
hard evidence (a real console error or network failure) also shows up. Do not mislabel an \
AI_INFERENCE expectation as one of the independent sources just to make it more likely to be \
reported — be honest about the source.
- ASSERT_URL assertions MUST include a non-empty `value` AND an explicit `operator` — one of \
EQUALS, CONTAINS, or STARTS_WITH. An ASSERT_URL with an empty value or missing operator is \
invalid and must never be produced; it can never be allowed to pass by construction. For every \
other assertion type, set `operator` to null.
- Prefer assertions grounded in what you can reasonably expect: visible text that would \
indicate success/error, a URL change after navigation (with an explicit operator and target), \
the presence/absence of an element, or — for a discovered `required`/`pattern`/`minlength`/ \
`maxlength` input — an `ASSERT_FIELD_VALIDITY` check (value "valid" or "invalid") against the \
browser's own native constraint-validation state. A `required` attribute proves the browser will \
report that field invalid when empty; it does NOT prove anything about whether the page's URL \
will change on submission (many apps validate server-side, via JS, or simply re-render the same \
URL with an error) — never assert "the URL should not change" as your evidence for a required \
field; assert the field's own validity state instead. Use discovered form-control attributes \
(required, minlength, maxlength, pattern, type) as evidence for validation-related expected \
outcomes — e.g. a `required` input with `minlength=3` supports an expected outcome of "the \
browser's constraint validation should mark this field invalid when empty" without you having to \
invent that requirement.
- A same-app redirect (e.g. "/" -> "/login" or "/" -> "/dashboard") is completely legitimate on \
its own and is never, by itself, evidence of a bug — do not write a test whose only expected \
outcome is "the URL should stay the same" as if that alone proved something is wrong when it \
changes.
- Generate a mix of test categories where the discovered app supports them: authentication \
flows (valid/invalid login), form validation (missing fields, invalid formats, mismatches), \
navigation (internal links resolve, no broken links), and general content/edge cases.
- Use safe synthetic test data only (e.g. test-user@example.com, ai_test_user_123, \
TestPassword123!). Never use real user data.
- Keep the plan focused: 4 to 8 tests is usually right for a small app. Do not overgenerate.
- Steps use relative URLs (e.g. "/login") for OPEN actions, resolved against the app's base URL.
- Do not fabricate application behavior — assertions should test genuinely plausible outcomes \
given the discovered forms/labels/buttons, not guesses about unrelated functionality. If you \
cannot construct a meaningful, evidence-backed expected outcome and assertion for something, \
do not generate a test for it at all rather than inventing one.
"""


def _summarize_discovery(discovery: dict) -> str:
    """Condense discovery data into a compact, token-efficient text block for the prompt."""
    lines = [f"Start URL: {discovery.get('start_url')}", f"Pages discovered: {discovery.get('page_count')}"]
    for pg in discovery.get("pages", []):
        lines.append(f"\n--- PAGE: {pg.get('url')} (title: {pg.get('title')}) ---")
        if pg.get("load_error"):
            lines.append(f"  LOAD ERROR: {pg['load_error']}")
            continue
        text_preview = (pg.get("visible_text") or "")[:300].replace("\n", " ")
        lines.append(f"  Visible text preview: {text_preview}")

        if pg.get("forms"):
            lines.append(f"  Forms: {len(pg['forms'])}")
            for f in pg["forms"]:
                lines.append(f"    - action={f.get('action')} method={f.get('method')} selector={f.get('selector')}")

        if pg.get("inputs"):
            lines.append("  Inputs:")
            for i in pg["inputs"][:15]:
                constraints = []
                if i.get("required"):
                    constraints.append("required")
                if i.get("minlength"):
                    constraints.append(f"minlength={i['minlength']}")
                if i.get("maxlength"):
                    constraints.append(f"maxlength={i['maxlength']}")
                if i.get("pattern"):
                    constraints.append(f"pattern={i['pattern']}")
                if i.get("disabled"):
                    constraints.append("disabled")
                if i.get("autocomplete"):
                    constraints.append(f"autocomplete={i['autocomplete']}")
                label = i.get("label_text") or i.get("aria-label")
                reliability = f"selector_type={i.get('selector_type')} unique={i.get('selector_unique')}"
                lines.append(f"    - selector={i.get('selector')} ({reliability}) type={i.get('type')} "
                              f"name={i.get('name')} id={i.get('id')} placeholder={i.get('placeholder')} "
                              f"label={label} constraints=[{', '.join(constraints)}]")

        if pg.get("buttons"):
            lines.append("  Buttons:")
            for b in pg["buttons"][:15]:
                reliability = f"selector_type={b.get('selector_type')} unique={b.get('selector_unique')}"
                lines.append(f"    - selector={b.get('selector')} ({reliability}) text='{b.get('text')}' "
                              f"aria-label={b.get('aria-label')}")

        if pg.get("selects"):
            lines.append("  Selects:")
            for s in pg["selects"][:10]:
                opts = s.get("options") or []
                opt_preview = [o.get("value") for o in opts][:6]
                lines.append(f"    - selector={s.get('selector')} name={s.get('name')} options={opt_preview}")

        if pg.get("checkboxes"):
            lines.append(f"  Checkboxes: {len(pg['checkboxes'])}")
        if pg.get("radios"):
            lines.append(f"  Radio buttons: {len(pg['radios'])}")

        if pg.get("links"):
            internal_links = [l.get("absolute_href") for l in pg["links"] if l.get("absolute_href")]
            lines.append(f"  Links ({len(pg['links'])}): {internal_links[:10]}")

        if pg.get("console_errors"):
            lines.append(f"  Console errors on load: {pg['console_errors'][:5]}")
        if pg.get("failed_requests"):
            lines.append(f"  Failed/error responses on load: {pg['failed_requests'][:5]}")

    return "\n".join(lines)


def _collect_known_selectors(discovery: dict) -> set:
    known = set()
    for pg in discovery.get("pages", []):
        for group in ("links", "buttons", "forms", "inputs", "selects", "checkboxes", "radios"):
            for item in pg.get(group, []) or []:
                sel = item.get("selector")
                if sel:
                    known.add(sel)
    return known


def validate_test_plan(plan: dict, discovery: dict) -> dict:
    """
    Cross-check the AI-generated plan against discovered selectors AND
    validate every assertion is well-formed and meaningful (Part 1/8 of
    the correction spec). This is the design-time gate that stops a test
    like `ASSERT_URL ""` from ever reaching the executor:

      - Zero assertions => invalid, dropped.
      - Missing `expected_outcome` => invalid, dropped.
      - Any assertion that fails `validate_assertion` (e.g. an ASSERT_URL
        with an empty value or missing operator) => the WHOLE test is
        rejected outright rather than silently executed with a
        rubber-stamp assertion. A repair pass may still recover it
        upstream; if not, it becomes a BLOCKED test, never a fake pass.

    Returns {"tests": [...], "blocked": [...]} — blocked tests carry a
    `reason` and are never executed, only reported as BLOCKED.
    """
    known_selectors = _collect_known_selectors(discovery)
    valid_tests = []
    blocked_tests = []

    for test in plan.get("tests", []):
        if not test.get("id"):
            test["id"] = f"T{uuid.uuid4().hex[:6]}"

        if not test.get("assertions"):
            blocked_tests.append({**test, "reason": "AI-generated test had no assertions."})
            continue

        if not (test.get("expected_outcome") or "").strip():
            blocked_tests.append({**test, "reason": "AI-generated test had no stated expected_outcome."})
            continue

        if test.get("expected_outcome_source") not in VALID_OUTCOME_SOURCES:
            blocked_tests.append({**test, "reason": "AI-generated test had a missing or invalid "
                                                       "expected_outcome_source."})
            continue

        assertion_problems = [validate_assertion(a) for a in test["assertions"]]
        assertion_problems = [p for p in assertion_problems if p]
        if assertion_problems:
            blocked_tests.append({
                **test,
                "reason": "One or more assertions were invalid/meaningless: " + "; ".join(assertion_problems),
            })
            continue

        warnings = []
        for step in test.get("steps", []):
            sel = step.get("selector")
            if sel and sel not in known_selectors:
                warnings.append(f"Selector '{sel}' in step '{step.get('action')}' was not "
                                 f"directly observed during discovery (AI-inferred).")
        test["validation_warnings"] = warnings
        valid_tests.append(test)

    return {"tests": valid_tests, "blocked": blocked_tests}


def build_fallback_plan(discovery: dict) -> dict:
    """
    Deterministic fallback test plan built ONLY from discovered elements.
    Used when AI generation fails even after retry/repair.

    Hard rule (Part 2 of the correction spec): a fallback test is only
    ever generated when the expected outcome is OBJECTIVELY OBSERVABLE
    from discovery evidence (e.g. a discovered link's own href, or a
    discovered element that must exist). If no such outcome can be
    established, we do NOT invent one and do NOT emit a test with an
    empty/meaningless assertion — the candidate becomes a BLOCKED entry
    with a clear reason instead. "The button was clicked" is never
    treated as evidence of anything.

    Returns {"tests": [...], "blocked": [...]}.
    """
    tests = []
    blocked = []
    pages = discovery.get("pages", [])
    if not pages:
        return {"tests": [], "blocked": []}

    home = pages[0]
    start_url = discovery.get("start_url")

    # 1. Home page loads at the start URL. IMPORTANT (Issue 1 of the third
    #    correction pass): a same-app redirect after loading the home page
    #    (e.g. "/" -> "/login" or "/" -> "/dashboard") is completely
    #    legitimate and must NEVER be treated as bug-eligible evidence on
    #    its own. This assertion is kept only as a coarse smoke check
    #    (did we even reach a page at all, as opposed to a load failure),
    #    so its expected_outcome_source is deliberately NOT one of the
    #    independent sources — a FAIL here can only ever become a finding
    #    if separately-cited, genuinely related hard evidence (a real
    #    console/network failure tied to the tested action) supports it.
    if start_url:
        tests.append({
            "id": "F1",
            "name": "Home page loads",
            "purpose": "Smoke check that the home page loads at all (diagnostic only).",
            "expected_outcome": f"Navigating to {start_url} should load some page without a hard "
                                 f"navigation/connection failure. Where the app ends up (including a "
                                 f"same-app redirect to e.g. /login or /dashboard) is NOT itself "
                                 f"evidence of a problem — a redirect alone is never a bug.",
            "expected_outcome_source": "WEAK_HEURISTIC",
            "category": "CONTENT",
            "steps": [{"action": "OPEN", "url": start_url, "selector": None, "value": None}],
            "assertions": [{"type": "ASSERT_URL", "operator": "STARTS_WITH", "selector": None, "value": start_url}],
            "validation_warnings": ["Deterministic fallback test — AI generation was unavailable.",
                                     "Diagnostic only: a FAIL here (e.g. a same-app redirect) must "
                                     "never be treated as a BUG without independent, genuinely "
                                     "related supporting evidence."],
            "fallback": True,
        })

    # 2. First discovered internal link resolves to ITS OWN discovered
    #    target — an objectively observable outcome taken directly from
    #    discovery data, not invented.
    first_link = None
    for l in home.get("links", []):
        href = l.get("absolute_href")
        if href and href != start_url:
            first_link = l
            break
    if first_link and first_link.get("selector"):
        target = first_link["absolute_href"]
        tests.append({
            "id": "F2",
            "name": "First discovered navigation link is reachable",
            "purpose": "Verify clicking the first discovered link navigates to its own discovered target.",
            "expected_outcome": f"Clicking the link should navigate to a URL starting with {target}, "
                                 f"exactly as discovered in the page's own href attribute.",
            "expected_outcome_source": "DISCOVERED_LINK_TARGET",
            "category": "NAVIGATION",
            "steps": [
                {"action": "OPEN", "url": start_url, "selector": None, "value": None},
                {"action": "CLICK", "url": None, "selector": first_link["selector"], "value": None},
            ],
            "assertions": [{"type": "ASSERT_URL", "operator": "STARTS_WITH", "selector": None, "value": target}],
            "validation_warnings": ["Deterministic fallback test — AI generation was unavailable."],
            "fallback": True,
        })
    elif home.get("links"):
        blocked.append({
            "id": "FB2", "name": "Navigation link check",
            "reason": "A discovered link had no reliable selector to click, so no observable "
                      "navigation outcome could be established.",
        })

    # 3. First discovered form with a `required` input: test the browser's
    #    OWN native constraint-validation state, not an assumption about
    #    what the application does with it (Issue 2 of the third
    #    correction pass). A `required` attribute genuinely, deterministically
    #    proves the browser will report validity.valueMissing=true for an
    #    empty value — it does NOT prove "the URL must not change on
    #    submit" (many apps validate server-side, via JS after submit, or
    #    simply re-render the same URL with an error banner). So the
    #    independent, HTML_REQUIRED_ATTRIBUTE-sourced assertion below
    #    checks ONLY the constraint-validation state. Whether the app also
    #    goes on to allow/block navigation is captured as a SEPARATE,
    #    explicitly diagnostic-only test (not independent evidence) purely
    #    for investigation — a URL change there is never, by itself, a bug.
    first_form_page = next((pg for pg in pages if pg.get("forms") and pg.get("inputs")), None)
    if first_form_page:
        submit_btn = next((b for b in first_form_page.get("buttons", []) if b.get("selector")), None)
        required_input = next((i for i in first_form_page.get("inputs", [])
                                if i.get("selector") and i.get("required")), None)
        if required_input:
            tests.append({
                "id": "F3",
                "name": "Required field reports invalid via browser constraint validation",
                "purpose": "Verify the browser's own native constraint validation actually reports "
                           "the empty, discovered `required` input as invalid — the deterministic "
                           "DOM-level fact a `required` attribute proves, independent of whatever "
                           "the application itself does with that fact.",
                "expected_outcome": f"With '{required_input['selector']}' left empty, the browser's "
                                     f"own constraint-validation state for that element (checkValidity() "
                                     f"/ validity.valueMissing) should report it as invalid — guaranteed "
                                     f"by the `required` attribute itself, not by any assumption about "
                                     f"navigation or URLs.",
                "expected_outcome_source": "HTML_REQUIRED_ATTRIBUTE",
                "category": "FORM_VALIDATION",
                "steps": [{"action": "OPEN", "url": first_form_page["url"], "selector": None, "value": None}],
                "assertions": [{"type": "ASSERT_FIELD_VALIDITY", "selector": required_input["selector"],
                                 "operator": None, "value": "invalid"}],
                "validation_warnings": ["Deterministic fallback test — AI generation was unavailable."],
                "fallback": True,
            })
            if submit_btn:
                tests.append({
                    "id": "F3b",
                    "name": "Diagnostic: submission behaviour with an invalid required field",
                    "purpose": "Observe (do not judge) whether the page navigates away when the form "
                               "is submitted with the required field still empty. This is diagnostic "
                               "only, for a human to investigate — NOT independent evidence of a bug.",
                    "expected_outcome": f"Diagnostic only: whether the URL changes after submitting "
                                         f"'{first_form_page['url']}' with the required field still "
                                         f"empty is recorded for investigation, not treated as a "
                                         f"pass/fail bug signal by itself — many valid reasons exist "
                                         f"for either outcome.",
                    "expected_outcome_source": "WEAK_HEURISTIC",
                    "category": "FORM_VALIDATION",
                    "steps": [
                        {"action": "OPEN", "url": first_form_page["url"], "selector": None, "value": None},
                        {"action": "CLICK", "url": None, "selector": submit_btn["selector"], "value": None},
                    ],
                    "assertions": [{"type": "ASSERT_URL", "operator": "STARTS_WITH", "selector": None,
                                     "value": first_form_page["url"]}],
                    "validation_warnings": ["Deterministic fallback test — AI generation was unavailable.",
                                             "Diagnostic only: a FAIL here must never be treated as a "
                                             "BUG without independent, genuinely related supporting "
                                             "evidence (see F3 for the actual independent evidence)."],
                    "fallback": True,
                })
        else:
            blocked.append({
                "id": "FB3", "name": "Form submission check",
                "reason": "The discovered form has no reliable observable expected outcome "
                          "(no `required` input was found), so no fallback assertion could be "
                          "established without inventing expected behaviour.",
            })

    return {"tests": tests, "blocked": blocked}


def generate_test_plan(api_key: str, model: str, discovery: dict) -> dict:
    """
    Main entry point. Tries the AI planner (with one retry + one repair
    attempt), and falls back to a deterministic plan if the AI path is
    unavailable. Returns:
      {"tests": [...], "blocked": [...], "source": "ai" | "ai_repaired" | "fallback",
       "ai_error": str | None}
    A single invalid AI-generated test never stops the whole run — it is
    moved to `blocked` and the remaining valid tests still execute.
    """
    summary = _summarize_discovery(discovery)
    user_prompt = (
        "Here is the structured discovery data captured from a real browser session "
        "against the target application. Generate a structured test plan as JSON.\n\n"
        f"{summary}"
    )

    last_error = None
    for attempt in range(2):  # initial attempt + 1 retry
        try:
            raw = structured_completion(
                api_key=api_key,
                model=model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_schema=TEST_PLAN_SCHEMA,
                schema_name="test_plan",
            )
            validated = validate_test_plan(raw, discovery)
            if validated["tests"]:
                validated["source"] = "ai" if attempt == 0 else "ai_repaired"
                validated["ai_error"] = None
                return validated
            last_error = ("AI returned a plan with no valid tests (all were dropped by validation: "
                          + "; ".join(b.get("reason", "") for b in validated.get("blocked", [])) + ")")
        except AIError as e:
            last_error = str(e)
            if e.kind == "auth":
                # No point retrying an auth failure.
                break
            # Otherwise: try a repair-style retry with a more directive prompt.
            user_prompt += ("\n\nIMPORTANT: Your previous response was invalid or empty. "
                             "Return STRICT JSON matching the schema, with every test "
                             "containing at least one assertion.")

    # AI path exhausted — deterministic fallback.
    fallback = build_fallback_plan(discovery)
    fallback["source"] = "fallback"
    fallback["ai_error"] = last_error
    return fallback


def repair_selectors(test: dict, known_selectors: set) -> dict:
    """
    Utility for opportunistic selector repair: swap a selector that wasn't
    observed during discovery for the most reliable selector that WAS
    observed. Never invents a selector — only substitutes among ones
    actually seen.

    Deliberately NOT auto-invoked as a blanket repair step in
    validate_test_plan(): picking "the most reliable known selector" with
    no notion of which element it should logically point at can silently
    swap in the WRONG element, which Part 7 explicitly calls worse than a
    blocked test. The actual safety net for selector reliability is, in
    order: (1) design-time validation here, (2) the AI-repair retry in
    generate_test_plan (re-prompting the whole plan), and (3) the
    executor's runtime existence/uniqueness check, which BLOCKS rather
    than guesses when a selector doesn't resolve cleanly. This function
    is kept available for a future, more context-aware repair step (e.g.
    matching by element role/kind rather than global "most reliable").
    """
    from browser.selectors import find_repair_candidate
    repaired = dict(test)
    replaced_any = False
    new_steps = []
    for step in test.get("steps", []):
        s = dict(step)
        if s.get("selector") and s["selector"] not in known_selectors:
            candidate = find_repair_candidate(known_selectors)
            if candidate:
                s["selector"] = candidate
                replaced_any = True
        new_steps.append(s)
    repaired["steps"] = new_steps
    repaired["repaired"] = replaced_any
    return repaired
