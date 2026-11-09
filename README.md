# AI App Tester

*Prototype built for the Decoded Digital Forward Deployed Engineer Internship assessment. Not
production software — see Known limitations below.*

## The problem

Small teams shipping fast rarely have dedicated QA. Manual click-through testing is slow, easy
to skip under deadline pressure, and only catches what a human happens to think to try.
Writing and maintaining a proper automated test suite (Selenium/Playwright scripts) is its own
project — which is exactly why most small teams don't have one, and why "improvements" (as
opposed to outright breakage) almost never get systematically surfaced.

## The approach

This project connects to a **live URL** and drives it with a **real Playwright browser** —
chosen over a repo/source upload because it tests the app the way a user actually experiences
it (rendered DOM, real navigation, real network behaviour) without needing access to a
codebase, and it's realistic to build well in a two-week window. AI does the two things a
human QA engineer would otherwise have to do by hand: decide *what* to test from what it
observes in the app (rather than a fixed, hand-written test list), and decide whether a
failure is a genuine, evidence-backed bug rather than a false alarm. Everything in between
(execution, assertion evaluation, evidence collection, finding validation) is deterministic
code, not the AI's word taken on faith — see "Validate findings" below for why that split
matters.

A lightweight, AI-powered application tester for small teams without dedicated QA. Point it
at a running web app, and it uses a real Playwright browser session plus the OpenAI API to
discover the app, plan and generate test cases, execute them, and produce an evidence-backed
developer report — bugs and improvements, separated, each with confidence and reproduction
steps.

## What it does

1. **Connect** — you give it a live URL (e.g. `http://127.0.0.1:5050`) and an OpenAI API key,
   for the current run only.
2. **Discover** — a real Chromium session (Playwright) opens the app, walks a bounded set of
   same-origin pages, and records links, forms, inputs, buttons, selects, checkboxes, radios,
   visible text, console errors, and failed network requests, plus a screenshot of each page.
3. **Plan** — the discovery data is sent to OpenAI, which reasons about the app and returns a
   **structured** test plan (JSON schema, via OpenAI Structured Outputs) — not free-form text.
   Every test must include a non-empty `expected_outcome` (stated before the assertions), an
   `expected_outcome_source` honestly declaring WHY that outcome is believed true (a real HTML
   constraint like `required`/`pattern`/`minlength`/`maxlength`, a discovered link's own target,
   an observed application contract/DOM state, or — only when genuinely nothing else backs it —
   `AI_INFERENCE`), and at least one well-formed outcome assertion, or it's rejected. `ASSERT_URL`
   assertions must carry an explicit `operator` (`EQUALS` / `CONTAINS` / `STARTS_WITH`) and a
   non-empty `value` — an empty-value URL assertion is rejected outright, both at plan-validation
   time and again at execution time, so it can never silently evaluate to "true no matter what".
4. **Validate** — generated tests are cross-checked against what was actually discovered and
   against a strict assertion schema. A test with no assertions, no stated expected outcome, or
   a malformed assertion is never executed — it's moved to a `BLOCKED` list with a clear reason
   instead of being run with a rubber-stamp check. One invalid AI-generated test never stops the
   run; the rest still execute. Selectors the AI inferred (rather than directly observed) are
   flagged in the report as warnings, not silently trusted.
5. **Execute** — a fresh Playwright session runs each test's steps (OPEN/CLICK/FILL/SELECT/
   PRESS/CHECK/UNCHECK/WAIT/SCREENSHOT) against the real app. Before interacting with an
   element, the executor checks the selector resolves to exactly one element; if it's missing or
   ambiguous, the test is `BLOCKED` rather than silently resolved with `.first` (a test hitting
   the wrong element is worse than a blocked one). This applies to `PRESS` too — a PRESS action
   must target a specific, verified element rather than falling back to a global
   `page.keyboard.press()` whose target we can't confirm. Assertions
   (ASSERT_TEXT[_VISIBLE], ASSERT_VISIBLE/NOT_VISIBLE, ASSERT_URL, ASSERT_COUNT,
   ASSERT_ELEMENT) are re-validated at execution time as a runtime backstop, and the same
   zero/one/many gating applies to element-identity assertions: 0 matches fails (except
   ASSERT_NOT_VISIBLE, which trivially passes), exactly 1 match is evaluated, and >1 matches
   BLOCKS the assertion rather than guessing which element was meant. Every piece of evidence
   collected (each assertion result, console error, network failure, screenshot) gets a stable
   structured reference (`assertion:T1:A1`, `console:T1:C1`, `network:T1:N1`,
   `screenshot:T1:after`) for the analysis step below to cite. Results are
   PASS / FAIL / BLOCKED / ERROR — never "PASS" just because actions ran without throwing.
