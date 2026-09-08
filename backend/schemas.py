"""Pydantic schemas and data contracts for pyBIM-LLM API Gateway.
Defines contracts between Revit Client (C# / pyRevit) and Python Backend.
"""

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field


class ElementMetadata(BaseModel):
    """Metadata representing an individual Revit element selected in the active model."""
    element_id: int = Field(..., description="Unique integer ID of the Revit element.")
    category: str = Field(..., description="Revit BuiltInCategory string (e.g. 'OST_Walls').")
    name: Optional[str] = Field(default=None, description="Element instance name.")
    family_name: Optional[str] = Field(default=None, description="Revit family name.")
    type_name: Optional[str] = Field(default=None, description="Revit type symbol name.")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Key-value dictionary of important parameters.")


class ScriptGenerationRequest(BaseModel):
    """Request payload sent by Revit Addin or API client to generate automation code."""
    user_prompt: str = Field(
        ...,
        description="Natural language instruction from the user (e.g. 'Filter selected walls and set FireRating to 2hr').",
    )
    environment: Literal["pyrevit", "csharp", "dynamo"] = Field(
        default="pyrevit",
        description="Target execution environment: 'pyrevit' (Python), 'csharp' (Native .NET), or 'dynamo' (Dynamo Python Script Node).",
    )
    language: Optional[Literal["python", "csharp", "cs", "dynamo"]] = Field(
        default="python",
        description="Target programming language: 'python' for pyRevit or 'csharp' for native C# IExternalCommand.",
    )
    selected_elements: Optional[List[ElementMetadata]] = Field(
        default=[],
        description="Optional list of elements currently selected in Revit UI.",
    )
    include_rag_rules: bool = Field(
        default=True,
        description="Whether to automatically search ChromaDB for relevant ISO 19650 and Revit API rules.",
    )
    custom_rules: Optional[str] = Field(
        default=None,
        description="Additional custom architectural or firm-specific rules to inject.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override LLM model name (defaults to server configured default: qwen2.5-coder:1.5b).",
    )
    temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Temperature for sampling. 0.0-0.2 is ideal for deterministic code generation.",
    )


# Alias for explicit specification compatibility
GenerateRequest = ScriptGenerationRequest



class ScriptGenerationResponse(BaseModel):
    """Response payload containing generated code, RAG audit trail, and performance metrics."""
    success: bool = Field(default=True, description="Indicates if code generation succeeded.")
    code: str = Field(..., description="Clean, executable code ready for Revit execution.")
    language: str = Field(..., description="Target language of the generated code.")
    model_used: str = Field(..., description="Name of the LLM model that performed inference.")
    intent: Optional[str] = Field(default="code_generation", description="Classified intent: 'code_generation' or 'text_generation'.")
    qa_audit_passed: Optional[bool] = Field(default=True, description="Whether QA Reviewer Agent passed all Revit API checklist rules.")
    qa_feedback_cycles: Optional[int] = Field(default=1, description="Number of feedback cycles between Developer and QA Reviewer agents.")
    qa_checklist: Optional[Dict[str, bool]] = Field(default=None, description="Detailed audit checklist status (transactions, imports, etc.).")
    auto_remediated: Optional[bool] = Field(default=False, description="Whether structural auto-remediation was applied against official standards.")
    retrieved_rules_count: int = Field(default=0, description="Number of knowledge chunks retrieved from RAG.")
    retrieved_sources: List[str] = Field(default=[], description="List of source rule files consulted by RAG.")
    execution_time_seconds: float = Field(..., description="Total time taken for RAG + inference in seconds.")
    validation_notes: Optional[str] = Field(default=None, description="Syntax and structure validation feedback.")
    error_message: Optional[str] = Field(default=None, description="Error details if generation failed.")


class HealthResponse(BaseModel):
    """System health check response verifying connectivity to Ollama and Vector DB."""
    status: Literal["healthy", "degraded", "unhealthy"] = Field(..., description="Overall service health state.")
    ollama_connected: bool = Field(..., description="Status of connection to local Ollama instance.")
    chromadb_connected: bool = Field(..., description="Status of ChromaDB vector store.")
    available_models: List[str] = Field(default=[], description="List of local LLM models ready for inference.")
    indexed_rules_count: int = Field(default=0, description="Number of active knowledge chunks in ChromaDB.")
    version: str = Field(default="0.1.0", description="API Gateway version.")


