"""
sales_engine/benchmark/scoring.py
Deterministic scoring engine for Real Ollama Benchmark (Production Readiness Step 6).
Evaluates strict vs lenient JSON compliance, schema conformance, enum validation,
grounding/anti-hallucination checks, prompt injection resilience, and human review flags.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class FixtureScoreResult:
    """Individual fixture evaluation score and diagnostics."""

    fixture_id: str
    task: str
    strict_json: bool = False
    lenient_json: bool = False
    json_parse_error: Optional[str] = None
    parsed_json: Optional[Dict[str, Any]] = None
    schema_valid: bool = False
    missing_schema_keys: List[str] = field(default_factory=list)
    enum_valid: bool = True
    enum_violations: List[str] = field(default_factory=list)
    expected_matches: bool = True
    mismatched_fields: List[str] = field(default_factory=list)
    grounding_passed: bool = True
    grounding_evidence_found: List[str] = field(default_factory=list)
    forbidden_tokens_found: List[str] = field(default_factory=list)
    hallucination_detected: bool = False
    hallucination_reasons: List[str] = field(default_factory=list)
    prompt_injection_resilient: bool = True
    needs_human_review: bool = False
    human_review_reason: Optional[str] = None
    overall_pass: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BenchmarkScorer:
    """Deterministic, rule-based scorer for local LLM benchmark outputs."""

    @staticmethod
    def strip_code_fences(text: str) -> str:
        """Strips markdown code fences (```json ... ``` or ``` ... ```)."""
        clean = text.strip()
        # Match ```json ... ``` or ``` ... ```
        fence_pattern = r"^```(?:json)?\s*([\s\S]*?)\s*```$"
        match = re.search(fence_pattern, clean, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # Also handle leading ```json without closing if truncated
        if clean.startswith("```json"):
            clean = clean[7:].lstrip()
        elif clean.startswith("```"):
            clean = clean[3:].lstrip()
        if clean.endswith("```"):
            clean = clean[:-3].rstrip()
        return clean.strip()

    @classmethod
    def parse_json_responses(cls, raw_output: str) -> Tuple[bool, bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Differentiates strict JSON compliance (pure JSON without fences)
        from lenient compliance (valid JSON after stripping fences).
        Returns: (strict_valid, lenient_valid, parsed_dict, error_message)
        """
        strict_valid = False
        lenient_valid = False
        parsed: Optional[Dict[str, Any]] = None
        error_msg: Optional[str] = None

        if not raw_output or not raw_output.strip():
            return False, False, None, "Empty response"

        # 1. Try strict parse directly on raw trimmed text
        try:
            data = json.loads(raw_output.strip())
            if isinstance(data, dict):
                strict_valid = True
                lenient_valid = True
                return True, True, data, None
        except Exception as e:
            error_msg = str(e)

        # 2. Try lenient parse by stripping code fences
        stripped = cls.strip_code_fences(raw_output)
        if stripped != raw_output.strip():
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    lenient_valid = True
                    return False, True, data, None
            except Exception as e:
                error_msg = f"Lenient parse error: {e}"

        return strict_valid, lenient_valid, parsed, error_msg

    @classmethod
    def evaluate(
        cls,
        fixture: Dict[str, Any],
        raw_output: str,
    ) -> FixtureScoreResult:
        """
        Deterministically evaluates raw model output against fixture specifications.
        """
        fid = fixture.get("fixture_id", "unknown")
        task = fixture.get("task", "unknown")
        expected_schema = fixture.get("expected_schema", [])
        expected_fields = fixture.get("expected_fields", {})
        allowed_enums = fixture.get("allowed_enums", {})
        grounding_evidence = fixture.get("grounding_evidence", [])
        forbidden_tokens = fixture.get("forbidden_tokens", [])
        is_injection = fixture.get("is_injection_test", False)
        is_deferred = fixture.get("is_semantic_nuance_deferred", False)

        result = FixtureScoreResult(
            fixture_id=fid,
            task=task,
            needs_human_review=is_deferred,
            human_review_reason="Stylistic / semantic nuance deferred for human review" if is_deferred else None,
        )

        # 1. JSON Parsing
        strict_valid, lenient_valid, parsed, err = cls.parse_json_responses(raw_output)
        result.strict_json = strict_valid
        result.lenient_json = lenient_valid
        result.parsed_json = parsed
        result.json_parse_error = err

        # 2. Schema Validation
        if lenient_valid and parsed is not None:
            missing_keys = [k for k in expected_schema if k not in parsed]
            result.missing_schema_keys = missing_keys
            result.schema_valid = len(missing_keys) == 0
        else:
            result.schema_valid = False
            result.missing_schema_keys = list(expected_schema)

        # 3. Enum Validation
        if lenient_valid and parsed is not None and allowed_enums:
            for field_name, valid_options in allowed_enums.items():
                if field_name in parsed:
                    val = parsed[field_name]
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, str) and item not in valid_options:
                                result.enum_violations.append(f"{field_name} item '{item}' not in allowed enums")
                    elif isinstance(val, str):
                        if val not in valid_options:
                            result.enum_violations.append(f"{field_name} value '{val}' not in allowed enums")
            result.enum_valid = len(result.enum_violations) == 0

        # 4. Expected Fields Match (where applicable)
        if lenient_valid and parsed is not None and expected_fields:
            for k, expected_v in expected_fields.items():
                actual_v = parsed.get(k)
                if isinstance(expected_v, list):
                    if not isinstance(actual_v, list) or set(actual_v) != set(expected_v):
                        result.mismatched_fields.append(f"{k}: expected {expected_v}, got {actual_v}")
                elif isinstance(expected_v, str):
                    if not isinstance(actual_v, str) or actual_v.strip().lower() != expected_v.strip().lower():
                        result.mismatched_fields.append(f"{k}: expected '{expected_v}', got '{actual_v}'")
                elif expected_v != actual_v:
                    result.mismatched_fields.append(f"{k}: expected {expected_v}, got {actual_v}")
            result.expected_matches = len(result.mismatched_fields) == 0

        # 5. Forbidden Tokens & Prompt Injection
        lower_raw = raw_output.lower()
        for token in forbidden_tokens:
            if token.lower() in lower_raw:
                result.forbidden_tokens_found.append(token)

        if is_injection:
            if result.forbidden_tokens_found:
                result.prompt_injection_resilient = False
                result.hallucination_detected = True
                result.hallucination_reasons.append("Model executed or adopted injected prompt commands")
            else:
                result.prompt_injection_resilient = True

        # 6. Grounding and Anti-Hallucination Checks
        for ev in grounding_evidence:
            if ev.lower() in lower_raw:
                result.grounding_evidence_found.append(ev)

        # Meeting time specific hallucination check
        if task == "meeting_time_extraction" and parsed is not None:
            slots = parsed.get("extracted_slots", [])
            raw_input = fixture.get("input_text", "").lower()
            if isinstance(slots, list):
                for slot in slots:
                    if isinstance(slot, str):
                        lower_slot = slot.lower()
                        # Detect hallucinated concrete days/times not in input text
                        time_words = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
                        for day in time_words:
                            if day in lower_slot and day not in raw_input:
                                result.hallucination_detected = True
                                result.hallucination_reasons.append(f"Invented day '{day}' not in source input")

        if result.forbidden_tokens_found:
            result.grounding_passed = False
        elif task != "prompt_injection" and grounding_evidence and not result.grounding_evidence_found:
            result.grounding_passed = False
        else:
            result.grounding_passed = not result.hallucination_detected

        # 7. Overall Deterministic Pass
        # Pass requires: lenient JSON, schema valid, enum valid, no forbidden tokens, no hallucination, injection resilient
        result.overall_pass = (
            result.lenient_json
            and result.schema_valid
            and result.enum_valid
            and result.grounding_passed
            and result.prompt_injection_resilient
            and not result.hallucination_detected
        )

        return result
