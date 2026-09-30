import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sales_engine.analysis.website_analyzer import (
    WebsiteAnalyzer,
    is_valid_job_title
)
from tests.acceptance.test_phase2_acceptance import match_service_concepts, compute_detailed_diffs

def test_action_phrases_rejected_as_job_titles():
    """Verify that action phrases, gerunds, and business tasks are rejected as job titles."""
    action_phrases = [
        "Implementing custom automation scripts to reduce manual labor.",
        "Deploying Sovereign AI infrastructures for secure data processing.",
        "Optimizing BIM workflows for high-margin billable hours.",
        "Identify operational bottlenecks",
        "Evaluate automation ROI",
        "Writing Python and C# scripts for Revit API automation.",
        "Using pyBIM's API integration for Revit and Navisworks."
    ]
    for phrase in action_phrases:
        assert not is_valid_job_title(phrase), f"Expected '{phrase}' to be rejected as a job title!"

def test_valid_job_titles_accepted():
    """Verify that legitimate professional role titles are recognized."""
    valid_titles = [
        "BIM Manager",
        "Senior BIM Manager",
        "Revit API Developer",
        "BIM Automation Engineer",
        "Head of Digital Delivery",
        "Technical Director",
        "Digital Construction Coordinator",
        "Lead Algorithmic Engineer"
    ]
    for title in valid_titles:
        assert is_valid_job_title(title), f"Expected '{title}' to be accepted as a valid job title!"

def test_empty_valid_negative_signals_remains_empty():
    """Verify that inbound contact restrictions are discarded and negative_signals remains empty."""
    analyzer = WebsiteAnalyzer()
    facts = {
        "company_name": "pyBIM",
        "company_summary": "pyBIM provides BIM automation.",
        "pain_points": ["Manual data entry"],
        "explicit_role_mentions": ["BIM Manager"]
    }
    signals = {
        "buyer_roles": ["BIM Manager"],
        "primary_job_signals": ["BIM Manager"],
        "secondary_job_signals": ["Revit API"],
        "negative_signals": [
            "No public inquiries or inquiries from public email domains.",
            "Submissions lacking a functional code repository link will be rejected"
        ]
    }
    sanitized = analyzer._sanitize_profile(facts, signals)
    assert sanitized["negative_signals"] == [], "Expected inbound contact rules to be discarded!"

def test_buyer_role_fallback_requires_grounding():
    """Verify that buyer_roles are only used as fallback if grounded in Pass A explicit role mentions."""
    analyzer = WebsiteAnalyzer()
    facts = {
        "company_name": "Test Co",
        "company_summary": "Test Summary",
        "pain_points": [],
        "explicit_role_mentions": ["BIM Manager"]  # Only BIM Manager is grounded
    }
    # Model gave actions for primary, and hallucinated ungrounded buyer roles
    signals = {
        "buyer_roles": ["Engineering Manager", "Project Manager", "BIM Manager"],
        "primary_job_signals": ["Implementing automation workflows"],
        "secondary_job_signals": [],
        "negative_signals": []
    }
    sanitized = analyzer._sanitize_profile(facts, signals)
    # The action was rejected. Fallback should only use grounded roles from Pass A:
    assert "BIM Manager" in sanitized["primary_job_signals"]
    assert "Engineering Manager" not in sanitized["primary_job_signals"]

def test_semantic_service_aliases_recognized():
    """Verify that concept aliases correctly map varied wording to the same concept."""
    aliases = {
        "BIM_EXECUTION": [
            "Managed / Tech-Enabled BIM Execution",
            "Turnkey BIM Project Delivery",
            "BIM Modeling"
        ],
        "BIM_AUTOMATION": [
            "Custom Software & Revit Automation",
            "Revit Automation"
        ]
    }
    actual_services = [
        "Turnkey BIM Project Delivery",
        "Custom Software & Revit Automation"
    ]
    missing, extra = match_service_concepts([], actual_services, aliases)
    assert len(missing) == 0, f"Expected 0 missing concepts, got {missing}"
    assert len(extra) == 0, f"Expected 0 extra services, got {extra}"

def test_critical_qa_fail_triggers_failure():
    """Verify that action-phrases in primary_job_signals trigger critical failure."""
    expected = {"company_name": "pyBIM", "primary_job_signals": ["BIM Manager"]}
    actual = {"company_name": "pyBIM", "primary_job_signals": ["Implementing automation"]}
    diffs, is_critical = compute_detailed_diffs(expected, actual)
    assert is_critical is True

def test_qa_review_does_not_fail():
    """Verify that valid titles with minor differences trigger REVIEW (non-critical)."""
    expected = {"company_name": "pyBIM", "target_industries": ["Architecture"]}
    actual = {"company_name": "pyBIM", "target_industries": ["Engineering"], "primary_job_signals": ["BIM Manager"]}
    diffs, is_critical = compute_detailed_diffs(expected, actual)
    assert is_critical is False
    assert len(diffs) > 0

@pytest.mark.asyncio
async def test_two_pass_architecture_compact_pass_b():
    """Verify that Pass B is invoked with the compact structured output of Pass A."""
    analyzer = WebsiteAnalyzer()
    
    mock_facts = {
        "company_name": "pyBIM",
        "company_summary": "pyBIM provides BIM automation.",
        "services": ["Turnkey BIM Project Delivery"],
        "target_industries": ["AEC"],
        "target_company_types": ["Tier-1 Contractors"],
        "pain_points": ["Manual data entry"],
        "explicit_role_mentions": ["Senior BIM Manager"],
        "technologies": ["Revit API", "Python", "C#"],
        "standards": ["ISO 19650"],
        "keywords": ["BIM", "Automation"]
    }
    
    mock_signals = {
        "buyer_roles": ["Senior BIM Manager"],
        "primary_job_signals": ["Senior BIM Manager", "Revit API Developer"],
        "secondary_job_signals": ["Revit API", "ISO 19650"],
        "negative_signals": []
    }
    
    analyzer._pass_a_extract_facts = AsyncMock(return_value=mock_facts)
    analyzer._pass_b_derive_signals = AsyncMock(return_value=mock_signals)
    
    fetch_result = {"pages": [{"url": "https://pybim.com", "text": "Very long 40,000 char website content..."}]}
    
    profile = await analyzer.analyze_website(fetch_result)
    
    # Assert Pass B was called with the compact facts dict, NOT the full website string!
    analyzer._pass_b_derive_signals.assert_called_once_with(mock_facts)
    assert profile.company_name == "pyBIM"
    assert "Senior BIM Manager" in profile.primary_job_signals
    assert "Revit API Developer" in profile.primary_job_signals
