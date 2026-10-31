"""
sales_engine/llm_gateway/output_schemas.py
Versioned, typed schema registry for SalesAI LLM Gateway task outputs.
Strictly offline, fail-closed, zero network/IO.
Implements S2.5: explicitly named, versioned schemas across all task families
(treating Website Extraction Pass A and Pass B as independent schemas).

MAPPING TABLE (Existing Live Prompt / Models -> S2.5 Schemas):
+-------------------------------+----------------------------------+--------------------------------------+------------------------------------------+
| Task / Subtask                | Existing Live Source / Contract  | S2.5 Validated Schema Class          | Compatibility Notes                      |
+-------------------------------+----------------------------------+--------------------------------------+------------------------------------------+
| website_extraction:pass_a     | website_analyzer._pass_a_extract | WebsitePassAOutput                   | Strict typed lists & status enums;       |
|                               |                                  |                                      | optional evidence_refs.                  |
| website_extraction:pass_b     | website_analyzer._pass_b_derive  | WebsitePassBOutput                   | Bounded string lists; buyer roles        |
|                               |                                  |                                      | & primary job signals required non-empty.|
| job_analysis                  | backend.schemas.JobAnalysisResult| JobAnalysisOutput                    | Mirrors JobAnalysisResult; typed signals |
|                               | & job_analyzer.analyze_job       |                                      | with signal + evidence strings.          |
| discovery_query_generation    | discovery.query_generator        | DiscoveryQueryGenerationOutput       | Matches GeneratedQueriesResponse schema  |
|                               | & GeneratedQueriesResponse       |                                      | (queries: query, type, priority).        |
| inbox_classification          | inbox.classifier & ReplyCategory | InboxClassificationOutput            | Canonical ReplyCategory enum, confidence |
|                               |                                  |                                      | 0..1, opt-out override precedence.       |
| reply_suggestion              | inbox.models.ReplySuggestion     | ReplySuggestionOutput                | Subject, body, classification;           |
|                               |                                  |                                      | strict lack of sending authority.        |
| outreach_draft                | outreach.schemas.LLMEmailResponse| OutreachDraftOutput                  | Fully aligned to LLMEmailResponse;       |
|                               | & draft_validator                |                                      | separate DraftValidator business gate.   |
+-------------------------------+----------------------------------+--------------------------------------+------------------------------------------+
"""

from typing import Dict, List, Optional, Any, Type, Literal, Set, Union
from pydantic import BaseModel, Field, ConfigDict, field_validator

from sales_engine.llm_gateway.model_catalog import normalize_task_id, validate_subtask_id
from sales_engine.llm_gateway.errors import LLMInvalidSchemaError

SCHEMA_REGISTRY_VERSION = "1.0.0"