6. **Analyse** — the full execution evidence (steps, assertions, console errors, network
   failures, and a small, cost-bounded set of actual screenshot images) is sent to OpenAI. When
   the selected model is on the vision-capable allowlist (e.g. `gpt-4o*`, `gpt-4.1*`), failure
   and final screenshots are attached as real image content — not just described — so visual
   inspection is genuine when claimed; on a non-vision model, analysis is DOM/evidence-only and
   the report says so explicitly, rather than claiming visual analysis that didn't happen.
   OpenAI classifies notable outcomes as BUG, IMPROVEMENT, or NOT_A_FINDING, each with a
   confidence score, severity, route, reproduction steps, structured evidence references, and a
   recommendation. A failed assertion is *never* automatically treated as a bug: a BUG requires
   either that the test's own `expected_outcome_source` is independent (not `AI_INFERENCE`), or
   that at least one cited reference is hard evidence (a real console error or network failure)
   independent of what was merely expected. `AI_INFERENCE` alone, with no other hard evidence, can
   only ever become `NOT_A_FINDING`. Execution errors (e.g. a selector timeout) are similarly
   never auto-reported as bugs.
7. **Validate findings** — every finding returned by the AI (or the rule-based fallback) is
   re-checked deterministically against the actual execution evidence before it reaches the
   report, using ONLY structured evidence references — no fuzzy/token-overlap text matching.
   The referenced test ID must exist and have executed, confidence must be a real number in
   [0, 1], severity/type must be from the known enum, the claimed route must be consistent with
   where the test actually ended up, reproduction steps must correspond to steps that were
   actually executed, and every cited evidence reference must parse, belong to the same test_id,
   and resolve to something that was genuinely collected — a reference that doesn't exist, or
   that points at a different test, rejects the *entire* finding, with no partial credit for
   "a similar-sounding word appeared somewhere else." A finding that fails any check is dropped —
   the system prefers no finding over an unsupported one.
8. **Report** — a readable, tabbed developer report (Overview / Tests / Bugs / Improvements /
   Evidence / Report). Every BUG/IMPROVEMENT shows its full evidence chain: Test ID → Expected
   outcome → Expected outcome source → Executed actions → Assertion(s) → Actual result →
   Evidence → Screenshot → Conclusion — all pre-resolved from the structured references, not
   just a flat evidence array. Screenshots render inline via a safe `/artifact/<id>` route —
   never a raw filesystem path. The overall run is labeled SUCCESS / PARTIAL / BLOCKED / FAILED
   based on what actually happened — a run with some blocked or errored tests is never reported
   as a flat 100% success.

If OpenAI is unavailable (bad key, rate limit, invalid model, malformed/truncated response), the
app retries once, attempts a repair pass, and — only if that also fails — falls back to a small,
deterministic test plan built **only** from discovered elements whose expected outcome is
objectively observable (e.g. a discovered link's own href, or a `required` input's own
constraint). If no such outcome can be established for a discovered form, it becomes a `BLOCKED`
entry with a clear reason rather than an invented, always-passing assertion. Fallback tests are
clearly labeled as such in the report, and the pipeline never crashes or silently pretends the
AI worked.

## Project structure

