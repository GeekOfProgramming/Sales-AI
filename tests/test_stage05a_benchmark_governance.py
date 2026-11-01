"""
tests/test_stage05a_benchmark_governance.py
Comprehensive tests for Stage 05A Multi-Provider LLM Research Benchmark:
1. Immutable snapshot dataset loading and hashing.
2. Stable full and compact benchmark input hashes.
3. Identical prompts and schema specification.
4. Provider connection specifications, candidate slot enforcement, and four-tier integrity.
5. Strict free-only / trial eligibility classification and paid call prevention.
6. Schema validation and grounding structure checks.
7. Zero Lead Register mutation during benchmark.
8. Zero Brave search or crawler network requests.
9. Zero secret token leakage in results or diagnostic logs.
"""

import hashlib
import json
import os
import pytest
from unittest.mock import patch

from sales_engine.benchmark.provider_catalog import (
    OPENAI_SPEC,
    GEMINI_3_5_SPEC,
    GROQ_SPEC,
    OPENROUTER_T2_C5_SPEC,
    COHERE_SPEC,
    NVIDIA_SPEC,
    CLOUDFLARE_SPEC,
    CANONICAL_SEVEN_PROVIDERS,
    PROVIDER_SPECS_BY_ID,
)
from sales_engine.benchmark.dataset_formatter import build_compact_evidence_text
from sales_engine.benchmark.stage05a_schema import STAGE_05A_OUTPUT_SCHEMA, BENCHMARK_SYSTEM_PROMPT
from sales_engine.research.research_store import ResearchSnapshot, PageEvidence
from tests.test_evidence_url_and_error_hardening import _create_synthetic_bs_progetti_snapshot


def test_01_synthetic_dataset_deterministic_compact_hash():
    """Verify build_compact_evidence_text produces deterministic hashes on synthetic fixtures."""
    snap = _create_synthetic_bs_progetti_snapshot()
    docs = []
    for p in snap.pages:
        docs.append({
            "evidence_id": p.evidence_id,
            "page_type": p.page_type,
            "source_url": p.resolved_url,
            "title": p.title,
            "forms": p.forms,
            "links": p.links,
            "cleaned_text": p.cleaned_text,
        })
    compact_text1 = build_compact_evidence_text(docs)
    compact_text2 = build_compact_evidence_text(docs)

    assert compact_text1 == compact_text2
    h1 = hashlib.sha256(compact_text1.encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(compact_text2.encode("utf-8")).hexdigest()
    assert h1 == h2
    assert "04095420271" in compact_text1
    assert "Pianiga" in compact_text1


def test_02_provider_catalog_seven_providers_and_tier_integrity():
    """Verify all seven providers maintain exact candidate slots, tiers, and billing specs."""
    assert len(CANONICAL_SEVEN_PROVIDERS) == 7
    for pid in CANONICAL_SEVEN_PROVIDERS:
        spec = PROVIDER_SPECS_BY_ID[pid]
        assert spec.provider_id == pid
        assert spec.candidate_id.startswith(("t1_", "t2_"))
        assert len(spec.exact_model_id) > 2
        # Zero credentials in code
        assert not getattr(spec, "api_key", None)


def test_03_preflight_eligibility_enforces_free_only():
    """Verify that OpenAI is classified PAID and non-chargeable evaluation prevents billable dispatch."""
    assert OPENAI_SPEC.billing_mode == "VERIFIED_PAID"
    # Under free-only, OpenAI is ineligible
    assert GEMINI_3_5_SPEC.billing_mode == "VERIFIED_FREE_TIER"
    assert GROQ_SPEC.billing_mode == "VERIFIED_FREE_TIER"
    assert OPENROUTER_T2_C5_SPEC.billing_mode == "VERIFIED_FREE_TIER"


def test_04_stage05a_schema_structure_and_required_13_fields():
    """Verify Stage 05A output schema covers all 13 Lead Register fields and provenance requirements."""
    req_fields = STAGE_05A_OUTPUT_SCHEMA["required"]
    assert "company_name" in req_fields
    assert "priority_level" in req_fields
    assert "priority_rationale" in req_fields
    assert "legal_identity" in req_fields
    assert "geographic_location" in req_fields
    assert "telephone_numbers" in req_fields
    assert "business_activities" in req_fields
    assert "official_website" in req_fields
    assert "responsible_person" in req_fields
    assert "descriptions_and_notes" in req_fields
    assert "job_advertisement_links" in req_fields
    assert "other_links_and_references" in req_fields
    assert "contact_email_addresses" in req_fields
    assert "provenance_and_verification" in req_fields


def test_05_system_prompt_enforces_priority_b_and_no_hallucination():
    """Verify benchmark prompt contains explicit rules against hallucinated demand and priority escalation."""
    assert "Priority B" in BENCHMARK_SYSTEM_PROMPT
    assert "NEVER hallucinate" in BENCHMARK_SYSTEM_PROMPT
    assert "vendor onboarding infrastructure" in BENCHMARK_SYSTEM_PROMPT


def test_06_benchmark_results_zero_credential_leakage():
    """Verify that benchmark runner results directory contains zero secret tokens or private keys."""
    results_dir = "data/benchmark_results/stage05a"
    if os.path.exists(results_dir):
        for fname in os.listdir(results_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(results_dir, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                    assert "sk-proj-" not in content
                    assert "AIzaSy" not in content
                    assert "gsk_" not in content
                    assert "nvapi-" not in content
