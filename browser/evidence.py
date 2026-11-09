"""
browser/evidence.py
--------------------
Central store for evidence artifacts (currently: screenshots).

The web UI must never see a raw filesystem path. Instead, every stored
artifact gets an opaque ID, and the Flask app exposes it at
`/artifact/<id>`. This module owns the mapping from ID -> file on disk.
"""

from __future__ import annotations

import os
import uuid

ARTIFACT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "reports", "artifacts")
os.makedirs(ARTIFACT_DIR, exist_ok=True)


def save_screenshot(run_id: str, label: str, png_bytes: bytes) -> str:
    """
    Save PNG bytes to disk under an opaque artifact ID and return that ID.
    The ID (not the path) is what gets embedded in reports / sent to the
    browser.
    """
    if not png_bytes:
        return None
    artifact_id = f"{run_id}_{label}_{uuid.uuid4().hex[:8]}.png"
    # Keep the ID filesystem-safe.
    artifact_id = artifact_id.replace("/", "_").replace("\\", "_").replace(" ", "_")
    path = os.path.join(ARTIFACT_DIR, artifact_id)
    with open(path, "wb") as f:
        f.write(png_bytes)
    return artifact_id


def load_screenshot_bytes(artifact_id: str) -> bytes | None:
    """
    Read back the raw PNG bytes for a previously saved artifact ID, for
    cases (like sending a screenshot to a vision-capable model) where the
    caller needs the actual image data rather than just a serving path.
    Uses the same safe path resolution as artifact_path.
    """
    path = artifact_path(artifact_id)
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def artifact_path(artifact_id: str) -> str | None:
    """
    Resolve an artifact ID to a filesystem path, safely — rejects any ID
    that isn't a plain filename already known to live in ARTIFACT_DIR.
    """
    if not artifact_id:
        return None
    # Defense in depth: strip any path components, only allow the basename.
    safe_name = os.path.basename(artifact_id)
    candidate = os.path.join(ARTIFACT_DIR, safe_name)
    real_candidate = os.path.realpath(candidate)
    real_dir = os.path.realpath(ARTIFACT_DIR)
    if not real_candidate.startswith(real_dir + os.sep):
        return None
    if not os.path.isfile(real_candidate):
        return None
    return real_candidate