```
ai_tester/
    app.py                     Flask app: routes, pipeline orchestration, run registry
    requirements.txt
    README.md

    ai/
        client.py               OpenAI API wrapper (structured outputs, error classification)
        schemas.py               JSON schemas for the test plan and findings
        test_planner.py          Discovery -> AI test plan (+ validation + deterministic fallback)
        evidence_analyzer.py     Execution evidence -> AI findings (+ rule-based fallback)

    browser/
        discovery.py             Playwright-based app discovery (bounded crawl)
        executor.py               Executes test steps against a live browser session
        assertions.py             Assertion evaluation + design-time validation (EQUALS/CONTAINS/
                                   STARTS_WITH ASSERT_URL semantics; zero/one/many element-identity
                                   gating for ASSERT_VISIBLE/NOT_VISIBLE/ELEMENT — no `.first`)
        selectors.py              Selector reliability scoring/classification + live existence/
                                   uniqueness checks
        evidence.py               Screenshot storage, safe artifact ID <-> path mapping
      ai/
        evidence_index.py         Structured evidence references (assertion:T1:A1, console:T1:C1,
                                   network:T1:N1, screenshot:T1:after) — no fuzzy text matching

    reporting/
        report.py                 Shapes run data into the final report structure

    demo_app/                   A small, deliberately imperfect demo app ("TaskFlow")
        app.py
        templates/
        static/

    static/                     Main app's CSS/JS
    templates/                  Main app's HTML (index, report, report_pending)
    reports/artifacts/          Screenshot artifacts, served only via /artifact/<id>
```

## Running it

```bash
pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

Then open **http://127.0.0.1:5000**.

### Try it against the included demo app

The demo app ("TaskFlow") is a separate, tiny Flask app with several **intentional, realistic
issues** for the tester to find:

```bash
cd demo_app
python app.py
```

This starts TaskFlow on **http://127.0.0.1:5050**. In another terminal, start the main tester
(`python app.py` from the project root, port 5000), open http://127.0.0.1:5000, paste in an
OpenAI API key, set the URL to `http://127.0.0.1:5050`, and click **RUN AI TEST**.

TaskFlow is a small, mostly-functional app: registration (username/email/password validation,
duplicate-email rejection, preserved form values, a success message, redirect to login),
login (validation, session handling, redirect to dashboard), and logout all work correctly by
design — so a correct tester run should report those flows as **PASS**, not invent bugs where
none exist. A small number of **intentional, realistic issues** are deliberately kept so the
tester has genuine things to find:
- The "About" nav link points to a route that doesn't exist (`/about` returns 404) — a genuine
  **BUG** (broken navigation), discoverable purely through browser execution and evidence.
- The footer "Subscribe" button is an icon with no visible text, `aria-label`, or accessible
  name — an evidence-based **IMPROVEMENT** candidate (accessibility gap), not a hard bug.

### Demo script (for a live walkthrough)

1. Start TaskFlow (`python demo_app/app.py`, port 5050) and the AI App Tester (`python app.py`,
   port 5000) in two terminals.
2. Open the tester's UI, paste an OpenAI API key, and enter `http://127.0.0.1:5050`.
3. Click **RUN AI TEST** and narrate each pipeline stage as it completes: discovery, the
   AI-generated test plan, live Playwright execution, evidence collection, AI analysis.
4. In the final report, point out: `Home → Get Started → Registration` and the
   registration/login/dashboard flow reporting **PASS** (correct behaviour is not misreported
   as a bug); `About → 404` reporting as a **BUG** with its evidence chain; and the footer
   accessibility gap reporting as an **IMPROVEMENT**. These classifications should emerge
   naturally from the run, not be hand-picked — if the AI plan happens not to cover one of
   these flows on a given run, that's an accurate reflection of what the AI chose to test, not
   a scripted result.

### Trying it against your own app

Any URL Playwright can reach from the machine running this tool will work — a local dev
server, a staging URL, or a public site. Discovery is deliberately bounded (same-origin only,
shallow depth, a handful of pages, sensible timeouts) so it stays a fast, predictable
prototype rather than an open-ended crawler.

## Running the unit tests

```bash
cd ai_tester
python -m unittest discover -s tests
```

These are stdlib `unittest` (no `pytest` dependency) and run without a real browser or a real
OpenAI key — Playwright and the OpenAI client are exercised against small fake objects
(`tests/fakes.py`) so the deterministic logic (assertion semantics, selector reliability,
finding validation, PASS/FAIL/BLOCKED classification, artifact-path safety, AI error handling)
is verified directly. Coverage includes, per test file:

- `test_assertions.py` — ASSERT_URL (valid CONTAINS/EQUALS/STARTS_WITH, empty value, missing
  operator, correct/incorrect URL), plus ASSERT_TEXT/VISIBLE/COUNT.
