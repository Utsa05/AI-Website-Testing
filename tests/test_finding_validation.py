import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.evidence_analyzer import validate_findings


def make_test_result(**overrides):
    base = {
        "id": "T1",
        "name": "Invalid login shows no error",
        "status": "FAIL",
        "final_url": "https://example.com/login",
        "expected_outcome": "An error message should be shown for invalid credentials.",
        "expected_outcome_source": "AI_INFERENCE",
        "assertion_results": [
            {"type": "ASSERT_TEXT_VISIBLE", "value": "invalid", "passed": False,
             "detail": "Text 'invalid' NOT found on page.", "ref": "A1"}
        ],
        "console_errors": [],
        "failed_requests": [],
        "steps_executed": [
            {"action": "OPEN", "selector": None, "value": None, "status": "success", "error": None},
            {"action": "CLICK", "selector": "#submit", "value": None, "status": "success", "error": None},
        ],
        "screenshot_before": "shot1.png",
        "screenshot_after": "shot2.png",
        "error": None,
    }
    base.update(overrides)
    return base


def make_finding(**overrides):
    base = {
        "type": "BUG",
        "title": "Invalid login gives no feedback",
        "severity": "MEDIUM",
        "confidence": 0.8,
        "route": "https://example.com/login",
        "test_id": "T1",
        "expected": "An error message should be shown",
        "actual": "No error message shown",
        "reproduction_steps": ["OPEN /login", "CLICK #submit"],
        "evidence": ["assertion:T1:A1"],
        "recommendation": "Show a visible error on invalid credentials.",
    }
    base.update(overrides)
    return base


