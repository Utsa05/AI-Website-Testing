"""
ai/client.py
------------
Thin wrapper around the OpenAI API. The API key comes from the request
for the current run only — it is never read from disk, never hard-coded,
and never persisted anywhere by this module.

Uses Structured Outputs (JSON schema response_format) so responses are
validated by the API itself rather than parsed with fragile free-form
regexes.
"""

from __future__ import annotations

import base64

try:
    from openai import OpenAI, APIError, AuthenticationError, RateLimitError, APITimeoutError, BadRequestError
    _OPENAI_IMPORT_ERROR = None
except ImportError as _e:  # pragma: no cover - exercised only when the openai package is absent
    OpenAI = None
    # Fall back to plain Exception subclasses so `except AuthenticationError` etc.
    # below still work (they just never actually match anything real) instead
    # of raising NameError. This keeps this module importable — and therefore
    # keeps the rest of the app importable/testable — even in an environment
    # where the `openai` package hasn't been installed yet; get_client() below
    # raises a clear, actionable AIError the moment it's actually needed.
    class APIError(Exception): ...
    class AuthenticationError(APIError): ...
    class RateLimitError(APIError): ...
    class APITimeoutError(APIError): ...
    class BadRequestError(APIError): ...
    _OPENAI_IMPORT_ERROR = _e

# Models known to support image (vision) input via chat.completions as of
# this writing. This is a conservative allowlist, not a guess: if the
# selected model isn't recognised as vision-capable, we never claim visual
# analysis happened — we just fall back to DOM/evidence-only analysis and
# say so explicitly in the report.
VISION_CAPABLE_MODEL_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "o4", "o3")


def model_supports_vision(model: str) -> bool:
    if not model:
        return False
    m = model.lower()
    return any(m.startswith(p) for p in VISION_CAPABLE_MODEL_PREFIXES)


class AIError(Exception):
    """Raised for any AI-related failure the caller should handle (retry / fallback)."""

    def __init__(self, message: str, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind  # "auth" | "model" | "timeout" | "rate_limit" | "response" | "unknown"


def get_client(api_key: str) -> "OpenAI":
    if not api_key or not api_key.strip():
        raise AIError("No OpenAI API key was provided for this run.", kind="auth")
    if OpenAI is None:
        raise AIError(f"The 'openai' package is not installed/available: {_OPENAI_IMPORT_ERROR}", kind="unknown")
    return OpenAI(api_key=api_key.strip())


def structured_completion(api_key: str, model: str, system_prompt: str, user_prompt: str,
                           json_schema: dict, schema_name: str, max_tokens: int = 4000,
                           temperature: float = 0.2, images: list[tuple[str, bytes]] | None = None) -> dict:
    """
    Calls the OpenAI Chat Completions API with a strict JSON schema
    response format and returns the parsed JSON object. Raises AIError
    on any failure so the caller can retry / repair / fall back.

    `images`, if given, is a list of (label, png_bytes) tuples. Each is
    sent to the model as an actual image content block (using the
    supported OpenAI vision input mechanism) alongside the text prompt —
    this is what lets the model genuinely inspect a screenshot rather than
    just being told one exists. Callers MUST check `model_supports_vision`
    first; this function does not silently drop unsupported images, it
    will let the API itself reject them so the failure is visible.
    """
    client = get_client(api_key)

    if images:
        content = [{"type": "text", "text": user_prompt}]
        for label, png_bytes in images:
            if not png_bytes:
                continue
            b64 = base64.b64encode(png_bytes).decode("ascii")
            content.append({"type": "text", "text": f"[Image below: {label}]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "auto"},
            })
        user_message = {"role": "user", "content": content}
    else:
        user_message = {"role": "user", "content": user_prompt}

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            },
        )
    except AuthenticationError as e:
        raise AIError(f"OpenAI authentication failed — check the API key. ({e})", kind="auth") from e
    except RateLimitError as e:
        raise AIError(f"OpenAI rate limit / quota error. ({e})", kind="rate_limit") from e
    except APITimeoutError as e:
        raise AIError(f"OpenAI request timed out. ({e})", kind="timeout") from e
    except BadRequestError as e:
        msg = str(e)
        if "model" in msg.lower():
            raise AIError(f"The selected model is invalid or unavailable: {msg}", kind="model") from e
        raise AIError(f"OpenAI rejected the request: {msg}", kind="response") from e
    except APIError as e:
        raise AIError(f"OpenAI API error: {e}", kind="unknown") from e
    except Exception as e:  # noqa: BLE001
        raise AIError(f"Unexpected error calling OpenAI: {e}", kind="unknown") from e

    try:
        choice = response.choices[0]
        content = choice.message.content
        finish_reason = choice.finish_reason
    except (IndexError, AttributeError) as e:
        raise AIError(f"Malformed OpenAI response: {e}", kind="response") from e

    if finish_reason == "length":
        raise AIError("OpenAI response was truncated (max_tokens too low).", kind="response")

    if not content:
        raise AIError("OpenAI returned an empty response.", kind="response")

    import json
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise AIError(f"OpenAI response was not valid JSON: {e}", kind="response") from e

    return parsed
