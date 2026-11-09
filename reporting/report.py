"""
reporting/report.py
--------------------
Assembles the final developer-facing report from a completed run's data.
Pure data-shaping — no I/O — so it's easy to feed into the HTML template
or return as JSON.
"""

from __future__ import annotations

from ai.evidence_index import build_evidence_index, parse_reference, describe_evidence


def _attach_evidence_chain(findings: list, test_results: list) -> list:
    """
    Make every BUG/IMPROVEMENT traceable end-to-end (Part 7 of the second
    correction pass): Finding -> Test ID -> Expected outcome -> Expected
    outcome source -> Executed actions -> Assertion -> Actual result ->
    Evidence -> Screenshot -> Conclusion. Each finding gets an
    `evidence_chain` dict with all of this pre-resolved so the template
    doesn't need to re-derive it.
    """
    by_id = {r.get("id"): r for r in test_results}
    enriched = []
    for f in findings:
        f = dict(f)
        test = by_id.get(f.get("test_id"), {})
        index = build_evidence_index(test)

        executed_actions = [
            f"{s.get('action')} {s.get('selector') or s.get('url') or ''}".strip()
            for s in test.get("steps_executed", [])
        ]
        assertion_summaries = [
            f"{a.get('type')} {a.get('operator') or ''} {a.get('value') or a.get('selector') or ''} "
            f"-> passed={a.get('passed')}".strip()
            for a in test.get("assertion_results", [])
        ]

        resolved_evidence = []
        screenshot_artifact = None
        for ref in f.get("evidence") or []:
            data = index.get(ref)
            if data:
                resolved_evidence.append(describe_evidence(ref, data))
                if data.get("kind") == "screenshot" and not screenshot_artifact:
                    screenshot_artifact = data.get("artifact")
            else:
                resolved_evidence.append(f"[{ref}] (reference could not be resolved)")

        if not screenshot_artifact:
            # Fall back to the test's own after-screenshot for visual
            # context in the report even if the finding didn't explicitly
            # cite the screenshot reference.
            screenshot_artifact = test.get("screenshot_after") or test.get("screenshot_before")

        f["evidence_chain"] = {
            "test_id": f.get("test_id"),
            "test_name": test.get("name"),
            "expected_outcome": test.get("expected_outcome") or f.get("expected"),
            "expected_outcome_source": test.get("expected_outcome_source", "AI_INFERENCE"),
            "executed_actions": executed_actions,
            "assertions": assertion_summaries,
            "actual_result": f.get("actual") or test.get("error") or "",
            "evidence_resolved": resolved_evidence,
            "screenshot_artifact": screenshot_artifact,
            "conclusion": f.get("recommendation", ""),
        }
        enriched.append(f)
    return enriched


def build_report(run: dict) -> dict:
    test_results = run.get("test_results", [])
    findings = run.get("findings", [])

    passed = sum(1 for t in test_results if t.get("status") == "PASS")
    failed = sum(1 for t in test_results if t.get("status") == "FAIL")
    blocked = sum(1 for t in test_results if t.get("status") == "BLOCKED")
    errored = sum(1 for t in test_results if t.get("status") == "ERROR")

    findings = _attach_evidence_chain(findings, test_results)
    bugs = [f for f in findings if f.get("type") == "BUG"]
    improvements = [f for f in findings if f.get("type") == "IMPROVEMENT"]
    assertion_issues = [f for f in findings if f.get("type") == "ASSERTION_ISSUE"]

    # Overall run status — never reported as a blanket success if anything
    # major failed or was blocked (Part 15/16 of the correction spec).
    total = len(test_results)
    if total == 0:
        run_status = "FAILED"
    elif errored == total:
        run_status = "FAILED"
    elif errored > 0 or blocked > 0:
        run_status = "PARTIAL"
    else:
        run_status = "SUCCESS"

    summary = {
        "run_id": run.get("run_id"),
        "target_url": run.get("target_url"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "pages_discovered": run.get("discovery", {}).get("page_count", 0),
        "tests_generated": len(run.get("test_plan", {}).get("tests", [])),
        "tests_executed": len(test_results),
        "passed": passed,
        "failed": failed,
        "blocked": blocked,
        "errored": errored,
        "bugs_found": len(bugs),
        "improvements_found": len(improvements),
        "assertion_issues_found": len(assertion_issues),
        "test_plan_source": run.get("test_plan", {}).get("source"),
        "analysis_source": run.get("analysis_source"),
        "vision_used": run.get("vision_used", False),
        "model": run.get("model"),
        "run_status": run_status,
    }

    return {
        "summary": summary,
        "tests": test_results,
        "bugs": bugs,
        "improvements": improvements,
        "assertion_issues": assertion_issues,
        "errors_log": run.get("errors_log", []),
    }
