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


class IngestResponse(BaseModel):
    """Acknowledgement response when background ingestion task is accepted."""
    status: str = Field(default="accepted", description="Status of the ingestion request.")
    message: str = Field(..., description="Status message detailing the background task.")
    url: str = Field(..., description="Target URL queued for background ingestion.")