class IngestRequest(BaseModel):
    """Request payload for online documentation ingestion."""
    url: str = Field(..., description="Target documentation URL to scrape, clean, and inject into RAG.")
    slug: Optional[str] = Field(default=None, description="Optional custom filename/slug for the rule.")
    submitter: Optional[str] = Field(default="Revit Client / Web User", description="Submitter identifier or network host.")


class IngestResponse(BaseModel):
    """Acknowledgement response when background ingestion task is accepted into the review queue."""
    status: str = Field(default="pending", description="Status of the ingestion request (pending, approved, rejected).")
    message: str = Field(..., description="Status message detailing the queue registration.")
    url: str = Field(..., description="Target URL queued for review.")
    request_id: str = Field(..., description="Unique tracking identifier for querying submission status.")
    current_state: str = Field(default="pending", description="Current workflow state in Admin-Gate pipeline.")


class LoginRequest(BaseModel):
    """Authentication request payload for administrator access."""
    username: str = Field(..., description="Administrator username.")
    password: str = Field(..., description="Administrator password.")


class TokenResponse(BaseModel):
    """JWT Token bearer response for authenticated administrator sessions."""
    access_token: str = Field(..., description="Signed JWT Bearer access token.")
    token_type: str = Field(default="bearer", description="Token authorization type.")
    role: str = Field(default="admin", description="Assigned role.")
    username: str = Field(..., description="Authenticated username.")
    expires_in_minutes: int = Field(default=1440, description="Token validity window in minutes.")


class QueueItemResponse(BaseModel):
    """Item representation in the Admin-Gate Knowledge Queue."""
    request_id: str = Field(..., description="Tracking ID.")
    url: str = Field(..., description="Target documentation URL.")
    slug: Optional[str] = Field(default=None, description="Slug identifier.")
    status: str = Field(..., description="Current status: pending, processing, approved, or rejected.")
    submitter: str = Field(..., description="Submitting user or network host.")
    submitted_at: str = Field(..., description="ISO timestamp of submission.")
    processed_at: Optional[str] = Field(default=None, description="ISO timestamp of decision/processing.")
    message: Optional[str] = Field(default=None, description="Audit note or error details.")


class ApprovalRequest(BaseModel):
    """Payload to approve a pending knowledge request and trigger ChromaDB ingestion."""
    request_id: str = Field(..., description="Tracking ID of the pending queue item to approve.")


class RejectionRequest(BaseModel):
    """Payload to reject a pending knowledge request without vector database modification."""
    request_id: str = Field(..., description="Tracking ID of the pending queue item to reject.")
    reason: Optional[str] = Field(default="Rejected by administrator due to compliance standards.", description="Audit reason for rejection.")


# --- Autodesk Construction Cloud (ACC) Schemas ---

class ACCHubItem(BaseModel):
    """Hub representation in Autodesk Construction Cloud / BIM 360."""
    hub_id: str = Field(..., description="Unique APS Hub ID.")
    name: str = Field(..., description="Name of the corporate hub.")
    region: str = Field(default="US", description="Data residency region (US/EMEA).")
    extension_type: str = Field(default="Account", description="Hub account extension type.")


class ACCProjectItem(BaseModel):
    """Project representation inside an ACC Hub."""
    project_id: str = Field(..., description="Unique APS Project ID.")
    hub_id: str = Field(..., description="Parent Hub ID.")
    name: str = Field(..., description="Project name.")
    project_type: str = Field(default="ACC", description="Project type (ACC or BIM360).")
    status: str = Field(default="Active", description="Project state.")


