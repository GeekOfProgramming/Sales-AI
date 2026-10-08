# sales_engine/analytics/fixtures.py
"""
Sanitized offline evaluation fixtures for Phase 12 Model Evaluation Framework.
Covers all 6 LLM tasks:
1. website_extraction
2. company_classification
3. email_draft_generation
4. reply_classification
5. reply_suggestion
6. meeting_time_extraction

Contains zero live secrets, zero live emails, and stable versioning ('phase12-v1').
"""

from typing import Dict, Any, List

FIXTURE_SET_VERSION = "phase12-v1"

EVALUATION_FIXTURES: List[Dict[str, Any]] = [
    # -------------------------------------------------------------
    # 1. Website Extraction
    # -------------------------------------------------------------
    {
        "fixture_id": "fix_web_001",
        "task": "website_extraction",
        "input_text": "Milan MEP Engineering SRL provides specialized BIM coordination and HVAC modeling services in Lombardy since 2012. We have 25 engineers and utilize Autodesk Revit, Navisworks, and custom Python automation tools.",
        "expected_schema": ["company_name", "services", "tools", "headcount", "country"],
        "expected_fields": {
            "company_name": "Milan MEP Engineering SRL",
            "services": ["BIM coordination", "HVAC modeling"],
            "tools": ["Autodesk Revit", "Navisworks", "Python automation"],
            "country": "IT",
        },
        "grounding_evidence": ["BIM coordination", "HVAC modeling", "Autodesk Revit"],
    },
    {
        "fixture_id": "fix_web_002",
        "task": "website_extraction",
        "input_text": "Studio Tecnico Roma: Architectural design and planning services across Italy. Contact us at info@studiotecnicoroma.it for civil engineering consultations.",
        "expected_schema": ["company_name", "services", "country"],
        "expected_fields": {
            "company_name": "Studio Tecnico Roma",
            "services": ["Architectural design", "civil engineering"],
            "country": "IT",
        },
        "grounding_evidence": ["Architectural design", "civil engineering"],
    },

    # -------------------------------------------------------------
    # 2. Company Classification
    # -------------------------------------------------------------
    {
        "fixture_id": "fix_class_001",
        "task": "company_classification",
        "input_text": "Engineering firm with 40 Revit modelers working on hospital MEP projects across Europe. High modeling workload and active Revit API automation needs.",
        "expected_schema": ["company_tier", "bim_maturity", "relationship_type", "opportunity_types"],
        "expected_fields": {
            "company_tier": "tier_1",
            "bim_maturity": "high",
            "relationship_type": "service_buyer",
            "opportunity_types": ["direct_bim_delivery", "mep_delivery", "revit_api"],
        },
        "grounding_evidence": ["hospital MEP projects", "Revit API automation"],
    },
    {
        "fixture_id": "fix_class_002",
        "task": "company_classification",
        "input_text": "Software development vendor offering cloud plugins for BIM workflows. Seeking resellers and systems integrators in Southern Europe.",
        "expected_schema": ["company_tier", "relationship_type", "opportunity_types"],
        "expected_fields": {
            "relationship_type": "technology_partner",
            "opportunity_types": ["technology_partnership", "channel_partnership"],
        },
        "grounding_evidence": ["cloud plugins for BIM", "resellers and systems integrators"],
    },

    # -------------------------------------------------------------
    # 3. Email Draft Generation
    # -------------------------------------------------------------
    {
        "fixture_id": "fix_draft_001",
        "task": "email_draft_generation",
        "input_text": "Recipient: Marco Bianchi, Technical Director at Ingegneria MEP Milano. Opportunity: Revit MEP overflow capacity and automated parameter checking.",
        "expected_schema": ["subject", "body"],
        "grounding_evidence": ["Ingegneria MEP Milano", "Revit MEP", "overflow capacity"],
        "forbidden_tokens": ["guarantee 100% win", "free trial unlimited", "urgent partnership contract"],
    },
    {
        "fixture_id": "fix_draft_002",
        "task": "email_draft_generation",
        "input_text": "Recipient: Sara Rossi, BIM Manager at Progetti Verona. Opportunity: Revit Dynamo & Python automation scripts for MEP model verification.",
        "expected_schema": ["subject", "body"],
        "grounding_evidence": ["Progetti Verona", "BIM Manager", "automation scripts"],
        "forbidden_tokens": ["spam", "click here to claim prize"],
    },

    # -------------------------------------------------------------
    # 4. Reply Classification
    # -------------------------------------------------------------
    {
        "fixture_id": "fix_reply_cls_001",
        "task": "reply_classification",
        "input_text": "Thanks for reaching out. Yes, we are currently looking for additional Revit MEP modeling support for a hospital project. Can we discuss next Tuesday afternoon?",
        "expected_schema": ["category", "confidence"],
        "expected_fields": {
            "category": "positive_interest",
        },
        "grounding_evidence": ["looking for additional Revit MEP modeling support", "discuss next Tuesday"],
    },
    {
        "fixture_id": "fix_reply_cls_002",
        "task": "reply_classification",
        "input_text": "I am not the right person for this. Please contact our Lead BIM Coordinator, Elena Conti, at e.conti@example.com.",
        "expected_schema": ["category", "confidence"],
        "expected_fields": {
            "category": "referral",
        },
        "grounding_evidence": ["Elena Conti", "e.conti@example.com"],
    },
    {
        "fixture_id": "fix_reply_cls_003",
        "task": "reply_classification",
        "input_text": "Please remove our address from your mailing list immediately. Unsubscribe.",
        "expected_schema": ["category", "confidence"],
        "expected_fields": {
            "category": "unsubscribe",
        },
        "grounding_evidence": ["remove our address", "Unsubscribe"],
    },

    # -------------------------------------------------------------
    # 5. Reply Suggestion
    # -------------------------------------------------------------
    {
        "fixture_id": "fix_sugg_001",
        "task": "reply_suggestion",
        "input_text": "Inbound from Technical Director: 'Sounds interesting. What is your turnaround time for Revit MEP modeling, and what team capacity do you currently have?'",
        "expected_schema": ["subject", "suggested_body"],
        "grounding_evidence": ["turnaround time", "team capacity", "Revit MEP modeling"],
        "forbidden_tokens": ["we have 5000 engineers", "free work forever"],
    },

    # -------------------------------------------------------------
    # 6. Meeting Time Extraction
    # -------------------------------------------------------------
    {
        "fixture_id": "fix_meet_001",
        "task": "meeting_time_extraction",
        "input_text": "Let's schedule a call on Thursday at 3:00 PM CET or Friday morning at 10:00 AM CET.",
        "expected_schema": ["extracted_slots"],
        "expected_fields": {
            "extracted_slots": ["Thursday 15:00 CET", "Friday 10:00 CET"],
        },
        "grounding_evidence": ["Thursday at 3:00 PM", "Friday morning at 10:00 AM"],
    },
    {
        "fixture_id": "fix_meet_002_deferred",
        "task": "meeting_time_extraction",
        "input_text": "I could do sometime early next week depending on when the contractor finishes their preliminary tender review, maybe Monday if they submit by Friday, otherwise Thursday late afternoon.",
        "expected_schema": ["extracted_slots"],
        "expected_fields": {
            "extracted_slots": ["conditional_pending_tender_review"],
        },
        "grounding_evidence": ["Monday if they submit by Friday", "Thursday late afternoon"],
        "is_semantic_nuance_deferred": True,
    },
]
