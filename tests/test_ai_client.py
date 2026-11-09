import ast
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai.client as client


class FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = mock.Mock(content=content)
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [FakeChoice(content, finish_reason)]


class FakeCompletions:
    def __init__(self, behavior):
        self._behavior = behavior  # callable(**kwargs) -> FakeResponse or raises

    def create(self, **kwargs):
        return self._behavior(**kwargs)


class FakeChat:
    def __init__(self, behavior):
        self.completions = FakeCompletions(behavior)


class FakeOpenAIClient:
    def __init__(self, behavior):
        self.chat = FakeChat(behavior)


class TestApiKeyHandling(unittest.TestCase):
    def test_missing_api_key_raises_auth_error(self):
        with self.assertRaises(client.AIError) as cm:
            client.get_client("")
        self.assertEqual(cm.exception.kind, "auth")

    def test_whitespace_only_api_key_raises_auth_error(self):
        with self.assertRaises(client.AIError) as cm:
            client.get_client("   ")
        self.assertEqual(cm.exception.kind, "auth")

    def test_api_key_never_written_to_disk_by_client_module(self):
        """
        Static check that ai/client.py contains no file-write calls at all —
        the module's only job is to hand the key to the OpenAI SDK object
        for the duration of the call. This guards against a future edit
        accidentally introducing persistence (logging, caching, etc).
        """
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai", "client.py")
        with open(src_path) as f:
            source = f.read()
        tree = ast.parse(source)
        write_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
        ]
        self.assertEqual(write_calls, [], "ai/client.py must never open files for writing.")
        self.assertNotIn("localStorage", source)
        self.assertNotIn(".env", source)


def _make_error(cls, message):
    """
    Construct an OpenAI SDK error compatibly whether the real `openai`
    package (whose error classes require response/body kwargs) or our
    plain-Exception fallback (used when openai isn't installed) is active.
    """
    try:
        return cls(message)
    except TypeError:
        fake_response = mock.Mock()
        fake_response.request = mock.Mock()
        return cls(message, response=fake_response, body=None)


class TestStructuredCompletionErrorHandling(unittest.TestCase):
    def _patch_client(self, behavior):
        fake = FakeOpenAIClient(behavior)
        return mock.patch.object(client, "get_client", return_value=fake)

    def test_malformed_json_raises_response_error(self):
        def behavior(**kwargs):
            return FakeResponse(content="{not valid json")
        with self._patch_client(behavior):
            with self.assertRaises(client.AIError) as cm:
                client.structured_completion("key", "gpt-4o-mini", "sys", "user", {}, "schema")
            self.assertEqual(cm.exception.kind, "response")

    def test_empty_content_raises_response_error(self):
        def behavior(**kwargs):
            return FakeResponse(content=None)
        with self._patch_client(behavior):
            with self.assertRaises(client.AIError) as cm:
                client.structured_completion("key", "gpt-4o-mini", "sys", "user", {}, "schema")
            self.assertEqual(cm.exception.kind, "response")

    def test_truncated_response_raises_response_error(self):
        def behavior(**kwargs):
            return FakeResponse(content='{"tests": []}', finish_reason="length")
        with self._patch_client(behavior):
            with self.assertRaises(client.AIError) as cm:
                client.structured_completion("key", "gpt-4o-mini", "sys", "user", {}, "schema")
            self.assertEqual(cm.exception.kind, "response")

    def test_valid_json_is_parsed_and_returned(self):
        def behavior(**kwargs):
            return FakeResponse(content='{"tests": []}')
        with self._patch_client(behavior):
            result = client.structured_completion("key", "gpt-4o-mini", "sys", "user", {}, "schema")
            self.assertEqual(result, {"tests": []})

    def test_authentication_error_maps_to_auth_kind(self):
        def behavior(**kwargs):
            raise _make_error(client.AuthenticationError, "bad key")
        with self._patch_client(behavior):
            with self.assertRaises(client.AIError) as cm:
                client.structured_completion("badkey", "gpt-4o-mini", "sys", "user", {}, "schema")
            self.assertEqual(cm.exception.kind, "auth")

    def test_rate_limit_error_maps_to_rate_limit_kind(self):
        def behavior(**kwargs):
            raise _make_error(client.RateLimitError, "slow down")
        with self._patch_client(behavior):
            with self.assertRaises(client.AIError) as cm:
                client.structured_completion("key", "gpt-4o-mini", "sys", "user", {}, "schema")
            self.assertEqual(cm.exception.kind, "rate_limit")

    def test_timeout_error_maps_to_timeout_kind(self):
        def behavior(**kwargs):
            raise _make_error(client.APITimeoutError, "too slow")
        with self._patch_client(behavior):
            with self.assertRaises(client.AIError) as cm:
                client.structured_completion("key", "gpt-4o-mini", "sys", "user", {}, "schema")
            self.assertEqual(cm.exception.kind, "timeout")


class TestVisionCapability(unittest.TestCase):
    def test_gpt4o_supports_vision(self):
        self.assertTrue(client.model_supports_vision("gpt-4o-mini"))

    def test_gpt35_does_not_support_vision(self):
        self.assertFalse(client.model_supports_vision("gpt-3.5-turbo"))

    def test_empty_model_does_not_support_vision(self):
        self.assertFalse(client.model_supports_vision(""))
        self.assertFalse(client.model_supports_vision(None))


if __name__ == "__main__":
    unittest.main()
