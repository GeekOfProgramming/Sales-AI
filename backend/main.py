"""FastAPI Gateway for pyBIM-LLM.
Coordinates requests from Revit Addin (C# / pyRevit) with local ChromaDB RAG and Ollama LLM.
"""

import os
import sys
import io
import json
import re
import ast
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, List

# Ensure UTF-8 stdout encoding for Windows console
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


from fastapi import FastAPI, HTTPException, Request, status, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest
from sales_engine.web.website_fetcher import WebsiteFetcher
from sales_engine.analysis.website_analyzer import WebsiteAnalyzer
from ai_engine.rag_retriever import BIMRAGRetriever
from ai_engine.data_ingestor import BIMDataIngestor
from ai_engine.crew_orchestrator import BIMCrewOrchestrator
from ai_engine.ingest_queue import ingest_queue
from ai_engine.acc_client import cloud_client
from ai_engine.cloud_auditor import cloud_auditor
from backend.auth import authenticate_admin, create_access_token, verify_admin_token
from backend.schemas import (
    ScriptGenerationRequest,
    ScriptGenerationResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    LoginRequest,
    TokenResponse,
    QueueItemResponse,
    ApprovalRequest,
    RejectionRequest,
    ACCHubItem,
    ACCProjectItem,
    ACCModelItem,
    CloudAuditRequest,
    CloudAuditResponse,
    CloudSyncApprovalRequest,
    ACCConfigRequest,
    ACCConfigStatusResponse,
    SalesLLMTestRequest,
    SalesLLMTestResponse,
    WebsiteAnalyzeRequest,
    WebsiteAnalyzeResponse,
    DiscoveryRequest,
    DiscoveryResponse,
    ExtractJobsRequest,
    ExtractJobsResponse,
    BuildLeadsRequest,
    BuildLeadsResponse,
    EnrichLeadsRequest,
    EnrichLeadsResponse
)
from sales_engine.outreach.schemas import GenerateDraftsRequest

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: initializes AI engine components and persistent ChromaDB on startup."""
    print("🚀 Initializing pyBIM-LLM AI services...")
    
    # 1. Initialize LLM Client
    app.state.llm_client = BIMLLMClient(default_model="qwen2.5-coder:1.5b")
    
    # 1. Initialize LLM Client
    app.state.llm_client = BIMLLMClient(default_model="qwen2.5-coder:1.5b")
    
    
    print("🎉 pyBIM-LLM Gateway is ready to serve requests.")
    yield
    print("🛑 Shutting down pyBIM-LLM Gateway.")


app = FastAPI(
    title="SalesAI Lead Intelligence API",
    description="SalesAI Backend with Local Ollama Integration",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for local Revit clients and development tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Phase 10.5: Local Sales Operator Console static assets & router
sales_static_path = Path(__file__).parent.parent / "static" / "sales"
if sales_static_path.exists():
    app.mount("/static/sales", StaticFiles(directory=str(sales_static_path)), name="sales_static")

from backend.sales_ui.router import router as sales_ui_router
from backend.sales_inbox_api import router as sales_inbox_api_router
from backend.sales_analytics_api import router as sales_analytics_api_router
app.include_router(sales_ui_router)
app.include_router(sales_inbox_api_router)
app.include_router(sales_analytics_api_router)


@app.get("/", tags=["UI"])
def root_endpoint():
    """Serve the Web UI Studio dashboard."""
    index_file = static_path / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"service": "pyBIM-LLM Gateway", "status": "operational"}


@app.get("/ui", tags=["UI"])
def ui_endpoint():
    """Dedicated endpoint for Web UI Studio."""
    return FileResponse(static_path / "index.html")


def get_llm_client() -> BIMLLMClient:
    """Safely get or lazy-initialize the LLM client."""
    if not hasattr(app.state, "llm_client") or app.state.llm_client is None:
        app.state.llm_client = BIMLLMClient(default_model="qwen2.5-coder:1.5b")
    return app.state.llm_client


def get_rag_retriever() -> BIMRAGRetriever:
    """Safely get or lazy-initialize the ChromaDB RAG retriever."""
    if not hasattr(app.state, "rag_retriever") or app.state.rag_retriever is None:
        project_root = Path(__file__).parent.parent
        persist_dir = str(project_root / "chroma_db")
        app.state.rag_retriever = BIMRAGRetriever(
            persist_dir=persist_dir,
            collection_name="bim_rules",
            embedding_model="nomic-embed-text",
        )
    return app.state.rag_retriever


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    """Verify connectivity to local Ollama LLM and ChromaDB vector store."""
    llm_client: BIMLLMClient = get_llm_client()
    rag_retriever: BIMRAGRetriever = get_rag_retriever()

    ollama_ok = llm_client.is_healthy()
    models = []
    if ollama_ok:
        try:
            models = llm_client.list_available_models()
        except Exception:
            ollama_ok = False

    try:
        rules_count = rag_retriever.count()
        chroma_ok = True
    except Exception:
        rules_count = 0
        chroma_ok = False

    if ollama_ok and chroma_ok:
        overall_status = "healthy"
    elif ollama_ok or chroma_ok:
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return HealthResponse(
        status=overall_status,
        ollama_connected=ollama_ok,
        chromadb_connected=chroma_ok,
        available_models=models,
        indexed_rules_count=rules_count,
        version="0.1.0",
    )


def _validate_code(code: str, language: str) -> Optional[str]:
    """Perform preliminary syntax validation on the extracted code."""
    if language.lower() in ("python", "py"):
        try:
            ast.parse(code)
            return "Python syntax validation passed."
        except SyntaxError as e:
            return f"Python syntax warning on line {e.lineno}: {e.msg}"
    elif language.lower() in ("csharp", "c#", "cs"):
        if "IExternalCommand" in code or "Transaction" in code:
            return "C# structural check passed (Revit API contracts present)."
        return "C# structure notice: Standard Revit API contracts not explicitly detected."
    return None


def intent_router(prompt: str) -> str:
    """Classify user prompt intent into 'text_generation' or 'code_generation'.

    Analyzes keywords and regex patterns to differentiate conceptual/explanatory
    inquiries from actionable script/code generation requests.
    """
    if not prompt or not prompt.strip():
        return "code_generation"

    clean = prompt.lower().strip()

    # Patterns indicating explanatory, descriptive, or conceptual queries
    text_patterns = [
        r"\b(explain|what is|how does|why|describe|summarize|tell me about|guide|difference between|compare|overview|concept|pros and cons)\b",
        r"(توضیح|چیست|چرا|چگونه|تفاوت|مقایسه|راهنما|خلاصه|مفهوم|مستندات|تعریف کن|به چه صورت|معرفی|منظور از)",
    ]

    # Patterns indicating code/script generation, modification, or automation
    code_patterns = [
        r"\b(write|generate|create|build|script|code|macro|plugin|addin|filter|collector|transaction|unwrapelement|parameter)\b",
        r"\b(def\s+|class\s+|import\s+|clr\.addreference)\b",
        r"(کد|اسکریپت|برنامه|پلاگین|ماکرو|المان|دیوار|تراکنش|پارامتر|تغییر بده|ایجاد کن|بساز|بنویس|فیلتر کن|حذف کن|محاسبه کن|اضافه کن|تنظیم کن)",
    ]

    is_text = any(re.search(pat, clean, re.IGNORECASE) for pat in text_patterns)
    is_code = any(re.search(pat, clean, re.IGNORECASE) for pat in code_patterns)

    # Pure text inquiry (unless user explicitly requests code writing)
    if is_text and not (
        "کد بنویس" in clean
        or "اسکریپت بنویس" in clean
        or "write code" in clean
        or "write a script" in clean
        or "generate script" in clean
        or "generate code" in clean
    ):
        return "text_generation"

    if is_code:
        return "code_generation"

    return "code_generation"


@app.post("/generate-script", response_model=ScriptGenerationResponse, tags=["AI Inference"])
async def generate_script(request: ScriptGenerationRequest):
    """Receive a prompt and optional selected Revit elements, retrieve rules via RAG, and generate code."""
    start_time = time.perf_counter()
    llm_client: BIMLLMClient = get_llm_client()
    rag_retriever: BIMRAGRetriever = get_rag_retriever()

    if not request.user_prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty.",
        )

    # 1. Format User Prompt with Selected Elements Metadata
    effective_prompt = request.user_prompt
    if request.selected_elements and len(request.selected_elements) > 0:
        elem_summaries = []
        for elem in request.selected_elements:
            elem_desc = f"ID: {elem.element_id} (Category: {elem.category}"
            if elem.name:
                elem_desc += f", Name: {elem.name}"
            if elem.family_name:
                elem_desc += f", Family: {elem.family_name}"
            if elem.parameters:
                elem_desc += f", Params: {elem.parameters}"
            elem_desc += ")"
            elem_summaries.append(elem_desc)

        effective_prompt += (
            f"\n\n[SELECTED REVIT ELEMENTS CONTEXT ({len(request.selected_elements)} elements)]:\n"
            + "\n".join(elem_summaries)
        )

    # 2. Retrieve Relevant Knowledge Rules via RAG
    retrieved_sources: List[str] = []
    rag_context_parts: List[str] = []

    if request.include_rag_rules:
        try:
            hits = rag_retriever.query(request.user_prompt, top_k=3)
            for hit in hits:
                src = hit["metadata"].get("source", "unknown")
                if src not in retrieved_sources:
                    retrieved_sources.append(src)
                header = hit["metadata"].get("header", "")
                rag_context_parts.append(f"[{src} - {header}]:\n{hit['content']}")
        except Exception as e:
            print(f"⚠️ RAG retrieval warning: {e}")

    # Append any custom rules provided directly in the request
    if request.custom_rules and request.custom_rules.strip():
        rag_context_parts.append(f"[Custom Client Rules]:\n{request.custom_rules.strip()}")

    combined_context = "\n\n".join(rag_context_parts) if rag_context_parts else None

    # 3. Semantic Routing: Intent Classification and Dynamic Model Assignment
    detected_intent = intent_router(request.user_prompt)

    available_models = []
    try:
        available_models = llm_client.list_available_models()
    except Exception:
        pass

    if request.model:
        selected_model = request.model
    elif detected_intent == "text_generation":
        # Check for local NLP model (e.g., llama3)
        llama_variant = next((m for m in available_models if "llama3" in m.lower()), None)
        if llama_variant:
            selected_model = llama_variant
        elif "llama3" in available_models:
            selected_model = "llama3"
        else:
            # Fallback to local coder model if llama3 is not yet pulled
            selected_model = "qwen2.5-coder:1.5b"
            print(f"ℹ️ Semantic Router: Intent is 'text_generation'. llama3 not installed locally; using {selected_model}")
    else:
        # Code generation default: qwen2.5-coder
        coder_variant = next((m for m in available_models if "qwen2.5-coder" in m.lower()), None)
        selected_model = coder_variant or "qwen2.5-coder:1.5b"

    # 4. Multi-Agent Execution (Code) vs Linear Direct Execution (Text)
    target_env = getattr(request, "environment", "pyrevit")
    target_lang = getattr(request, "language", "python") or "python"

    if detected_intent == "code_generation":
        # Determine language & environment contracts
        if target_env == "dynamo" or target_lang.lower() == "dynamo":
            target_env = "dynamo"
            target_lang = "python"
        elif target_env == "csharp" or target_lang.lower() in ("csharp", "cs", "c#"):
            target_env = "csharp"
            target_lang = "csharp"
        else:
            target_env = "pyrevit"
            target_lang = "python"

        try:
            orchestrator = BIMCrewOrchestrator(
                llm_client=llm_client,
                model_name=selected_model,
                max_feedback_cycles=2,
            )
            crew_result = await orchestrator.run(
                user_prompt=effective_prompt,
                environment=target_env,
                language=target_lang,
                context_rules=combined_context,
                temperature=request.temperature,
            )
            generated_code = crew_result.code
            model_used = crew_result.model_used
            qa_passed = crew_result.audit_passed
            qa_cycles = crew_result.feedback_cycles
            qa_checklist = crew_result.checklist
            auto_remediated = crew_result.auto_remediated
            validation_note = crew_result.audit_notes
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"CrewAI Multi-Agent orchestrator error: {str(e)}",
            )

    else:
        # Linear Direct NLP execution for text inquiries
        target_env = "text"
        target_lang = "markdown"

        llm_req = CodeGenerationRequest(
            user_prompt=effective_prompt,
            environment=target_env,
            language=target_lang,
            context_rules=combined_context,
            model=selected_model,
            temperature=request.temperature,
        )

        try:
            llm_resp = await llm_client.generate_code_async(llm_req, model_name=selected_model)
            generated_code = llm_resp.extracted_code
            model_used = llm_resp.model
            qa_passed = True
            qa_cycles = 1
            qa_checklist = None
            auto_remediated = False
            validation_note = "Direct NLP explanation generated without multi-agent overhead."
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Inference engine error: {str(e)}",
            )

    total_duration = round(time.perf_counter() - start_time, 3)

    return ScriptGenerationResponse(
        success=True,
        code=generated_code,
        language=f"{target_lang} ({target_env})",
        model_used=model_used,
        intent=detected_intent,
        qa_audit_passed=qa_passed,
        qa_feedback_cycles=qa_cycles,
        qa_checklist=qa_checklist,
        auto_remediated=auto_remediated,
        retrieved_rules_count=len(rag_context_parts),
        retrieved_sources=retrieved_sources,
        execution_time_seconds=total_duration,
        validation_notes=validation_note,
    )


def _run_approved_ingest(request_id: str, url: str, slug: Optional[str] = None):
    """Worker executed when administrator approves an item from the knowledge queue."""
    try:
        print(f"🕸️ [Admin-Gate Ingest] Processing approved URL: {url} (ID: {request_id})")
        ingest_queue.update_status(request_id, "processing", message="Scraping and vectorizing documentation...")
        ingestor = BIMDataIngestor()
        result = ingestor.ingest_url(url, slug=slug)
        chunk_count = result.get("indexed_chunks", 0)
        file_path = result.get("file_path", "")
        effective_slug = result.get("effective_slug", "")
        slug_feedback = result.get("slug_feedback")

        if slug_feedback:
            success_msg = f"Indexed as '{effective_slug}' ({chunk_count} chunks). 💡 Note: {slug_feedback}"
        else:
            success_msg = f"Successfully indexed as '{effective_slug}' ({chunk_count} chunks)."

        ingest_queue.update_status(request_id, "approved", message=success_msg, slug=effective_slug)
        print(f"✅ [Admin-Gate Ingest] Approved & Indexed {url} -> {file_path} ({chunk_count} chunks)")
        if slug_feedback:
            print(f"💡 [Admin-Gate Ingest] Naming note: {slug_feedback}")
    except Exception as e:
        err_msg = f"Ingestion error: {str(e)}"
        ingest_queue.update_status(request_id, "rejected", message=err_msg)
        print(f"❌ [Admin-Gate Ingest] Error ingesting {url}: {e}")


@app.post(
    "/api/auth/login",
    response_model=TokenResponse,
    summary="Admin Login to obtain JWT Bearer Token",
    description="Authenticates administrator to authorize Knowledge Queue approvals and vector DB changes.",
    tags=["Authentication"],
)
async def login_admin(credentials: LoginRequest):
    """Authenticate administrator and issue signed JWT bearer token."""
    if not authenticate_admin(credentials.username, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrative username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({"sub": credentials.username, "role": "admin"})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        role="admin",
        username=credentials.username,
        expires_in_minutes=1440,
    )


@app.post(
    "/api/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit documentation into Admin-Gate Review Queue",
    description="Stage 1: Enqueues URL for admin review to prevent Data Poisoning. Does not modify ChromaDB directly.",
    tags=["Knowledge Ingestion Queue"],
)
async def submit_ingest_request(request: IngestRequest):
    """Register an online documentation URL into the review queue."""
    target_url = str(request.url).strip()
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL scheme. Only http:// and https:// URLs are supported.",
        )

    initial_slug, slug_note = BIMDataIngestor.generate_smart_slug(target_url, user_slug=request.slug)
    item = ingest_queue.enqueue(
        url=target_url,
        slug=initial_slug,
        submitter=request.submitter or "Revit Client / Web User",
    )
    resp_msg = "Documentation request queued for administrator review. Use request_id to track approval status."
    if slug_note:
        resp_msg += f" 💡 Note: {slug_note}"

    return IngestResponse(
        status="pending",
        message=resp_msg,
        url=target_url,
        request_id=item["request_id"],
        current_state="pending",
    )


@app.get(
    "/api/ingest/queue",
    response_model=List[QueueItemResponse],
    summary="List all items in the Knowledge Ingestion Queue (Admin Only)",
    description="Retrieves pending, approved, or rejected knowledge submissions. Requires Bearer JWT token.",
    tags=["Knowledge Ingestion Queue"],
)
async def list_knowledge_queue(
    status_filter: Optional[str] = None,
    admin_user: dict = Depends(verify_admin_token),
):
    """Admin endpoint: Fetch pending or history queue items."""
    items = ingest_queue.list_items(status_filter=status_filter)
    return [QueueItemResponse(**item) for item in items]


@app.post(
    "/api/ingest/approve",
    summary="Approve pending request and trigger ChromaDB indexing (Admin Only)",
    description="Stage 2: Scrapes, sanitizes into Markdown, and injects into vector store in background.",
    tags=["Knowledge Ingestion Queue"],
)
async def approve_knowledge_request(
    request: ApprovalRequest,
    background_tasks: BackgroundTasks,
    admin_user: dict = Depends(verify_admin_token),
):
    """Admin endpoint: Approve queue item and launch scraper/vectorizer in background."""
    item = ingest_queue.get(request.request_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Queue item '{request.request_id}' not found.",
        )
    if item.get("status") == "approved":
        return {"status": "approved", "message": "Item is already approved and indexed.", "item": item}

    ingest_queue.update_status(request.request_id, "processing", message="Approved by admin. Processing...")
    background_tasks.add_task(_run_approved_ingest, request.request_id, item["url"], item.get("slug"))
    return {
        "status": "processing",
        "message": f"Request '{request.request_id}' approved. Background scraping and ChromaDB injection launched.",
        "request_id": request.request_id,
    }


@app.post(
    "/api/ingest/reject",
    summary="Reject pending request without vector store modification (Admin Only)",
    description="Blocks link from entering ChromaDB and archives request as rejected.",
    tags=["Knowledge Ingestion Queue"],
)
async def reject_knowledge_request(
    request: RejectionRequest,
    admin_user: dict = Depends(verify_admin_token),
):
    """Admin endpoint: Reject and archive unverified link."""
    item = ingest_queue.get(request.request_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Queue item '{request.request_id}' not found.",
        )
    updated = ingest_queue.update_status(
        request.request_id,
        "rejected",
        message=request.reason or "Rejected by administrator due to compliance standards.",
    )
    return {
        "status": "rejected",
        "message": f"Request '{request.request_id}' rejected. No data added to ChromaDB.",
        "item": updated,
    }


@app.post(
    "/api/ingest/queue/clear-history",
    summary="Purge processed items (approved/rejected) from knowledge queue history (Admin Only)",
    description="Removes historical approved and rejected records to keep the database and UI clean. Pending items are retained.",
    tags=["Knowledge Ingestion Queue"],
)
async def clear_queue_history(admin_user: dict = Depends(verify_admin_token)):
    """Admin endpoint: Clear approved/rejected historical logs."""
    removed_count = ingest_queue.clear_processed()
    return {
        "status": "success",
        "message": f"Purged {removed_count} historical records from queue.",
        "removed_count": removed_count,
    }


@app.get(
    "/api/ingest/status/{request_id}",
    response_model=QueueItemResponse,
    summary="Query submission status by tracking ID (Public)",
    description="Allows clients or users to track real-time workflow status (pending, processing, approved, rejected).",
    tags=["Knowledge Ingestion Queue"],
)
async def get_submission_status(request_id: str):
    """Public endpoint: Query real-time workflow status by tracking ID."""
    item = ingest_queue.get(request_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tracking ID '{request_id}' not found in knowledge queue.",
        )
    return QueueItemResponse(**item)


# --- Autodesk Construction Cloud (ACC) Endpoints ---

# @app.get(
#     "/api/acc/hubs",
#     response_model=List[ACCHubItem],
#     summary="List accessible corporate Hubs in ACC / BIM 360",
#     tags=["Cloud BIM (ACC)"],
# )
# async def list_acc_hubs():
#     """Retrieve list of accessible Autodesk Construction Cloud hubs."""
#     try:
#         hubs = await cloud_client.get_hubs()
#         return [ACCHubItem(**h) for h in hubs]
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_502_BAD_GATEWAY,
#             detail=f"Autodesk Platform Services error: {str(e)}",
#         )
# 
# 
# @app.get(
#     "/api/acc/projects/{hub_id}",
#     response_model=List[ACCProjectItem],
#     summary="List active projects inside an ACC Hub",
#     tags=["Cloud BIM (ACC)"],
# )
# async def list_acc_projects(hub_id: str):
#     """Retrieve list of active projects within a specified hub."""
#     try:
#         projects = await cloud_client.get_projects(hub_id=hub_id)
#         return [ACCProjectItem(**p) for p in projects]
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_502_BAD_GATEWAY,
#             detail=f"Failed to fetch projects for hub {hub_id}: {str(e)}",
#         )
# 
# 
# @app.get(
#     "/api/acc/models/{project_id}",
#     response_model=List[ACCModelItem],
#     summary="List Revit models in an ACC Project",
#     tags=["Cloud BIM (ACC)"],
# )
# async def list_acc_models(project_id: str):
#     """Retrieve Revit models (.rvt) available in the cloud project without downloading."""
#     try:
#         models = await cloud_client.get_models(project_id=project_id)
#         return [ACCModelItem(**m) for m in models]
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_502_BAD_GATEWAY,
#             detail=f"Failed to fetch models for project {project_id}: {str(e)}",
#         )
# 
# 
# @app.post(
#     "/api/acc/audit",
#     response_model=CloudAuditResponse,
#     summary="Execute Read-Only local AI compliance audit on Cloud Model",
#     description="Fetches element metadata via Model Derivative API and audits against ISO 19650 and BIM rules.",
#     tags=["Cloud BIM (ACC)"],
# )
# async def audit_cloud_model(req: CloudAuditRequest):
#     """Read-only audit: extracts parameter trees from ACC and produces a compliance report without modifying the model."""
#     try:
#         # 1. Fetch metadata without opening Revit or downloading full RVT
#         metadata = await cloud_client.get_model_metadata(req.urn)
#         
#         # 2. Local AI Audit against ISO 19650 rules
#         report = cloud_auditor.audit_model_metadata(metadata)
#         return CloudAuditResponse(**report)
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Cloud BIM audit failed: {str(e)}",
#         )
# 
# 
# @app.post(
#     "/api/acc/apply-changes",
#     summary="Human-in-the-Loop approval: commit reviewed parameter changes (Admin Only)",
#     description="Authorizes controlled write-back of approved corrections. Requires human administrator confirmation.",
#     tags=["Cloud BIM (ACC)"],
# )
# async def apply_cloud_model_changes(
#     req: CloudSyncApprovalRequest,
#     admin_user: dict = Depends(verify_admin_token),
# ):
#     """Human-in-the-Loop gateway: ensures no AI modifications occur without explicit admin confirmation."""
#     return {
#         "status": "authorized",
#         "urn": req.urn,
#         "approved_elements_count": len(req.approved_elements),
#         "approved_elements": req.approved_elements,
#         "authorized_by": admin_user.get("sub", "admin"),
#         "message": f"Human-in-the-loop approval recorded for {len(req.approved_elements)} element corrections. Ready for staged synchronization.",
#     }
# 
# 
# def update_env_credentials(client_id: Optional[str], client_secret: Optional[str], force_mock: bool = False):
#     """Safely updates or removes APS credentials in .env file."""
#     env_path = Path(__file__).resolve().parent.parent / ".env"
#     lines = []
#     if env_path.exists():
#         try:
#             lines = env_path.read_text(encoding="utf-8").splitlines()
#         except Exception:
#             lines = []
# 
#     new_lines = []
#     seen_id = False
#     seen_secret = False
# 
#     for line in lines:
#         stripped = line.strip()
#         if stripped.startswith("APS_CLIENT_ID="):
#             if not force_mock and client_id is not None:
#                 new_lines.append(f"APS_CLIENT_ID={client_id.strip()}")
#             seen_id = True
#         elif stripped.startswith("APS_CLIENT_SECRET="):
#             if not force_mock and client_secret is not None:
#                 new_lines.append(f"APS_CLIENT_SECRET={client_secret.strip()}")
#             seen_secret = True
#         else:
#             new_lines.append(line)
# 
#     if not force_mock and client_id is not None and not seen_id:
#         new_lines.append(f"APS_CLIENT_ID={client_id.strip()}")
#     if not force_mock and client_secret is not None and not seen_secret:
#         new_lines.append(f"APS_CLIENT_SECRET={client_secret.strip()}")
# 
#     env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
# 
#     if force_mock:
#         os.environ.pop("APS_CLIENT_ID", None)
#         os.environ.pop("APS_CLIENT_SECRET", None)
#     else:
#         if client_id is not None:
#             os.environ["APS_CLIENT_ID"] = client_id.strip()
#         if client_secret is not None:
#             os.environ["APS_CLIENT_SECRET"] = client_secret.strip()
# 
# 
# @app.get(
#     "/api/acc/config",
#     response_model=ACCConfigStatusResponse,
#     summary="Get Autodesk Construction Cloud configuration & connection status",
#     tags=["Cloud BIM (ACC)"],
# )
# async def get_acc_config():
#     """Returns current operational status (Live vs Simulation) and masked client ID."""
#     info = cloud_client.get_status_info()
#     return ACCConfigStatusResponse(**info)
# 
# 
# @app.post(
#     "/api/acc/config/test",
#     summary="Test Autodesk Platform Services credentials without saving (Admin Only)",
#     tags=["Cloud BIM (ACC)"],
# )
# async def test_acc_credentials(
#     req: ACCConfigRequest,
#     admin_user: dict = Depends(verify_admin_token),
# ):
#     """Verifies client ID and client secret against Autodesk's OAuth 2.0 au#     summary="Save Autodesk Platform Services cloud credentials (Admin Only)",
#     description="Updates .env persistence and reconfigures active cloud client in memory without restart.",
#     tags=["Cloud BIM (ACC)"],
# )
# async def configure_acc_credentials(
#     req: ACCConfigRequest,
#     admin_user: dict = Depends(verify_admin_token),
# ):
#     """Admin configuration: writes keys to .env and switches cloud_client dynamically between Live and Mock."""
#     if req.force_mock or (not req.client_id and not req.client_secret):
#         # Switch to Mock Mode
#         cloud_client.set_credentials("", "", force_mock=True)
#         update_env_credentials("", "", force_mock=True)
#         info = cloud_client.get_status_info()
#         info["message"] = "Switched to offline simulation / mock mode. Credentials cleared from active session."
#         return ACCConfigStatusResponse(**info)
# 
#     cid = (req.client_id or "").strip()
#     sec = (req.client_secret or "").strip()
# 
#     if not cid or not sec:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Both Client ID and Client Secret are required for live cloud connectivity.",
#         )
# 
#     # Unless explicitly bypassed, test connection with Autodesk servers
#     if not req.skip_verification:
#         test_res = await cloud_client.test_connection(client_id=cid, client_secret=sec)
#         if not test_res.get("success"):
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail=test_res.get("message", "Autodesk Platform Services credentials failed validation."),
#             )
# 
#     # Credentials valid: configure in-memory client and persist to .env
#     cloud_client.set_credentials(cid, sec, force_mock=False)
#     update_env_credentials(cid, sec, force_mock=False)
# 
#     info = cloud_client.get_status_info()
#     info["message"] = "Autodesk Platform Services credentials successfully saved and activated!"
#     return ACCConfigStatusResponse(**info)