class ACCModelItem(BaseModel):
    """Revit model item (.rvt) stored in Autodesk Construction Cloud."""
    model_id: str = Field(..., description="APS item or version ID.")
    project_id: str = Field(..., description="Parent project ID.")
    name: str = Field(..., description="Model filename (e.g. PRJ-ZZ-00-M3-A-0001.rvt).")
    version: int = Field(default=1, description="Version index of the model.")
    last_modified: str = Field(..., description="ISO timestamp of last modification.")
    file_size_mb: float = Field(..., description="Model size in Megabytes.")
    urn: str = Field(..., description="Base64 encoded Model Derivative URN.")


class CloudAuditRequest(BaseModel):
    """Request payload to initiate a read-only metadata audit on a cloud model."""
    urn: str = Field(..., description="Target model URN.")
    project_id: Optional[str] = Field(default=None, description="Parent project ID.")
    check_iso19650: bool = Field(default=True, description="Enforce ISO 19650 information container naming.")
    check_fire_rating: bool = Field(default=True, description="Verify mandatory fire protection parameters.")
    check_classifications: bool = Field(default=True, description="Verify OmniClass/UniFormat classifications.")


class CloudComplianceIssue(BaseModel):
    """Specific parameter or naming non-compliance issue discovered during audit."""
    element_id: Optional[int] = Field(default=None, description="Element integer ID, or null if container level.")
    category: str = Field(..., description="Revit Category or Container type.")
    parameter: str = Field(..., description="Parameter name or property key.")
    issue_type: str = Field(..., description="Classification of the issue.")
    current_value: str = Field(..., description="Current value found in cloud model.")
    proposed_value: str = Field(..., description="Proposed standardized value recommended by AI.")
    severity: Literal["HIGH", "MEDIUM", "LOW"] = Field(default="MEDIUM", description="Severity level.")
    description: str = Field(..., description="Detailed explanation of the non-compliance.")


class CloudAuditResponse(BaseModel):
    """Result of a Read-Only Cloud BIM Audit."""
    model_name: str = Field(..., description="Audited model filename.")
    urn: str = Field(..., description="Audited model URN.")
    total_elements_audited: int = Field(..., description="Count of elements evaluated.")
    total_checks_evaluated: int = Field(..., description="Number of validation checkpoints checked.")
    compliance_score: float = Field(..., description="Compliance percentage (0.0 to 100.0).")
    status: Literal["COMPLIANT", "NEEDS_REVIEW", "NON_COMPLIANT"] = Field(..., description="Health status.")
    audit_mode: Literal["READ_ONLY"] = Field(default="READ_ONLY", description="Guarantees non-destructive evaluation.")
    requires_human_approval: bool = Field(default=True, description="Enforces Human-in-the-Loop before write-back.")
    issues: List[CloudComplianceIssue] = Field(default=[], description="List of discovered issues.")
    audited_at: str = Field(..., description="ISO timestamp of audit execution.")
    summary_notes: str = Field(..., description="Executive audit summary.")


class CloudSyncApprovalRequest(BaseModel):
    """Payload to authorize controlled write-back of approved parameter corrections."""
    urn: str = Field(..., description="Model URN.")
    approved_elements: List[int] = Field(default=[], description="List of element IDs approved for synchronization.")
    reviewer_notes: Optional[str] = Field(default=None, description="Human reviewer justification.")


class ACCConfigRequest(BaseModel):
    """Request to update Autodesk Platform Services (APS) cloud credentials."""
    client_id: Optional[str] = Field(default=None, description="Autodesk Developer Client ID.")
    client_secret: Optional[str] = Field(default=None, description="Autodesk Developer Client Secret.")
    force_mock: bool = Field(default=False, description="Set True to revert back to Offline Simulation / Mock mode.")
    skip_verification: bool = Field(default=False, description="Save without verifying against Autodesk API.")


class ACCConfigStatusResponse(BaseModel):
    """Current connection and configuration status of Autodesk Construction Cloud."""
    status: Literal["CONNECTED_LIVE", "SIMULATED_MOCK"] = Field(..., description="Active operational mode.")
    mode_description: str = Field(..., description="Human-readable description of current state.")
    client_id_masked: Optional[str] = Field(default=None, description="Masked client ID (e.g. 'b89d...4f2a') for security.")
    is_live: bool = Field(..., description="Whether system is connected to live Autodesk servers.")
    message: str = Field(..., description="Status feedback message.")