class StrictOutputBaseModel(BaseModel):
    """Base class for all S2.5 output schemas. Forbids extra fields and enforces strict types."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        arbitrary_types_allowed=False,
        strict=True,
    )


# ---------------------------------------------------------------------------
# 1. website_extraction:pass_a
# ---------------------------------------------------------------------------

class OfferingItem(StrictOutputBaseModel):
    """Individual product or service offering with lifecycle status."""

    name: str = Field(..., min_length=1, max_length=200, description="Product or service offering name")
    status: Literal["active", "in_development", "coming_soon", "cta"] = Field(
        default="active",
        description="Lifecycle status: active, in_development, coming_soon, or cta",
    )


class WebsitePassAOutput(StrictOutputBaseModel):
    """Schema for Website Extraction Pass A: extracted facts and evidence."""

    company_name: str = Field(..., min_length=1, max_length=250, description="Verified company name")
    company_summary: str = Field(..., min_length=1, max_length=3000, description="Factual company description")
    offerings: List[OfferingItem] = Field(
        default_factory=list,
        max_length=30,
        description="Key products or services offered",
    )
    target_industries: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Target industries explicitly served",
    )
    target_company_types: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Types of client companies served",
    )
    pain_points: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Key pain points or costs solved",
    )
    client_testimonial_roles: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Job titles of clients/customers mentioned",
    )
    internal_team_roles: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Job titles of company's internal staff/founders",
    )
    technologies: List[str] = Field(
        default_factory=list,
        max_length=30,
        description="Software, tools, APIs mentioned",
    )
    standards: List[str] = Field(
        default_factory=list,
        max_length=30,
        description="Compliance standards mentioned (e.g., ISO 19650)",
    )
    keywords: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Core domain keywords",
    )
    evidence_refs: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Source evidence references cited",
    )


# ---------------------------------------------------------------------------
# 2. website_extraction:pass_b
# ---------------------------------------------------------------------------

class WebsitePassBOutput(StrictOutputBaseModel):
    """Schema for Website Extraction Pass B: derived sales and hiring signals."""

    buyer_roles: List[str] = Field(
        ...,
        min_length=1,
        max_length=25,
        description="Decision-maker and budget-holder titles at prospective client firms",
    )
    primary_job_signals: List[str] = Field(
        ...,
        min_length=1,
        max_length=25,
        description="Hiring roles indicating demand for services",
    )
    secondary_job_signals: List[str] = Field(
        default_factory=list,
        max_length=30,
        description="Relevant technologies, platforms, tools, and technical standards",
    )
    negative_signals: List[str] = Field(
        default_factory=list,
        max_length=25,
        description="Disqualifying signals indicating unsuitable prospects",
    )
    evidence_refs: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Source evidence references cited",
    )


# ---------------------------------------------------------------------------
# 3. job_analysis
# ---------------------------------------------------------------------------

class JobSignalItem(StrictOutputBaseModel):
    """Extracted hiring or technology signal backed by excerpt evidence."""

    signal: str = Field(..., min_length=1, max_length=300, description="Hiring or technology signal description")
    evidence: str = Field(..., min_length=1, max_length=1000, description="Direct quote or concrete evidence from text")


class JobAnalysisOutput(StrictOutputBaseModel):
    """Schema for Job Description Analysis: technologies, seniority, requirements, and signals."""

    technologies: List[str] = Field(
        default_factory=list,
        max_length=30,
        description="Detected software and technology requirements",
    )
    requirements: List[str] = Field(
        default_factory=list,
        max_length=30,
        description="Key role requirements and qualifications",
    )
    seniority: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Assessed seniority level (e.g. entry, mid, senior, lead, executive)",
    )
    remote_status: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Remote work policy (e.g. remote, hybrid, on-site)",
    )
    relevant_signals: List[JobSignalItem] = Field(
        default_factory=list,
        max_length=20,
        description="Specific hiring signals backed by evidence",
    )
    evidence_refs: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Source evidence references cited",
    )


# ---------------------------------------------------------------------------
# 4. discovery_query_generation
# ---------------------------------------------------------------------------

class DiscoveryQueryItem(StrictOutputBaseModel):
    """Individual generated search query with category and priority."""

    query: str = Field(..., min_length=3, max_length=250, description="Search query string")
    type: Literal["general_job", "company_career", "ats_targeted", "keyword_signal"] = Field(
        ...,
        description="Query intent type",
    )
    priority: int = Field(..., ge=1, le=10, description="Execution priority from 1 to 10")


class DiscoveryQueryGenerationOutput(StrictOutputBaseModel):
    """Schema for Discovery Query Generation: bounded list of targeted search queries."""

    queries: List[DiscoveryQueryItem] = Field(
        ...,
        min_length=1,
        max_length=30,
        description="List of targeted discovery search queries",
    )


# ---------------------------------------------------------------------------
# 5. inbox_classification
# ---------------------------------------------------------------------------

CANONICAL_INBOX_CATEGORIES = (
    "positive_interest",
    "meeting_proposed",
    "referral",
    "question",
    "not_now",
    "not_interested",
    "unsubscribe",
    "bounce",
    "auto_reply",
    "unknown",
)

InboxCategoryLiteral = Literal[
    "positive_interest",
    "meeting_proposed",
    "referral",
    "question",
    "not_now",
    "not_interested",
    "unsubscribe",
    "bounce",
    "auto_reply",
    "unknown",
]


class InboxClassificationOutput(StrictOutputBaseModel):
    """Schema for Inbound Email Reply Classification."""

    category: InboxCategoryLiteral = Field(
        ...,
        description="Canonical classification category",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Classification confidence score between 0.0 and 1.0",
    )
    reasoning: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Concise rationale for classification",
    )
    suggested_next_action: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional operational next step recommendation (never auto-executed)",
    )


# ---------------------------------------------------------------------------
# 6. reply_suggestion
# ---------------------------------------------------------------------------

class ReplySuggestionOutput(StrictOutputBaseModel):
    """Schema for Operator Reply Suggestion (strictly proposed text; no sending authority)."""

    subject: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Proposed reply subject line",
    )
    body: str = Field(
        ...,
        min_length=5,
        max_length=3000,
        description="Proposed reply email body text",
    )
    classification: Optional[InboxCategoryLiteral] = Field(
        default=None,
        description="Optional context classification",
    )
    evidence_refs: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Evidence items referenced in the suggestion",
    )


# ---------------------------------------------------------------------------
# 7. outreach_draft
# ---------------------------------------------------------------------------

class OutreachDraftOutput(StrictOutputBaseModel):
    """Schema for Cold Outreach Email Draft (aligned to LLMEmailResponse)."""

    subject: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Cold email subject (3-8 words, <=60 chars)",
    )
    body: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Email body content (bounded word count, <=160 words)",
    )
    service_used: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of active pyBIM service pitched",
    )
    personalization_notes: Optional[str] = Field(
        default="",
        max_length=500,
        description="Explanation of why this trigger/angle was selected",
    )
    evidence_refs: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="Evidence item IDs referenced (e.g. JOB-001, EVID-001)",
    )
    cta: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Low-friction call to action",
    )


# ---------------------------------------------------------------------------
# Schema Registry
# ---------------------------------------------------------------------------

TASK_OUTPUT_SCHEMAS: Dict[str, Type[StrictOutputBaseModel]] = {
    "website_extraction:pass_a": WebsitePassAOutput,
    "website_extraction:pass_b": WebsitePassBOutput,
    "job_analysis": JobAnalysisOutput,
    "discovery_query_generation": DiscoveryQueryGenerationOutput,
    "inbox_classification": InboxClassificationOutput,
    "reply_suggestion": ReplySuggestionOutput,
    "outreach_draft": OutreachDraftOutput,
}


class SchemaRegistry:
    """Versioned static schema registry for SalesAI LLM Gateway task outputs."""

    VERSION = SCHEMA_REGISTRY_VERSION

    @classmethod
    def resolve_key(cls, task_id: str, subtask_id: Optional[str] = None) -> str:
        """Resolve canonical task and subtask into schema lookup key."""
        canonical_task = normalize_task_id(task_id)
        canonical_subtask = validate_subtask_id(canonical_task, subtask_id)

        if canonical_task == "website_extraction":
            if not canonical_subtask:
                raise ValueError("website_extraction requires subtask 'pass_a' or 'pass_b'.")
            return f"{canonical_task}:{canonical_subtask}"
        return canonical_task

    @classmethod
    def get_schema(cls, task_id: str, subtask_id: Optional[str] = None) -> Type[StrictOutputBaseModel]:
        """Lookup schema model class by task and subtask."""
        key = cls.resolve_key(task_id, subtask_id)
        schema = TASK_OUTPUT_SCHEMAS.get(key)
        if not schema:
            raise ValueError(f"No schema registered for task key '{key}'. Registered keys: {sorted(list(TASK_OUTPUT_SCHEMAS.keys()))}")
        return schema

    @classmethod
    def is_registered(cls, task_id: str, subtask_id: Optional[str] = None) -> bool:
        """Check if task/subtask has a registered output schema."""
        try:
            key = cls.resolve_key(task_id, subtask_id)
            return key in TASK_OUTPUT_SCHEMAS
        except (ValueError, KeyError):
            return False

    @classmethod
    def list_registered_keys(cls) -> List[str]:
        """Return sorted list of all registered schema keys."""
        return sorted(list(TASK_OUTPUT_SCHEMAS.keys()))

    @classmethod
    def get_cohere_schema(cls, task_id: str, subtask_id: Optional[str] = None) -> Dict[str, Any]:
        """Lookup schema model class and convert to a Cohere-compatible JSON schema."""
        schema_cls = cls.get_schema(task_id, subtask_id)
        return to_cohere_json_schema(schema_cls)


# ---------------------------------------------------------------------------
# Cohere Structured Output Schema Conversion (S3.5.4C)
# ---------------------------------------------------------------------------

COHERE_UNSUPPORTED_KEYWORDS: Set[str] = {
    "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "patternProperties",
    "dependencies", "propertyNames", "allOf", "oneOf", "not"
}


def sanitize_cohere_schema_node(node: Any, defs: Dict[str, Any]) -> Any:
    """Recursively resolve $ref definitions and strip Cohere-unsupported keywords."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref.startswith("#/$defs/"):
                def_name = ref.split("/")[-1]
                if def_name in defs:
                    resolved = sanitize_cohere_schema_node(defs[def_name], defs)
                    if "description" in node and "description" not in resolved:
                        resolved["description"] = node["description"]
                    return resolved
                raise LLMInvalidSchemaError(
                    f"Unresolvable $ref in schema: '{ref}'. Fail closed."
                )
            raise LLMInvalidSchemaError(
                f"Unsupported $ref format in schema: '{ref}'. Fail closed."
            )

        for kw in ("allOf", "oneOf", "not"):
            if kw in node:
                raise LLMInvalidSchemaError(
                    f"Unsupported schema composition keyword '{kw}' for Cohere structured outputs."
                )

        new_dict: Dict[str, Any] = {}
        for k, v in node.items():
            if k in COHERE_UNSUPPORTED_KEYWORDS:
                continue
            if k == "title":
                continue
            if k == "$defs":
                continue
            new_dict[k] = sanitize_cohere_schema_node(v, defs)
        return new_dict
    elif isinstance(node, list):
        return [sanitize_cohere_schema_node(item, defs) for item in node]
    return node


