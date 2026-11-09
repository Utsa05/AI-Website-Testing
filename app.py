"""
app.py
------
Main Flask application for the AI App Tester.

Orchestrates the end-to-end pipeline:
  1. Connect to application
  2. Discover application (Playwright)
  3. Generate AI tests (OpenAI, structured output)
  4. Validate tests
  5. Execute browser actions (Playwright)
  6. Analyse evidence (OpenAI)
  7. Build developer report

Runs each pipeline in a background thread so the UI can poll progress.
The OpenAI API key is supplied per-run from the browser and is never
written to disk, logs, or session storage — it lives only in local
variables for the duration of the run's background thread.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_file, abort

from browser.discovery import discover
from browser.executor import execute_test
from browser.evidence import save_screenshot, artifact_path
from ai.test_planner import generate_test_plan
from ai.evidence_analyzer import analyze_evidence
from ai.client import AIError
from reporting.report import build_report

app = Flask(__name__)

DEFAULT_MODEL = "gpt-4o-mini"

PIPELINE_STEPS = [
    "Connect to application",
    "Discover application",
    "Generate AI tests",
    "Validate tests",
    "Execute browser actions",
    "Analyse evidence",
    "Build developer report",
]

# In-memory run registry. Fine for a local single-user prototype.
RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_status(run_id: str, step_index: int, message: str = "", error: str | None = None, done: bool = False):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return
        run["status"] = {
            "step_index": step_index,
            "step_name": PIPELINE_STEPS[step_index] if 0 <= step_index < len(PIPELINE_STEPS) else "Done",
            "message": message,
            "error": error,
            "done": done,
            "total_steps": len(PIPELINE_STEPS),
        }


def _log_error(run_id: str, msg: str):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if run is not None:
            run.setdefault("errors_log", []).append(msg)


def _validate_url(url: str) -> str | None:
    """Returns an error message if the URL looks invalid, else None."""
    if not url or not url.strip():
        return "Application URL is required."
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return "Application URL must start with http:// or https://"
    if not parsed.netloc:
        return "Application URL is missing a host."
    return None


def _run_pipeline(run_id: str, target_url: str, api_key: str, model: str, headless: bool):
    try:
        # Step 1: Connect to application
        _set_status(run_id, 0, "Connecting to target application...")
        time.sleep(0.2)

        # Step 2: Discover application
        _set_status(run_id, 1, "Launching Playwright and discovering the application...")
        try:
            discovery_raw = discover(target_url, headless=headless)
        except Exception as e:  # noqa: BLE001
            _log_error(run_id, f"Discovery failed: {e}")
            _set_status(run_id, 1, error=f"Could not reach or discover the application: {e}", done=True)
            return

        # Persist discovery screenshots as evidence artifacts (opaque IDs, not paths)
        pages_serialized = []
        for pg in discovery_raw["pages"]:
            d = pg.to_dict()
            shot_bytes = getattr(pg, "_screenshot_bytes", None)
            d.pop("_screenshot_bytes", None)
            if shot_bytes:
                label = f"discovery_{abs(hash(pg.url)) % 100000}"
                d["screenshot_path"] = save_screenshot(run_id, label, shot_bytes)
            pages_serialized.append(d)

        discovery = {
            "start_url": discovery_raw["start_url"],
            "page_count": discovery_raw["page_count"],
            "pages": pages_serialized,
            "errors": discovery_raw.get("errors", []),
        }

        if not discovery["pages"] or all(p.get("load_error") for p in discovery["pages"]):
            _set_status(run_id, 1, error="The application could not be loaded — check the URL and that it is running.", done=True)
            with RUNS_LOCK:
                RUNS[run_id]["discovery"] = discovery
            return

        with RUNS_LOCK:
            RUNS[run_id]["discovery"] = discovery

        # Step 3: Generate AI tests
        _set_status(run_id, 2, f"Asking OpenAI ({model}) to plan tests from discovery data...")
        test_plan = generate_test_plan(api_key, model, discovery)
        with RUNS_LOCK:
            RUNS[run_id]["test_plan"] = test_plan
        if test_plan.get("ai_error"):
            _log_error(run_id, f"AI test planning issue: {test_plan['ai_error']} (used source={test_plan.get('source')})")

        # Step 4: Validate tests (already cross-checked inside generate_test_plan;
        # this step surfaces that validation explicitly in the pipeline/report)
        blocked_at_plan_time = test_plan.get("blocked", [])
        _set_status(run_id, 3, f"Validated {len(test_plan.get('tests', []))} test(s) against discovered "
                                f"elements ({len(blocked_at_plan_time)} blocked before execution).")
        if not test_plan.get("tests") and not blocked_at_plan_time:
            _set_status(run_id, 3, error="No valid tests could be generated or derived from discovery data.", done=True)
            return

        # Step 5: Execute browser actions
        _set_status(run_id, 4, "Executing tests with a real Playwright browser session...")
        test_results = []

        # Tests that were already rejected at plan time (e.g. no reliable
        # expected outcome could be established) never reach the executor —
        # they are reported as BLOCKED directly, never as a fake PASS.
        for bt in blocked_at_plan_time:
            test_results.append({
                "id": bt.get("id"), "name": bt.get("name", bt.get("id")),
                "purpose": bt.get("purpose", ""), "expected_outcome": bt.get("expected_outcome", ""),
                "expected_outcome_source": bt.get("expected_outcome_source", "AI_INFERENCE"),
                "category": bt.get("category", "OTHER"), "validation_warnings": [],
                "fallback": bt.get("fallback", False),
                "status": "BLOCKED", "error": bt.get("reason", "Blocked before execution."),
                "steps_executed": [], "assertion_results": [], "console_errors": [],
                "failed_requests": [], "final_url": None, "screenshot_before": None,
                "screenshot_after": None, "duration_ms": 0, "expected": bt.get("reason", ""),
            })

        for i, test in enumerate(test_plan["tests"]):
            _set_status(run_id, 4, f"Executing test {i + 1}/{len(test_plan['tests'])}: {test.get('name')}")
            try:
                result = execute_test(discovery["start_url"], run_id, test, headless=headless)
                result["purpose"] = test.get("purpose", "")
                result["expected_outcome"] = test.get("expected_outcome", "")
                result["expected_outcome_source"] = test.get("expected_outcome_source", "AI_INFERENCE")
                result["category"] = test.get("category", "OTHER")
                result["validation_warnings"] = test.get("validation_warnings", [])
                result["fallback"] = test.get("fallback", False)
                result["expected"] = test.get("expected_outcome") or "; ".join(
                    f"{a.get('type')} {a.get('operator') or ''} {a.get('value') or a.get('selector') or ''}".strip()
                    for a in test.get("assertions", [])
                )
            except Exception as e:  # noqa: BLE001
                _log_error(run_id, f"Executor crashed on test {test.get('id')}: {e}\n{traceback.format_exc()}")
                result = {
                    "id": test.get("id"), "name": test.get("name"), "purpose": test.get("purpose", ""),
                    "expected_outcome": test.get("expected_outcome", ""),
                    "expected_outcome_source": test.get("expected_outcome_source", "AI_INFERENCE"),
                    "status": "ERROR", "error": f"Executor crashed: {e}",
                    "steps_executed": [], "assertion_results": [], "console_errors": [],
                    "failed_requests": [], "final_url": None, "screenshot_before": None,
                    "screenshot_after": None, "duration_ms": 0,
                }
            test_results.append(result)

        with RUNS_LOCK:
            RUNS[run_id]["test_results"] = test_results

        # Step 6: Analyse evidence
        _set_status(run_id, 5, f"Sending execution evidence to OpenAI ({model}) for analysis...")
        analysis = analyze_evidence(api_key, model, test_results)
        with RUNS_LOCK:
            RUNS[run_id]["findings"] = analysis.get("findings", [])
            RUNS[run_id]["analysis_source"] = analysis.get("source")
            RUNS[run_id]["vision_used"] = analysis.get("vision_used", False)
        if analysis.get("ai_error"):
            _log_error(run_id, f"AI evidence analysis issue: {analysis['ai_error']} (used source={analysis.get('source')})")

        # Step 7: Build developer report
        _set_status(run_id, 6, "Building developer report...")
        with RUNS_LOCK:
            RUNS[run_id]["finished_at"] = _now_iso()
            report = build_report(RUNS[run_id])
            RUNS[run_id]["report"] = report

        # Overall run status is never a blanket "100% success" if anything
        # major failed or was blocked — SUCCESS / PARTIAL / BLOCKED / FAILED,
        # accurately reflecting what actually happened (Part 15/16).
        run_status = report["summary"].get("run_status", "SUCCESS")
        _set_status(run_id, 6, f"Complete ({run_status}).", done=True)

    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        _log_error(run_id, f"Unhandled pipeline error: {e}\n{tb}")
        _set_status(run_id, 6, error=f"Unhandled error: {e}", done=True)
    finally:
        # Ensure the API key never lingers in memory longer than needed.
        api_key = None  # noqa: F841


@app.route("/")
def index():
    return render_template("index.html", default_model=DEFAULT_MODEL)


@app.route("/run", methods=["POST"])
def run_test():
    data = request.get_json(silent=True) or {}
    api_key = (data.get("api_key") or "").strip()
    target_url = (data.get("target_url") or "").strip()
    model = (data.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    headless = data.get("headless", True)

    url_error = _validate_url(target_url)
    if url_error:
        return jsonify({"error": url_error}), 400
    if not api_key:
        return jsonify({"error": "OpenAI API key is required."}), 400

    run_id = uuid.uuid4().hex[:12]
    with RUNS_LOCK:
        RUNS[run_id] = {
            "run_id": run_id,
            "target_url": target_url,
            "model": model,
            "started_at": _now_iso(),
            "finished_at": None,
            "status": {"step_index": 0, "step_name": PIPELINE_STEPS[0], "message": "Queued...",
                       "error": None, "done": False, "total_steps": len(PIPELINE_STEPS)},
            "discovery": None,
            "test_plan": None,
            "test_results": [],
            "findings": [],
            "analysis_source": None,
            "errors_log": [],
            "report": None,
        }

    thread = threading.Thread(target=_run_pipeline, args=(run_id, target_url, api_key, model, headless), daemon=True)
    thread.start()

    return jsonify({"run_id": run_id})


@app.route("/status/<run_id>")
def status(run_id):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return jsonify({"error": "Unknown run_id"}), 404
        return jsonify({
            "run_id": run_id,
            "status": run["status"],
            "target_url": run["target_url"],
            "model": run["model"],
        })


@app.route("/report/<run_id>")
def report_json(run_id):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return jsonify({"error": "Unknown run_id"}), 404
        if not run.get("report"):
            return jsonify({"error": "Report not ready yet.", "status": run["status"]}), 409
        return jsonify(run["report"])


@app.route("/report/<run_id>/view")
def report_view(run_id):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            abort(404)
        report = run.get("report")
    if not report:
        return render_template("report_pending.html", run_id=run_id)
    return render_template("report.html", report=report, run_id=run_id, errors_log=run.get("errors_log", []))


@app.route("/artifact/<artifact_id>")
def artifact(artifact_id):
    path = artifact_path(artifact_id)
    if not path:
        abort(404)
    return send_file(path, mimetype="image/png")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
