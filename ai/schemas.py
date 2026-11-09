"""
ai/schemas.py
-------------
JSON Schemas used with OpenAI Structured Outputs so the model's
responses are machine-validated rather than parsed from free text.
"""

TEST_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "expected_outcome": {"type": "string"},
                    "expected_outcome_source": {
                        "type": "string",
                        "description": "Why the expected_outcome is believed to be true. Use an "
                                        "independent source (HTML_REQUIRED_ATTRIBUTE / HTML_PATTERN / "
                                        "HTML_MINLENGTH / HTML_MAXLENGTH / DISCOVERED_LINK_TARGET / "
                                        "OBSERVED_APPLICATION_CONTRACT / DOM_STATE) whenever the "
                                        "discovery data actually supports it. Only use AI_INFERENCE "
                                        "when the expectation is your own reasoning with no discovered "
                                        "constraint backing it — a failed assertion on an AI_INFERENCE "
                                        "test can never be reported as a BUG on its own.",
                        "enum": ["HTML_REQUIRED_ATTRIBUTE", "HTML_PATTERN", "HTML_MINLENGTH",
                                 "HTML_MAXLENGTH", "DISCOVERED_LINK_TARGET",
                                 "OBSERVED_APPLICATION_CONTRACT", "DOM_STATE", "AI_INFERENCE"],
                    },
                    "category": {
                        "type": "string",
                        "enum": ["AUTH", "FORM_VALIDATION", "NAVIGATION", "CONTENT", "EDGE_CASE", "OTHER"],
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["OPEN", "CLICK", "FILL", "SELECT", "PRESS",
                                             "CHECK", "UNCHECK", "WAIT", "SCREENSHOT"],
                                },
                                "url": {"type": ["string", "null"]},
                                "selector": {"type": ["string", "null"]},
                                "value": {"type": ["string", "null"]},
                            },
                            "required": ["action", "url", "selector", "value"],
                        },
                    },
                    "assertions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["ASSERT_TEXT", "ASSERT_TEXT_VISIBLE", "ASSERT_VISIBLE",
                                             "ASSERT_URL", "ASSERT_COUNT", "ASSERT_ELEMENT",
                                             "ASSERT_NOT_VISIBLE", "ASSERT_FIELD_VALIDITY"],
                                },
                                "operator": {
                                    "type": ["string", "null"],
                                    "description": "Required (non-null) when type is ASSERT_URL: "
                                                    "one of EQUALS, CONTAINS, STARTS_WITH. Null for "
                                                    "every other assertion type.",
                                    "enum": ["EQUALS", "CONTAINS", "STARTS_WITH", None],
                                },
                                "selector": {"type": ["string", "null"]},
                                "value": {"type": ["string", "null"]},
                            },
                            "required": ["type", "operator", "selector", "value"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["id", "name", "purpose", "expected_outcome", "expected_outcome_source",
                             "category", "steps", "assertions"],
            },
        }
    },
    "required": ["tests"],
}


FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string",
                             "enum": ["BUG", "IMPROVEMENT", "ASSERTION_ISSUE", "NOT_A_FINDING"]},
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL", "NONE"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "route": {"type": "string"},
                    "test_id": {"type": "string"},
                    "expected": {"type": "string"},
                    "actual": {"type": "string"},
                    "reproduction_steps": {"type": "array", "items": {"type": "string"}},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ONLY exact structured evidence reference strings copied from "
                                        "what was given to you for this test_id, e.g. 'assertion:T1:A1', "
                                        "'console:T1:C1', 'network:T1:N1', 'screenshot:T1:after'. Never "
                                        "free text, never a reference for a different test_id, never a "
                                        "reference that wasn't listed.",
                    },
                    "recommendation": {"type": "string"},
                },
                "required": ["type", "title", "severity", "confidence", "route", "test_id",
                             "expected", "actual", "reproduction_steps", "evidence", "recommendation"],
            },
        }
    },
    "required": ["findings"],
}