class TestStructuralValidation(unittest.TestCase):
    def test_nonexistent_test_id_rejected(self):
        # "If the AI returns test_id = T99 but only T1-T6 exist: REJECT."
        results = [make_test_result(id="T1", expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(test_id="T99")]
        self.assertEqual(validate_findings(findings, results), [])

    def test_confidence_out_of_range_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(confidence=1.5)]
        self.assertEqual(validate_findings(findings, results), [])

    def test_confidence_non_numeric_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(confidence="high")]
        self.assertEqual(validate_findings(findings, results), [])

    def test_invalid_severity_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(severity="CATASTROPHIC")]
        self.assertEqual(validate_findings(findings, results), [])

    def test_invalid_type_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(type="MAYBE_A_BUG")]
        self.assertEqual(validate_findings(findings, results), [])

    def test_unsupported_route_rejected(self):
        results = [make_test_result(final_url="https://example.com/login",
                                     expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(route="https://example.com/completely/different/page")]
        self.assertEqual(validate_findings(findings, results), [])

    def test_reproduction_steps_not_matching_executed_steps_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(reproduction_steps=["DRAG the slider", "DROP it at 50%"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_test_never_executed_rejected(self):
        results = [make_test_result(status=None, expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding()]
        self.assertEqual(validate_findings(findings, results), [])


class TestStructuredEvidenceReferences(unittest.TestCase):
    """Part 3 of the second correction pass: no fuzzy/token-overlap matching."""

    def test_valid_reference_resolves(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(evidence=["assertion:T1:A1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_no_evidence_cited_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(evidence=[])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_malformed_reference_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(evidence=["the assertion failed"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_reference_to_nonexistent_item_rejected(self):
        # assertion:T1:A99 -- A99 was never collected.
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(evidence=["assertion:T1:A99"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_reference_to_different_test_id_rejected(self):
        results = [
            make_test_result(id="T1", expected_outcome_source="HTML_REQUIRED_ATTRIBUTE"),
            make_test_result(id="T2", expected_outcome_source="HTML_REQUIRED_ATTRIBUTE",
                              assertion_results=[{"type": "ASSERT_URL", "operator": "CONTAINS",
                                                   "value": "/x", "passed": False, "detail": "x", "ref": "A1"}]),
        ]
        # Finding is about T1 but cites T2's evidence — must be rejected
        # even though "assertion:T2:A1" is a perfectly valid reference for
        # test T2. Citing evidence across tests is not allowed.
        findings = [make_finding(test_id="T1", evidence=["assertion:T2:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_unrelated_evidence_with_shared_generic_words_rejected(self):
        # This is the exact case fuzzy token-overlap matching used to let
        # through: two evidence strings share common words ("page", "not",
        # "found") but describe completely unrelated things. Structured
        # references make this impossible because the cited ref simply
        # doesn't exist in the index.
        results = [make_test_result(
            expected_outcome_source="HTML_REQUIRED_ATTRIBUTE",
            assertion_results=[{"type": "ASSERT_TEXT_VISIBLE", "value": "invalid", "passed": False,
                                 "detail": "Text 'invalid' NOT found on page.", "ref": "A1"}],
        )]
        # The AI invents a plausible-sounding but nonexistent evidence item
        # instead of citing the real "assertion:T1:A1".
        findings = [make_finding(evidence=["assertion:T1:A2"])]  # A2 doesn't exist
        self.assertEqual(validate_findings(findings, results), [])

    def test_console_reference_resolves(self):
        results = [make_test_result(
            expected_outcome_source="AI_INFERENCE",
            console_errors=[{"type": "error", "text": "TypeError: cannot read property of undefined "
                                                        "while handling login form submission", "ref": "C1"}],
        )]
        findings = [make_finding(evidence=["console:T1:C1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_network_reference_resolves(self):
        results = [make_test_result(
            expected_outcome_source="AI_INFERENCE",
            failed_requests=[{"url": "https://example.com/api/login", "status": 500, "ref": "N1"}],
        )]
        findings = [make_finding(evidence=["network:T1:N1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_screenshot_reference_resolves(self):
        # Screenshot evidence resolves structurally, but per the new
        # negative-evidence requirement a BUG also needs a failing
        # assertion or console/network signal alongside it — see
        # TestDemoScenarioNavigationIsNotABug for the screenshot-only
        # rejection case. Use IMPROVEMENT here, which isn't gated that way.
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(type="IMPROVEMENT", evidence=["screenshot:T1:after"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_screenshot_reference_when_no_screenshot_taken_rejected(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE",
                                     screenshot_before=None, screenshot_after=None)]
        findings = [make_finding(evidence=["screenshot:T1:after"])]
        self.assertEqual(validate_findings(findings, results), [])


class TestFailedAssertionAloneIsNotABug(unittest.TestCase):
    """Part 1/4 of the second correction pass."""

    def test_failed_assertion_with_ai_inference_and_no_hard_evidence_is_not_a_bug(self):
        results = [make_test_result(expected_outcome_source="AI_INFERENCE")]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1"])]
        result = validate_findings(findings, results)
        # The shared fixture models an invalid-login/text-assertion scenario
        # with the login correctly rejected (final_url still /login) — the
        # invalid-login fix (see TestInvalidLoginTextAssertionFix) now
        # surfaces this as ASSERTION_ISSUE rather than dropping it
        # entirely, which is strictly more informative. Either way, the
        # one thing that must never happen is BUG.
        self.assertFalse(any(r["type"] == "BUG" for r in result))

    def test_failed_text_assertion_with_ai_inference_outside_login_context_is_dropped(self):
        # Same rule, but for a non-login test: no login-rejection signal
        # applies, so a text-only, AI_INFERENCE-sourced failure is simply
        # dropped rather than reclassified.
        results = [make_test_result(
            name="Homepage tagline check", expected_outcome="The tagline should say 'Welcome'.",
            expected_outcome_source="AI_INFERENCE", final_url="https://example.com/",
        )]
        findings = [make_finding(type="BUG", test_id="T1", route="https://example.com/",
                                  evidence=["assertion:T1:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_failed_assertion_with_independent_source_is_eligible_for_bug(self):
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_ai_inference_bug_allowed_with_additional_console_evidence(self):
        # AI_INFERENCE alone isn't enough, but real hard evidence (a
        # console error) tied to the tested action is an independent
        # signal of application misbehaviour regardless of what was
        # merely expected. The console text must be topically related to
        # the login test's own context (Issue 3, third correction pass) —
        # a generic, unrelated JS error would not count (see the
        # TestEvidenceCorrelation cases below).
        results = [make_test_result(
            expected_outcome_source="AI_INFERENCE",
            console_errors=[{"type": "error",
                              "text": "Uncaught ReferenceError during login redirect handling",
                              "ref": "C1"}],
        )]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1", "console:T1:C1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_ai_inference_improvement_is_not_gated_the_same_way(self):
        # IMPROVEMENT findings are more observational and are not required
        # to have independent expected-outcome support the way BUG is.
        results = [make_test_result(expected_outcome_source="AI_INFERENCE")]
        findings = [make_finding(type="IMPROVEMENT", evidence=["assertion:T1:A1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_missing_expected_outcome_source_treated_as_not_independent(self):
        results = [make_test_result(expected_outcome_source=None)]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1"])]
        result = validate_findings(findings, results)
        self.assertFalse(any(r["type"] == "BUG" for r in result))


class TestEvidenceCorrelation(unittest.TestCase):
    """Issue 3 of the third correction pass: console/network evidence must
    be demonstrably related to the tested behaviour, not just 'something
    failed somewhere during this test'."""

    def test_login_api_500_during_login_is_related(self):
        results = [make_test_result(
            name="Invalid login shows no error", expected_outcome_source="AI_INFERENCE",
            failed_requests=[{"url": "https://example.com/api/login", "status": 500, "ref": "N1"}],
        )]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1", "network:T1:N1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)

    def test_analytics_404_during_login_is_unrelated(self):
        results = [make_test_result(
            name="Invalid login shows no error", expected_outcome_source="AI_INFERENCE",
            failed_requests=[{"url": "https://example.com/analytics.js", "status": 404, "ref": "N1"}],
        )]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1", "network:T1:N1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_favicon_404_is_unrelated(self):
        results = [make_test_result(
            name="Invalid login shows no error", expected_outcome_source="AI_INFERENCE",
            failed_requests=[{"url": "https://example.com/favicon.ico", "status": 404, "ref": "N1"}],
        )]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1", "network:T1:N1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_unrelated_console_error_cannot_create_bug(self):
        results = [make_test_result(
            name="Registration form validation", expected_outcome_source="AI_INFERENCE",
            console_errors=[{"type": "error", "text": "Failed to load Google Analytics", "ref": "C1"}],
        )]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1", "console:T1:C1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_unrelated_network_error_cannot_create_bug(self):
        results = [make_test_result(
            name="Home page content check", expected_outcome_source="AI_INFERENCE",
            failed_requests=[{"url": "https://example.com/static/logo.png", "status": 404, "ref": "N1"}],
        )]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1", "network:T1:N1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_home_page_favicon_cannot_support_unrelated_login_finding(self):
        # The exact BAD example from the correction notes: a favicon 404 on
        # the home page must never be usable to back a claim about login.
        results = [make_test_result(
            id="T1", name="Home page loads", expected_outcome_source="WEAK_HEURISTIC",
            failed_requests=[{"url": "https://example.com/favicon.ico", "status": 404, "ref": "N1"}],
        )]
        findings = [make_finding(type="BUG", title="Login is broken", test_id="T1",
                                  evidence=["network:T1:N1"])]
        self.assertEqual(validate_findings(findings, results), [])


class TestRedirectSafety(unittest.TestCase):
    """Issue 1 of the third correction pass: a redirect alone is never a BUG."""

    def test_legitimate_redirect_to_login_is_not_a_bug(self):
        # F1-style test: home page redirected to /login. WEAK_HEURISTIC
        # source + no independent/hard evidence => no bug. Uses a
        # URL-typed assertion (not the shared fixture's default text
        # assertion) since this test is specifically about redirect/URL
        # safety, decoupled from the login-text-assertion fix below.
        results = [make_test_result(
            id="T1", name="Home page loads", status="FAIL", purpose="Smoke check.",
            expected_outcome="The home page should stay on the same URL.",
            expected_outcome_source="WEAK_HEURISTIC", final_url="https://example.com/login",
            assertion_results=[{"type": "ASSERT_URL", "operator": "STARTS_WITH",
                                 "value": "https://example.com/", "passed": False,
                                 "detail": "Redirected to /login instead.", "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T1", route="https://example.com/login",
                                  evidence=["assertion:T1:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_legitimate_redirect_to_dashboard_is_not_a_bug(self):
        results = [make_test_result(
            id="T1", name="Home page loads", status="FAIL", purpose="Smoke check.",
            expected_outcome="The home page should stay on the same URL.",
            expected_outcome_source="WEAK_HEURISTIC", final_url="https://example.com/dashboard",
            assertion_results=[{"type": "ASSERT_URL", "operator": "STARTS_WITH",
                                 "value": "https://example.com/", "passed": False,
                                 "detail": "Redirected to /dashboard instead.", "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T1", route="https://example.com/dashboard",
                                  evidence=["assertion:T1:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_ai_only_url_expectation_failing_is_not_a_bug(self):
        results = [make_test_result(
            id="T1", name="Login redirects to dashboard", status="FAIL",
            purpose="Check post-login redirect.",
            expected_outcome="After login the user should land on the dashboard.",
            expected_outcome_source="AI_INFERENCE", final_url="https://example.com/login",
            assertion_results=[{"type": "ASSERT_URL", "operator": "CONTAINS",
                                 "value": "/dashboard", "passed": False,
                                 "detail": "Still on /login.", "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T1", route="https://example.com/login",
                                  evidence=["assertion:T1:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_explicit_discovered_link_target_violation_is_bug_eligible(self):
        # F2-style test: discovered link explicitly points at /dashboard,
        # but navigation actually landed on /wrong-page — this genuinely IS
        # independent evidence, unlike the generic home-page URL heuristic.
        results = [make_test_result(
            id="T2", name="Dashboard link is reachable", status="FAIL",
            expected_outcome_source="DISCOVERED_LINK_TARGET", final_url="https://example.com/wrong-page",
            assertion_results=[{"type": "ASSERT_URL", "operator": "STARTS_WITH",
                                 "value": "https://example.com/dashboard", "passed": False,
                                 "detail": "Landed on /wrong-page instead of discovered target /dashboard.",
                                 "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T2", route="https://example.com/wrong-page",
                                  evidence=["assertion:T2:A1"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)


class TestAiInferenceDecisionModel(unittest.TestCase):
    def test_ai_inference_failed_assertion_no_independent_evidence_is_no_finding(self):
        results = [make_test_result(expected_outcome_source="AI_INFERENCE")]
        findings = [make_finding(type="BUG")]
        # Shared fixture models a correctly-rejected invalid-login scenario,
        # so this now surfaces as ASSERTION_ISSUE (see
        # TestInvalidLoginTextAssertionFix) rather than vanishing — the
        # invariant this test actually cares about is "never BUG".
        result = validate_findings(findings, results)
        self.assertFalse(any(r["type"] == "BUG" for r in result))

    def test_ai_inference_failed_assertion_with_independent_evidence_is_bug_eligible(self):
        # AI expected a dashboard redirect; independent evidence (a
        # discovered link target) supports the SAME conclusion.
        results = [make_test_result(
            expected_outcome_source="DISCOVERED_LINK_TARGET",
        )]
        findings = [make_finding(type="BUG")]
        self.assertEqual(len(validate_findings(findings, results)), 1)


class TestDemoScenarioNavigationIsNotABug(unittest.TestCase):
    """Item 5/16 of the presentation-readiness pass: the exact TaskFlow
    demo flows (Home -> Get Started -> Registration, and
    Registration -> Login -> Dashboard) must never be misreported as bugs
    just because a navigation/URL change occurred."""

    def test_get_started_redirect_cannot_be_reported_as_bug_via_its_own_passing_assertion(self):
        # Even if a finding tries to cite the flow's own (passing)
        # assertion as "evidence" that something is wrong, a passing
        # assertion can never support a BUG — there is nothing negative
        # about it to point to.
        results = [make_test_result(
            id="T1", name="Get Started navigates to registration", status="PASS",
            expected_outcome_source="DISCOVERED_LINK_TARGET",
            final_url="http://127.0.0.1:5050/register",
            assertion_results=[{"type": "ASSERT_URL", "operator": "CONTAINS", "value": "/register",
                                 "passed": True, "detail": "Landed on /register as discovered.", "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T1", route="http://127.0.0.1:5050/register",
                                  evidence=["assertion:T1:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_registration_login_dashboard_correct_flow_cannot_be_reported_as_bug(self):
        results = [make_test_result(
            id="T2", name="Full registration to dashboard flow", status="PASS",
            expected_outcome_source="OBSERVED_APPLICATION_CONTRACT",
            final_url="http://127.0.0.1:5050/dashboard",
            assertion_results=[{"type": "ASSERT_URL", "operator": "CONTAINS", "value": "/dashboard",
                                 "passed": True, "detail": "Reached dashboard after valid login.", "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T2", evidence=["assertion:T2:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_screenshot_alone_cannot_support_a_bug(self):
        # A screenshot illustrates a finding; it is never, by itself,
        # evidence that something is broken.
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(type="BUG", evidence=["screenshot:T1:after"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_failed_assertion_plus_screenshot_still_valid_bug(self):
        # A real failing assertion, illustrated with a screenshot, remains
        # a perfectly valid BUG — only screenshot-ONLY evidence is rejected.
        results = [make_test_result(expected_outcome_source="HTML_REQUIRED_ATTRIBUTE")]
        findings = [make_finding(type="BUG", evidence=["assertion:T1:A1", "screenshot:T1:after"])]
        self.assertEqual(len(validate_findings(findings, results)), 1)


class TestInvalidLoginTextAssertionFix(unittest.TestCase):
    """Regression tests for the invalid-login over-aggressive BUG bug: a
    failed ASSERT_TEXT checking for one exact phrase ("Invalid
    credentials") must never alone justify BUG, and when the login was
    actually correctly rejected (per the final URL), it should surface as
    ASSERTION_ISSUE instead of silently vanishing or wrongly becoming a
    BUG."""

    def test_exact_wording_mismatch_on_correctly_rejected_login_becomes_assertion_issue(self):
        results = [make_test_result(
            id="T1", name="Invalid login shows an error", status="FAIL",
            purpose="Verify invalid credentials are rejected with an error message.",
            expected_outcome="An 'Invalid credentials' message should be shown.",
            # The AI mislabeled this as an independent source even though
            # exact response wording can never be discovery-confirmed —
            # the fix must not simply trust this label for text assertions.
            expected_outcome_source="OBSERVED_APPLICATION_CONTRACT",
            final_url="https://example.com/login",  # still on the login page: rejected correctly
            assertion_results=[{"type": "ASSERT_TEXT_VISIBLE", "value": "Invalid credentials",
                                 "passed": False, "detail": "Text 'Invalid credentials' NOT found on page.",
                                 "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T1", title="Invalid login shows no error",
                                  evidence=["assertion:T1:A1"])]
        result = validate_findings(findings, results)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "ASSERTION_ISSUE")

    def test_exact_wording_mismatch_with_ai_inference_and_no_url_evidence_is_dropped(self):
        # Not login-flavoured, so the reclassification heuristic doesn't
        # apply, and honestly-labeled AI_INFERENCE text evidence alone
        # still can't support anything — dropped entirely (no finding).
        results = [make_test_result(
            id="T1", name="Homepage tagline check", status="FAIL",
            expected_outcome="The tagline text should say 'Welcome'.",
            expected_outcome_source="AI_INFERENCE",
            final_url="https://example.com/",
            assertion_results=[{"type": "ASSERT_TEXT_VISIBLE", "value": "Welcome",
                                 "passed": False, "detail": "Text 'Welcome' NOT found on page.", "ref": "A1"}],
        )]
        findings = [make_finding(type="BUG", test_id="T1", evidence=["assertion:T1:A1"])]
        self.assertEqual(validate_findings(findings, results), [])

    def test_login_test_with_corroborating_url_assertion_can_still_be_a_real_bug(self):
        # If the test ALSO has a non-text assertion showing the user
        # actually reached an authenticated area with bad credentials,
        # that's genuine corroborating evidence of a real security bug —
        # the text-only tightening must not suppress THIS case.
        results = [make_test_result(
            id="T1", name="Invalid login should not authenticate", status="FAIL",
            expected_outcome="Invalid credentials should not log the user in.",
            expected_outcome_source="AI_INFERENCE",
            final_url="https://example.com/dashboard",  # uh oh — actually got in!
            assertion_results=[
                {"type": "ASSERT_TEXT_VISIBLE", "value": "Invalid credentials", "passed": False,
                 "detail": "Text not found.", "ref": "A1"},
                {"type": "ASSERT_URL", "operator": "CONTAINS", "value": "/login", "passed": False,
                 "detail": "Expected to stay on /login but ended up on /dashboard.", "ref": "A2"},
            ],
        )]
        findings = [make_finding(type="BUG", test_id="T1", route="https://example.com/dashboard",
                                  evidence=["assertion:T1:A1", "assertion:T1:A2"])]
        # AI_INFERENCE + no hard evidence, but this is a corroborated
        # multi-signal failure (not text-only) — still correctly dropped
        # under the ordinary AI_INFERENCE-alone rule (needs independent
        # source or hard evidence), demonstrating the fix does NOT
        # loosen the existing rule, only the text-only special case.
        self.assertEqual(validate_findings(findings, results), [])

    def test_login_test_with_corroborating_url_and_independent_source_is_bug_eligible(self):
        results = [make_test_result(
            id="T1", name="Invalid login should not authenticate", status="FAIL",
            expected_outcome="A discovered link target confirms the dashboard route.",
            expected_outcome_source="DISCOVERED_LINK_TARGET",
            final_url="https://example.com/dashboard",
            assertion_results=[
                {"type": "ASSERT_TEXT_VISIBLE", "value": "Invalid credentials", "passed": False,
                 "detail": "Text not found.", "ref": "A1"},
                {"type": "ASSERT_URL", "operator": "CONTAINS", "value": "/login", "passed": False,
                 "detail": "Expected to stay on /login but ended up on /dashboard.", "ref": "A2"},
            ],
        )]
        findings = [make_finding(type="BUG", test_id="T1", route="https://example.com/dashboard",
                                  evidence=["assertion:T1:A1", "assertion:T1:A2"])]
        result = validate_findings(findings, results)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "BUG")

    def test_ai_can_emit_assertion_issue_directly_and_it_still_validates(self):
        results = [make_test_result(
            id="T1", name="Invalid login shows an error", status="FAIL",
            expected_outcome_source="AI_INFERENCE", final_url="https://example.com/login",
        )]
        findings = [make_finding(type="ASSERTION_ISSUE", test_id="T1")]
        result = validate_findings(findings, results)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "ASSERTION_ISSUE")


if __name__ == "__main__":
    unittest.main()