- `test_selectors.py` — selector existence, ambiguity rejection, reliability ranking.
- `test_executor.py` — PASS/FAIL/BLOCKED/ERROR semantics end-to-end through `execute_test`,
  including "a browser action succeeding is not automatically PASS" and "ambiguous selector
  blocks rather than using `.first`".
- `test_finding_validation.py` — the deterministic finding validator (bad test_id, out-of-range
  confidence, invalid enums, unsupported route, invented evidence, mismatched reproduction
  steps).
- `test_discovery_extraction.py` — the expanded element/form attribute extraction (required,
  minlength/maxlength/pattern, aria-*, associated label text, select options, selector
  priority).
- `test_evidence_store.py` — artifact save/load round-trip and path-traversal rejection.
- `test_ai_client.py` — API key never persisted/written to disk, malformed/empty/truncated
  response handling, and auth/rate-limit/timeout error classification.

## Security notes

- The OpenAI API key is submitted from the browser to the local Flask backend for the current
  run only. It is **never** written to disk, `.env` files, source code, logs, or browser
  storage (no localStorage/cookies). It lives only in memory for the duration of the
  background pipeline thread.
- Screenshots are served through `/artifact/<opaque-id>`, which resolves IDs against a fixed
  directory and rejects anything that isn't a known file in it (defense against path
  traversal). No filesystem path is ever exposed to the browser.

## Visual analysis honesty

Whether a given run actually included visual (image) analysis is recorded and shown in the
report (`vision_used`, and an "Analysis source" / "Visual (screenshot) analysis" line). This
tool never claims visual analysis happened unless a screenshot was actually attached as an
image to the OpenAI request for that run — on a non-vision-capable model, or when the AI path
falls back to the rule-based analyzer, the report says the analysis was DOM/evidence-only.

## Known limitations (by design, for a 2-week prototype)

- In-memory run registry — restarting the Flask process clears run history. Fine for a local,
  single-user prototype; not intended for multi-user/production use.
- Discovery crawl is intentionally shallow/bounded (same-origin, `max_depth=2`, `max_pages=6`
  by default) — this is a scoping decision, not an oversight; see `browser/discovery.py`.
- Out of scope per the brief: CI/CD integration, full repository/source-code analysis, MFA
  flows, load/performance testing, and distributed browser infrastructure.
- This is a prototype: the in-memory run store, lack of auth on the local Flask app, and use
  of Flask's dev server are all appropriate for a local demo, not a deployed service.
- Selector repair is deliberately conservative: the executor BLOCKs an ambiguous/missing
  selector rather than guessing (`browser/selectors.py`'s `check_selector_on_page`). A more
  context-aware automatic repair utility exists (`ai/test_planner.py`'s `repair_selectors`) but
  is intentionally not auto-invoked, since blindly substituting "the most reliable known
  selector" with no notion of which element it should point at can silently interact with the
  wrong element — worse than a blocked test. The safety net today is design-time validation +
  one AI-repair retry of the whole plan + the executor's runtime BLOCK.
- This sandbox/dev environment does not always have network access to install the `openai`
  package or download a Playwright browser binary; `ai/client.py` imports `openai` lazily and
  degrades to a clear `AIError` if it's missing, and the unit test suite uses fake Playwright
  objects (`tests/fakes.py`) specifically so the deterministic logic can be verified without
  either dependency being present. A full live run still requires both installed normally.

## Future improvements

Roughly in priority order, if this moved beyond a two-week prototype:
- Persist run history (SQLite or similar) instead of an in-memory registry, so runs survive a
  restart and can be compared over time.
- Context-aware selector repair (matching by element role/kind, not just "most reliable known
  selector") so more ambiguous-selector cases can be auto-resolved instead of BLOCKED.
- Finer-grained evidence correlation using the `step_index`/`order`/`timestamp` already
  captured on console/network events, to tie a finding to the *specific* action that triggered
  it rather than the whole test.
- Multi-page/deeper discovery with configurable crawl budgets, and authenticated-flow support
  (testing behind a login) beyond what the current bounded, same-origin crawl covers.
- A proper design system pass on the report/UI beyond the current functional styling.