@app.post("/api/sales/test-llm", tags=["SalesAI"])
async def test_sales_llm(request: SalesLLMTestRequest):
    prompt = f"""
You are a B2B sales research assistant.

Analyze the following business description:

{request.text}

Return a concise explanation of:
- what the business sells
- who its likely customers are
- what problems it solves
"""

    llm_client = get_llm_client()
    llm_req = CodeGenerationRequest(
        user_prompt=prompt,
        environment="sales",
        language="text"
    )

    try:
        response = await llm_client.generate_code_async(llm_req, model_name="llama3")
        return {
            "status": "ok",
            "model": "llama3",
            "response": response.extracted_code
        }
    except Exception as e:
        return {
            "status": "error",
            "model": "llama3",
            "response": str(e)
        }

@app.post("/api/sales/analyze-website", tags=["SalesAI"], response_model=WebsiteAnalyzeResponse)
async def analyze_website(request: WebsiteAnalyzeRequest):
    fetcher = WebsiteFetcher()
    analyzer = WebsiteAnalyzer()
    
    try:
        fetch_result = fetcher.fetch_website_content(str(request.url))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    try:
        profile = await analyzer.analyze_website(fetch_result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    return WebsiteAnalyzeResponse(
        status="ok",
        url=str(request.url),
        pages_analyzed=len(fetch_result.get("pages", [])),
        profile=profile
    )

@app.post("/api/sales/discover", tags=["SalesAI"], response_model=DiscoveryResponse)
async def discover_jobs(request: DiscoveryRequest):
    fetcher = WebsiteFetcher()
    analyzer = WebsiteAnalyzer()
    
    # Reuse Phase 2 Website Analyzer
    try:
        fetch_result = fetcher.fetch_website_content(str(request.website_url))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Website fetch failed: {e}")
        
    try:
        profile = await analyzer.analyze_website(fetch_result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Website analysis failed: {e}")
        
    # Phase 3: Discovery Orchestrator
    try:
        from sales_engine.discovery.discovery_orchestrator import DiscoveryOrchestrator
        orchestrator = DiscoveryOrchestrator()
        return await orchestrator.discover(
            website_url=str(request.website_url),
            profile=profile,
            countries=request.countries,
            max_queries=request.max_queries,
            results_per_query=request.results_per_query
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discovery failed: {e}")

@app.post("/api/sales/extract-jobs", tags=["SalesAI"], response_model=ExtractJobsResponse)
async def extract_jobs(request: ExtractJobsRequest):
    try:
        from sales_engine.sources.job_extraction_orchestrator import JobExtractionOrchestrator
        orchestrator = JobExtractionOrchestrator()
        
        url_strings = [str(url) for url in request.urls]
        return await orchestrator.extract_jobs(url_strings, profile=request.profile)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

@app.post("/api/sales/build-leads", tags=["SalesAI"], response_model=BuildLeadsResponse)
async def build_leads(request: BuildLeadsRequest):
    try:
        from sales_engine.leads.lead_orchestrator import process_leads
        return process_leads(request.jobs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lead building failed: {e}")

@app.post("/api/sales/enrich-leads", tags=["SalesAI"], response_model=EnrichLeadsResponse)
async def enrich_leads(request: EnrichLeadsRequest):
    try:
        from sales_engine.enrichment.enrichment_orchestrator import EnrichmentOrchestrator
        orchestrator = EnrichmentOrchestrator()
        return await orchestrator.enrich_leads(
            leads=request.leads,
            website_profile=request.website_profile,
            qualified_only=request.qualified_only,
            max_contacts_per_lead=request.max_contacts_per_lead,
            providers=request.providers
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lead enrichment failed: {e}")

@app.post("/api/sales/export", tags=["SalesAI"])
async def export_leads(request: dict):
    """
    Phase 7: Export human-reviewable Excel workbook, CSVs, CRM-ready JSON,
    and optional Google Sheets sync.
    """
    from sales_engine.exports.schemas import ExportRequest
    from sales_engine.exports.export_orchestrator import ExportOrchestrator
    try:
        req_obj = ExportRequest(**request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid export request schema: {e}")

    try:
        orchestrator = ExportOrchestrator()
        result = orchestrator.export(req_obj)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")


@app.post("/api/sales/generate-drafts", tags=["SalesAI"])
async def generate_email_drafts(request: GenerateDraftsRequest):
    """
    Phase 8: Generate grounded, personalized cold email drafts for qualified leads.
    """
    from sales_engine.outreach.outreach_orchestrator import OutreachOrchestrator
    try:
        llm = getattr(app.state, "llm_client", None)
        from sales_engine.outreach.email_generator import EmailGenerator
        gen = EmailGenerator(llm_client=llm)
        orchestrator = OutreachOrchestrator(email_generator=gen)
        result = orchestrator.generate_drafts(request, jobs=request.jobs)
        return result.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Draft generation failed: {e}")









# =========================================================================
# PHASE 9: HUMAN REVIEW, APPROVAL, SAFE EMAIL SENDING & AUDIT TRAIL
# =========================================================================

from sales_engine.sending import (
    ReviewStore,
    ApprovalService,
    SuppressionStore,
    SendOrchestrator,
    AuditService,
    DraftImportRequest,
    DraftImportResponse,
    DraftEditRequest,
    DraftApprovalRequest,
    DraftRejectRequest,
    DraftRequestChangesRequest,
    DraftSendRequest,
    BatchSendRequest,
    BatchSendResponse,
    SuppressionAddRequest,
)

# Global store & services instances for the FastAPI app
def get_sending_services():
    store = ReviewStore()
    approval_svc = ApprovalService(store)
    suppression_svc = SuppressionStore(store)
    orchestrator = SendOrchestrator(review_store=store, suppression_store=suppression_svc)
    audit_svc = AuditService(store)
    return store, approval_svc, suppression_svc, orchestrator, audit_svc


@app.post("/api/sales/drafts/import", response_model=DraftImportResponse, tags=["Sending"])
def import_drafts_endpoint(req: DraftImportRequest):
    """Import Phase 8 EmailDrafts into the human review queue."""
    _, approval_svc, _, _, _ = get_sending_services()
    imported = approval_svc.import_drafts(req.drafts)
    return DraftImportResponse(
        imported_count=len(imported),
        draft_ids=[d.draft_id for d in imported],
        drafts=imported,
    )


@app.get("/api/sales/drafts", tags=["Sending"])
def list_drafts_endpoint(
    approval_status: Optional[str] = None,
    send_status: Optional[str] = None,
    lead_id: Optional[str] = None,
    contact_id: Optional[str] = None,
    recipient_email: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List drafts in review queue with filtering."""
    store, _, _, _, _ = get_sending_services()
    filters = {}
    if approval_status:
        filters["approval_status"] = approval_status
    if send_status:
        filters["send_status"] = send_status
    if lead_id:
        filters["lead_id"] = lead_id
    if contact_id:
        filters["contact_id"] = contact_id
    if recipient_email:
        filters["recipient_email"] = recipient_email

    drafts, total = store.list_drafts(filters=filters, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "drafts": [d.model_dump() for d in drafts]}


@app.get("/api/sales/drafts/{draft_id}", tags=["Sending"])
def get_draft_endpoint(draft_id: str, revision: Optional[int] = None):
    """Retrieve a specific draft by ID and optional revision."""
    store, _, _, _, _ = get_sending_services()
    draft = store.get_draft(draft_id, revision)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")
    return draft.model_dump()


@app.post("/api/sales/drafts/{draft_id}/edit", tags=["Sending"])
def edit_draft_endpoint(draft_id: str, req: DraftEditRequest):
    """Human edit on draft content. Creates a new revision and invalidates approval."""
    _, approval_svc, _, _, _ = get_sending_services()
    try:
        new_draft = approval_svc.edit_draft(draft_id, req)
        return new_draft.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sales/drafts/{draft_id}/approve", tags=["Sending"])
def approve_draft_endpoint(draft_id: str, req: DraftApprovalRequest):
    """Explicit human approval. Computes and locks approval fingerprint. DOES NOT SEND."""
    _, approval_svc, _, _, _ = get_sending_services()
    try:
        approved = approval_svc.approve_draft(
            draft_id=draft_id,
            revision=req.revision,
            reviewer=req.reviewer,
            note=req.note,
        )
        return approved.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sales/drafts/{draft_id}/reject", tags=["Sending"])
def reject_draft_endpoint(draft_id: str, req: DraftRejectRequest):
    """Reject draft from sending."""
    _, approval_svc, _, _, _ = get_sending_services()
    try:
        rejected = approval_svc.reject_draft(
            draft_id=draft_id,
            revision=req.revision,
            reviewer=req.reviewer,
            note=req.note,
        )
        return rejected.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sales/drafts/{draft_id}/request-changes", tags=["Sending"])
def request_changes_endpoint(draft_id: str, req: DraftRequestChangesRequest):
    """Request changes on a draft revision."""
    _, approval_svc, _, _, _ = get_sending_services()
    try:
        updated = approval_svc.request_changes(
            draft_id=draft_id,
            revision=req.revision,
            reviewer=req.reviewer,
            note=req.note,
        )
        return updated.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sales/drafts/{draft_id}/send", tags=["Sending"])
def send_single_draft_endpoint(draft_id: str, req: DraftSendRequest):
    """Explicitly send an approved draft revision (dry-run by default)."""
    _, _, _, orchestrator, _ = get_sending_services()
    result = orchestrator.send_draft(
        draft_id=draft_id,
        revision=req.revision,
        dry_run=req.dry_run,
        reviewer=req.reviewer,
    )
    return result.model_dump()


@app.post("/api/sales/send-batch", response_model=BatchSendResponse, tags=["Sending"])
def send_batch_endpoint(req: BatchSendRequest):
    """Explicit batch send for listed draft IDs with partial failure isolation."""
    _, _, _, orchestrator, _ = get_sending_services()
    try:
        response = orchestrator.send_batch(
            draft_ids=req.draft_ids,
            dry_run=req.dry_run,
            reviewer=req.reviewer,
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/sales/suppression", tags=["Sending"])
def list_suppression_endpoint(limit: int = 100, offset: int = 0):
    """List entries on the do-not-contact suppression list."""
    _, _, suppression_svc, _, _ = get_sending_services()
    entries = suppression_svc.list_suppressed(limit=limit, offset=offset)
    return {"total": len(entries), "entries": [e.model_dump() for e in entries]}


@app.post("/api/sales/suppression", tags=["Sending"])
def add_suppression_endpoint(req: SuppressionAddRequest):
    """Add email address to suppression list."""
    _, _, suppression_svc, _, _ = get_sending_services()
    entry = suppression_svc.add_suppression(
        email=req.email,
        reason=req.reason,
        source=req.source,
        company_domain=req.company_domain,
    )
    return entry.model_dump()


@app.delete("/api/sales/suppression/{email}", tags=["Sending"])
def remove_suppression_endpoint(email: str):
    """Remove email from suppression list."""
    _, _, suppression_svc, _, _ = get_sending_services()
    success = suppression_svc.remove_suppression(email)
    if not success:
        raise HTTPException(status_code=404, detail="Email not found in suppression list")
    return {"status": "removed", "email": email}


@app.get("/api/sales/drafts/{draft_id}/events", tags=["Sending"])
def list_draft_events_endpoint(draft_id: str):
    """List immutable audit events for a draft."""
    _, _, _, _, audit_svc = get_sending_services()
    events = audit_svc.get_draft_events(draft_id)
    return {"draft_id": draft_id, "events": [e.model_dump() for e in events]}


# ---------------------------------------------------------------------------
# Phase 10 Discovery & Candidate Intelligence API Endpoints
# Strictly zero sends: none of these trigger outbound communications.
# ---------------------------------------------------------------------------
from pydantic import BaseModel as _PydanticBaseModel
from sales_engine.candidates.models import (
    CandidateCompany as _CandidateCompany,
    DiscoveryObservation as _DiscoveryObservation,
    MarketEvidence as _MarketEvidence,
)
from sales_engine.candidates.candidate_store import CandidateStore as _CandidateStore
from sales_engine.candidates.classifier import CompanyClassifier as _CompanyClassifier
from sales_engine.discovery_v2.source_registry import SourceRegistry as _SourceRegistry
from sales_engine.discovery_v2.discovery_engine import DiscoveryEngineV2 as _DiscoveryEngineV2
from sales_engine.discovery_v2.bridges import Phase10ConsoleBridge as _Phase10ConsoleBridge
from sales_engine.historical.importer import HistoricalImporter as _HistoricalImporter

_discovery_store_inst: Optional[_CandidateStore] = None
_discovery_registry_inst: Optional[_SourceRegistry] = None
_discovery_classifier_inst: Optional[_CompanyClassifier] = None
_discovery_engine_inst: Optional[_DiscoveryEngineV2] = None

def get_discovery_services():
    global _discovery_store_inst, _discovery_registry_inst, _discovery_classifier_inst, _discovery_engine_inst
    if _discovery_store_inst is None:
        db_path = os.getenv("SALESAI_DB_PATH", os.getenv("SALESAI_CANDIDATES_DB", "data/sales_outreach.db"))
        _discovery_store_inst = _CandidateStore(db_path=db_path)
    if _discovery_registry_inst is None:
        _discovery_registry_inst = _SourceRegistry()
    if _discovery_classifier_inst is None:
        from sales_engine.discovery_v2.bridges import Phase9AuthorityBridge
        authority_bridge = Phase9AuthorityBridge(db_path=_discovery_store_inst.db_path)
        _discovery_classifier_inst = _CompanyClassifier(suppression_store=authority_bridge)
    if _discovery_engine_inst is None:
        _discovery_engine_inst = _DiscoveryEngineV2(
            candidate_store=_discovery_store_inst,
            classifier=_discovery_classifier_inst,
            source_registry=_discovery_registry_inst,
        )
    return _discovery_store_inst, _discovery_registry_inst, _discovery_classifier_inst, _discovery_engine_inst


class DiscoveryScopeApiRequest(_PydanticBaseModel):
    scope_name: str
    discovery_modes: Optional[List[str]] = None
    markets: Optional[List[str]] = None
    source_ids: Optional[List[str]] = None
    relationship_types: Optional[List[str]] = None
    opportunity_types: Optional[List[str]] = None
    company_tiers: Optional[List[str]] = None
    bim_maturity: Optional[List[str]] = None
    max_results: int = 50
    company_name_keywords: Optional[List[str]] = None
    required_signals: Optional[List[str]] = None
    excluded_keywords: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    notes: Optional[str] = ""
    enabled: bool = True
    scope_id: Optional[str] = None


class DiscoveryScopeApiUpdateRequest(_PydanticBaseModel):
    scope_name: Optional[str] = None
    discovery_modes: Optional[List[str]] = None
    markets: Optional[List[str]] = None
    source_ids: Optional[List[str]] = None
    relationship_types: Optional[List[str]] = None
    opportunity_types: Optional[List[str]] = None
    company_tiers: Optional[List[str]] = None
    bim_maturity: Optional[List[str]] = None
    max_results: Optional[int] = None
    company_name_keywords: Optional[List[str]] = None
    required_signals: Optional[List[str]] = None
    excluded_keywords: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    notes: Optional[str] = None
    enabled: Optional[bool] = None


class DiscoveryRunApiRequest(_PydanticBaseModel):
    scope_id: Optional[str] = None
    sources: Optional[List[str]] = None
    query: str = "BIM"
    discovery_mode: str = "company_first"


class WatchlistAddApiRequest(_PydanticBaseModel):
    reason: str
    signals: Optional[List[str]] = None


class HistoricalImportApiRequest(_PydanticBaseModel):
    file_path: str
    sheet_name: Optional[str] = None


class SourceImportApiRequest(_PydanticBaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    sources: Optional[List[Dict[str, Any]]] = None
    api_secret: Optional[str] = None
    client_secret: Optional[str] = None
    password: Optional[str] = None
    extra_fields: Optional[Dict[str, Any]] = None
    model_config = {"extra": "allow"}


@app.get("/api/sales/discovery/scopes", tags=["Phase10-Discovery"])
def list_discovery_scopes_endpoint(enabled_only: bool = False):
    """List stored discovery search scopes."""
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore
    store, _, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    scopes = scope_store.list_scopes(enabled_only=enabled_only)
    return {"total": len(scopes), "scopes": [s.to_dict() for s in scopes]}


@app.post("/api/sales/discovery/scopes", tags=["Phase10-Discovery"])
def save_discovery_scope_endpoint(req: DiscoveryScopeApiRequest):
    """Create or update a discovery search scope."""
    from sales_engine.discovery_v2.scope import DiscoveryScope, DiscoveryScopeStore, validate_scope_payload
    store, registry, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    payload = req.model_dump()
    errors = validate_scope_payload(payload, registry=registry)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    scope = DiscoveryScope(
        scope_name=req.scope_name,
        discovery_modes=req.discovery_modes or ["company_first"],
        markets=req.markets or ["Italy"],
        source_ids=req.source_ids or [],
        relationship_types=req.relationship_types or ["service_buyer"],
        opportunity_types=req.opportunity_types or ["direct_bim_delivery"],
        company_tiers=req.company_tiers or ["tier1", "tier2", "tier3"],
        bim_maturity=req.bim_maturity or ["high", "medium", "low"],
        max_results=req.max_results,
        company_name_keywords=req.company_name_keywords or [],
        required_signals=req.required_signals or [],
        excluded_keywords=req.excluded_keywords or [],
        languages=req.languages or [],
        notes=req.notes or "",
        enabled=req.enabled,
        scope_id=req.scope_id or "",
    )
    saved = scope_store.save_scope(scope)
    return saved.to_dict()


@app.get("/api/sales/discovery/scopes/{scope_id}", tags=["Phase10-Discovery"])
def get_discovery_scope_endpoint(scope_id: str):
    """Get a discovery search scope by ID."""
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore
    store, _, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    scope = scope_store.get_scope(scope_id)
    if not scope:
        raise HTTPException(status_code=404, detail=f"Discovery scope {scope_id} not found")
    return scope.to_dict()


@app.post("/api/sales/discovery/scopes/{scope_id}/update", tags=["Phase10-Discovery"])
def update_discovery_scope_endpoint(scope_id: str, req: DiscoveryScopeApiUpdateRequest):
    """Update an existing discovery search scope preserving identity."""
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore, validate_scope_payload
    store, registry, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    existing = scope_store.get_scope(scope_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Discovery scope {scope_id} not found")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    merged = existing.to_dict()
    merged.update(updates)
    errors = validate_scope_payload(merged, registry=registry)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    updated = scope_store.update_scope(scope_id, updates)
    return updated.to_dict()


@app.post("/api/sales/discovery/scopes/{scope_id}/duplicate", tags=["Phase10-Discovery"])
def duplicate_discovery_scope_endpoint(scope_id: str):
    """Duplicate an existing search profile with a new ID."""
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore
    store, _, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    try:
        cloned = scope_store.duplicate_scope(scope_id)
        return cloned.to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Discovery scope {scope_id} not found")


@app.post("/api/sales/discovery/scopes/{scope_id}/enable", tags=["Phase10-Discovery"])
def enable_discovery_scope_endpoint(scope_id: str):
    """Enable a search profile."""
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore
    store, _, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    try:
        updated = scope_store.enable_scope(scope_id)
        return updated.to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Discovery scope {scope_id} not found")


@app.post("/api/sales/discovery/scopes/{scope_id}/disable", tags=["Phase10-Discovery"])
def disable_discovery_scope_endpoint(scope_id: str):
    """Disable a search profile."""
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore
    store, _, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    try:
        updated = scope_store.disable_scope(scope_id)
        return updated.to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Discovery scope {scope_id} not found")


@app.post("/api/sales/discovery/scopes/{scope_id}/preview", tags=["Phase10-Discovery"])
def preview_discovery_scope_endpoint(scope_id: str):
    """Preview the execution plan for a scope without performing any external calls."""
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore, preview_discovery_plan
    store, registry, _, _ = get_discovery_services()
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    scope = scope_store.get_scope(scope_id)
    if not scope:
        raise HTTPException(status_code=404, detail=f"Discovery scope {scope_id} not found")
    plan = preview_discovery_plan(scope, registry=registry)
    return plan


@app.post("/api/sales/discovery/run", tags=["Phase10-Discovery"])
def trigger_discovery_run_endpoint(req: DiscoveryRunApiRequest):
    """Trigger a discovery run with strict zero-send invariant. Supports scope_id execution."""
    store, registry, _, engine = get_discovery_services()
    if req.scope_id:
        from sales_engine.discovery_v2.scope import DiscoveryScopeStore, DiscoveryRunner
        scope_store = DiscoveryScopeStore(db_path=store.db_path)
        runner = DiscoveryRunner(
            scope_store=scope_store,
            candidate_store=store,
            source_registry=registry,
            engine=engine,
        )
        run_rec = runner.run(req.scope_id)
        return run_rec.to_dict()
    sources_to_run = req.sources or [s.id for s in registry.list_sources(enabled_only=True)]
    run_rec = engine.run_multi_source_discovery(
        sources=sources_to_run,
        query=req.query,
        discovery_mode=req.discovery_mode,
    )
    return run_rec.model_dump() if hasattr(run_rec, "model_dump") else run_rec.__dict__


@app.get("/api/sales/discovery/runs", tags=["Phase10-Discovery"])
def list_discovery_runs_endpoint(limit: int = 50):
    """List recent discovery run executions."""
    store, _, _, _ = get_discovery_services()
    from sales_engine.discovery_v2.scope import DiscoveryScopeStore
    scope_store = DiscoveryScopeStore(db_path=store.db_path)
    scope_runs = [r.to_dict() for r in scope_store.list_runs(limit=limit)]
    candidate_runs = store.list_discovery_runs(limit=limit)
    seen_ids = {sr["run_id"] for sr in scope_runs}
    all_runs = scope_runs + [r for r in candidate_runs if r.get("run_id") not in seen_ids]
    all_runs.sort(key=lambda x: str(x.get("started_at") or ""), reverse=True)
    return {"total": len(all_runs[:limit]), "runs": all_runs[:limit]}


@app.get("/api/sales/discovery/runs/{run_id}", tags=["Phase10-Discovery"])
def get_discovery_run_endpoint(run_id: str):
    """Get status and details of a specific discovery run."""
    store, _, _, _ = get_discovery_services()
    run = store.get_discovery_run(run_id)
    if not run:
        from sales_engine.discovery_v2.scope import DiscoveryScopeStore
        scope_store = DiscoveryScopeStore(db_path=store.db_path)
        scope_run = scope_store.get_run(run_id)
        if scope_run:
            return scope_run.to_dict()
        raise HTTPException(status_code=404, detail=f"Discovery run {run_id} not found")
    return run


@app.get("/api/sales/candidates", tags=["Phase10-Candidates"])
def list_candidates_endpoint(
    status: Optional[str] = None,
    tier: Optional[str] = None,
    country: Optional[str] = None,
    relationship_type: Optional[str] = None,
    limit: int = 100,
):
    """List candidate companies with optional filtering."""
    store, registry, _, _ = get_discovery_services()
    bridge = _Phase10ConsoleBridge(store=store, registry=registry)
    candidates = bridge.get_candidate_list_summary(
        status=status,
        relationship_type=relationship_type,
        tier=tier,
        limit=limit,
    )
    if country:
        candidates = [c for c in candidates if c.get("country", "").lower() == country.lower()]
    return {"total": len(candidates), "candidates": candidates[:limit]}


@app.get("/api/sales/candidates/{candidate_id}", tags=["Phase10-Candidates"])
def get_candidate_detail_endpoint(candidate_id: str):
    """Get detailed candidate profile, observations, evidence, and historical outreach."""
    store, registry, _, _ = get_discovery_services()
    bridge = _Phase10ConsoleBridge(store=store, registry=registry)
    detail = bridge.get_candidate_detail(candidate_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found")
    detail["historical_outreach"] = store.get_historical_outreach_for_candidate(candidate_id)
    detail["evidence"] = [e.model_dump() if hasattr(e, "model_dump") else e.__dict__ for e in store.get_evidence(candidate_id)]
    return detail


@app.post("/api/sales/candidates/{candidate_id}/qualify", tags=["Phase10-Candidates"])
def qualify_candidate_endpoint(candidate_id: str):
    """Run company qualification classification on candidate."""
    store, _, classifier, _ = get_discovery_services()
    cand = store.get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found")
    obs = store.get_observations_for_company(candidate_id)
    ev = store.get_evidence(candidate_id)
    qual = classifier.classify_company(cand, observations=obs, evidence=ev)
    store.save_candidate(cand)
    return qual.model_dump() if hasattr(qual, "model_dump") else qual.__dict__


@app.post("/api/sales/candidates/{candidate_id}/research", tags=["Phase10-Candidates"])
def research_candidate_endpoint(candidate_id: str):
    """Trigger deep research on candidate."""
    store, _, _, _ = get_discovery_services()
    cand = store.get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found")
    cand.last_researched_at = datetime.now(timezone.utc).isoformat()
    store.save_candidate(cand)
    return {
        "candidate_id": candidate_id,
        "company_name": cand.name,
        "status": "researched",
        "last_researched_at": cand.last_researched_at,
    }


@app.post("/api/sales/candidates/{candidate_id}/watch", tags=["Phase10-Candidates"])
def add_to_watchlist_endpoint(candidate_id: str, req: WatchlistAddApiRequest):
    """Add a candidate to the watchlist with monitoring signals."""
    store, _, _, _ = get_discovery_services()
    cand = store.get_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found")
    entry = store.add_to_watchlist(candidate_id, reason=req.reason, signals=req.signals)
    return entry.model_dump() if hasattr(entry, "model_dump") else entry.__dict__


@app.get("/api/sales/watchlist", tags=["Phase10-Candidates"])
def list_watchlist_endpoint():
    """List active watchlist candidates."""
    store, registry, _, _ = get_discovery_services()
    bridge = _Phase10ConsoleBridge(store=store, registry=registry)
    entries = bridge.get_watchlist_summary()
    return {"total": len(entries), "watchlist": entries}


@app.get("/api/sales/sources", tags=["Phase10-Discovery"])
def list_sources_endpoint(enabled_only: bool = False):
    """List all registered discovery sources with real-time health metrics and credential status."""
    _, registry, _, _ = get_discovery_services()
    sources = registry.list_sources(enabled_only=enabled_only)
    health = registry.get_health_summary()
    res = []
    for s in sources:
        h = health.get(s.id, health.get(s.name, {}))
        d = s.to_dict()
        d["health"] = h
        res.append(d)
    return {"total": len(res), "sources": res}


@app.get("/api/sales/sources/export", tags=["Phase10-Discovery"])
def export_sources_endpoint(format: str = "json"):
    """Export discovery source configurations with zero secrets."""
    from fastapi.responses import Response
    _, registry, _, _ = get_discovery_services()
    exported_str = registry.export_sources(format=format)
    media_type = "application/x-yaml" if format.lower() == "yaml" else "application/json"
    return Response(content=exported_str, media_type=media_type)


@app.get("/api/sales/sources/{source_id}", tags=["Phase10-Discovery"])
def get_source_endpoint(source_id: str):
    """Get single source configuration and health."""
    _, registry, _, _ = get_discovery_services()
    s = registry.get_source(source_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")
    health = registry.get_health_summary().get(s.id, {})
    d = s.to_dict()
    d["health"] = health
    return d


@app.post("/api/sales/sources", tags=["Phase10-Discovery"])
async def create_source_endpoint(request: Request):
    """Create a new discovery source with taxonomy and URL validation."""
    _, registry, _, _ = get_discovery_services()
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    payload_str = json.dumps(payload)
    forbidden_tokens = ["sk-", "Bearer ", "password", "api_secret", "client_secret"]
    for tok in forbidden_tokens:
        if tok in payload_str:
            raise HTTPException(status_code=400, detail=f"Source configuration payload must not contain secrets or credentials: '{tok}'")

    try:
        sc = registry.register_source(payload)
        return sc.to_dict()
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.post("/api/sales/sources/{source_id}/update", tags=["Phase10-Discovery"])
async def update_source_endpoint(source_id: str, request: Request):
    """Update an existing discovery source."""
    _, registry, _, _ = get_discovery_services()
    if not registry.get_source(source_id):
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    payload_str = json.dumps(payload)
    forbidden_tokens = ["sk-", "Bearer ", "password", "api_secret", "client_secret"]
    for tok in forbidden_tokens:
        if tok in payload_str:
            raise HTTPException(status_code=400, detail=f"Source configuration payload must not contain secrets or credentials: '{tok}'")

    try:
        sc = registry.update_source(source_id, payload)
        return sc.to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@app.post("/api/sales/sources/{source_id}/enable", tags=["Phase10-Discovery"])
def enable_source_endpoint(source_id: str):
    """Enable a discovery source."""
    _, registry, _, _ = get_discovery_services()
    try:
        sc = registry.enable_source(source_id)
        return {"status": "enabled", "source_id": source_id, "source": sc.to_dict()}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")


@app.post("/api/sales/sources/{source_id}/disable", tags=["Phase10-Discovery"])
def disable_source_endpoint(source_id: str):
    """Disable a discovery source."""
    _, registry, _, _ = get_discovery_services()
    try:
        sc = registry.disable_source(source_id)
        return {"status": "disabled", "source_id": source_id, "source": sc.to_dict()}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Source '{source_id}' not found")


@app.post("/api/sales/sources/{source_id}/test", tags=["Phase10-Discovery"])
def test_source_endpoint(source_id: str):
    """Safely test connection and availability of a discovery source."""
    _, registry, _, _ = get_discovery_services()
    res = registry.test_source(source_id)
    if res.get("status") == "NOT_AVAILABLE" and "not found" in res.get("error", "").lower():
        raise HTTPException(status_code=404, detail=res.get("error"))
    return res


@app.post("/api/sales/sources/import", tags=["Phase10-Discovery"])
async def import_source_config_endpoint(request: Request):
    """Import or reload source configuration with taxonomy and secrets validation."""
    _, registry, _, _ = get_discovery_services()
    try:
        payload = await request.json()
    except Exception:
        payload = None

    if not payload:
        registry._load_configs()
        return {"status": "reloaded", "total_sources": len(registry.sources)}

    # Check for secrets or token leakage in payload
    payload_str = json.dumps(payload)
    forbidden_tokens = ["sk-", "Bearer ", "password", "api_secret", "client_secret"]
    for tok in forbidden_tokens:
        if tok in payload_str:
            raise HTTPException(status_code=400, detail=f"Source configuration payload must not contain secrets or credentials: '{tok}'")

    sources_list = payload.get("sources", [payload] if "id" in payload or "name" in payload else [])
    registered = []
    for s_dict in sources_list:
        if isinstance(s_dict, dict) and ("id" in s_dict or "name" in s_dict):
            try:
                sc = registry.register_source(s_dict)
                registered.append(sc.id)
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=str(ve))

    return {"status": "imported", "registered_sources": registered, "total_sources": len(registry.sources)}


@app.post("/api/sales/history/import", tags=["Phase10-Historical"])
def import_historical_data_endpoint(req: HistoricalImportApiRequest):
    """Import historical spreadsheet (CSV/XLSX) with idempotency, root restriction, and safety guards."""
    store, _, _, _ = get_discovery_services()
    importer = _HistoricalImporter(store=store)

    # Path traversal protection: resolve path and verify within allowed roots
    raw_path_str = req.file_path.strip()
    if not raw_path_str:
        raise HTTPException(status_code=400, detail="file_path cannot be empty")

    base_dir = Path(__file__).resolve().parent.parent
    try:
        resolved_path = Path(raw_path_str).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Allowed roots: workspace base directory or data directory
    allowed_roots = [base_dir.resolve(), (base_dir / "data").resolve()]
    is_allowed = False
    for root in allowed_roots:
        try:
            resolved_path.relative_to(root)
            is_allowed = True
            break
        except ValueError:
            continue

    if not is_allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Access denied: file_path must reside within repository or data directory. Path traversal outside root is forbidden."
        )

    if not resolved_path.exists():
        raise HTTPException(status_code=404, detail=f"File {req.file_path} not found")

    if resolved_path.suffix.lower() == ".csv":
        records = importer.parse_csv(resolved_path)
    elif resolved_path.suffix.lower() in (".xlsx", ".xlsm"):
        records = importer.parse_xlsx(resolved_path)
    else:
        raise HTTPException(status_code=400, detail="Only CSV and XLSX files are supported")

    result = importer.import_records(records)
    return {
        "status": "completed",
        "file": resolved_path.name,
        "rows_read": result.total_rows_read,
        "records_parsed": result.records_parsed,
        "companies_created": result.companies_created,
        "companies_updated": result.companies_updated,
        "contacts_extracted": result.contacts_extracted,
        "duplicates_skipped": result.duplicates_skipped,
        "errors": result.errors,
    }