def to_cohere_json_schema(model_cls_or_dict: Union[Type[BaseModel], Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate or sanitize a Cohere-compatible JSON Schema from a Pydantic model or schema dictionary.
    - Strictly preserves expected field types, required fields, arrays, and nested objects.
    - Inlines all $defs/$ref definitions into self-contained schemas.
    - Strips unsupported validation keywords (minLength, maxLength, maxItems, minItems, etc.).
    - Explicitly verifies that evidence_refs is an array of strings when present.
    - Rejects unsupported schema constructs (allOf, oneOf, not) before dispatch.
    """
    if isinstance(model_cls_or_dict, dict):
        raw_schema = model_cls_or_dict
    elif isinstance(model_cls_or_dict, type) and issubclass(model_cls_or_dict, BaseModel):
        raw_schema = model_cls_or_dict.model_json_schema()
    else:
        raise LLMInvalidSchemaError(
            f"Expected BaseModel class or dict for Cohere schema, got {type(model_cls_or_dict).__name__}."
        )

    defs = raw_schema.get("$defs", {})
    sanitized = sanitize_cohere_schema_node(raw_schema, defs)

    if not isinstance(sanitized, dict) or sanitized.get("type") != "object":
        raise LLMInvalidSchemaError("Cohere JSON schema root must have type 'object'.")
    if "properties" not in sanitized or not isinstance(sanitized["properties"], dict):
        raise LLMInvalidSchemaError("Cohere JSON schema root must define 'properties'.")

    # Explicitly enforce evidence_refs as array of strings when present
    props = sanitized["properties"]
    if "evidence_refs" in props:
        ev_prop = props["evidence_refs"]
        if ev_prop.get("type") != "array" or ev_prop.get("items", {}).get("type") != "string":
            raise LLMInvalidSchemaError(
                "evidence_refs in Cohere schema must strictly be an array of strings."
            )

    return sanitized
