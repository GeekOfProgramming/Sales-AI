"""FastAPI Gateway for pyBIM-LLM.
Coordinates requests from Revit Addin (C# / pyRevit) with local ChromaDB RAG and Ollama LLM.
"""

import sys
import io
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


from fastapi import FastAPI, HTTPException, status, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest
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
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: initializes AI engine components and persistent ChromaDB on startup."""
    print("🚀 Initializing pyBIM-LLM AI services...")
    
    # 1. Initialize LLM Client
    app.state.llm_client = BIMLLMClient(default_model="qwen2.5-coder:1.5b")
    
    # 2. Initialize RAG Retriever
    project_root = Path(__file__).parent.parent
    persist_dir = str(project_root / "chroma_db")
    app.state.rag_retriever = BIMRAGRetriever(
        persist_dir=persist_dir,
        collection_name="bim_rules",
        embedding_model="nomic-embed-text",
    )
    
    # Ensure rules directory is indexed if collection is empty
    rules_dir = project_root / "data" / "rules"
    if rules_dir.exists() and app.state.rag_retriever.count() == 0:
        print(f"📥 Indexing initial rules from {rules_dir}...")
        count = app.state.rag_retriever.index_directory(rules_dir)
        print(f"✅ Indexed {count} semantic rule chunks.")
    
    print("🎉 pyBIM-LLM Gateway is ready to serve requests.")
    yield
    print("🛑 Shutting down pyBIM-LLM Gateway.")


app = FastAPI(
    title="pyBIM-LLM Gateway",
    description="Local, privacy-first AI Core & RAG Bridge for Autodesk Revit Automation.",
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


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    """Verify connectivity to local Ollama LLM and ChromaDB vector store."""
    llm_client: BIMLLMClient = app.state.llm_client
    rag_retriever: BIMRAGRetriever = app.state.rag_retriever

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
    llm_client: BIMLLMClient = app.state.llm_client
    rag_retriever: BIMRAGRetriever = app.state.rag_retriever

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
        success_msg = f"Successfully scraped & indexed {chunk_count} chunks into ChromaDB."
        ingest_queue.update_status(request_id, "approved", message=success_msg)
        print(f"✅ [Admin-Gate Ingest] Approved & Indexed {url} -> {file_path} ({chunk_count} chunks)")
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

    item = ingest_queue.enqueue(
        url=target_url,
        slug=request.slug,
        submitter=request.submitter or "Revit Client / Web User",
    )
    return IngestResponse(
        status="pending",
        message="Documentation request queued for administrator review. Use request_id to track approval status.",
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

@app.get(
    "/api/acc/hubs",
    response_model=List[ACCHubItem],
    summary="List accessible corporate Hubs in ACC / BIM 360",
    tags=["Cloud BIM (ACC)"],
)
async def list_acc_hubs():
    """Retrieve list of accessible Autodesk Construction Cloud hubs."""
    try:
        hubs = await cloud_client.get_hubs()
        return [ACCHubItem(**h) for h in hubs]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Autodesk Platform Services error: {str(e)}",
        )


@app.get(
    "/api/acc/projects/{hub_id}",
    response_model=List[ACCProjectItem],
    summary="List active projects inside an ACC Hub",
    tags=["Cloud BIM (ACC)"],
)
async def list_acc_projects(hub_id: str):
    """Retrieve list of active projects within a specified hub."""
    try:
        projects = await cloud_client.get_projects(hub_id=hub_id)
        return [ACCProjectItem(**p) for p in projects]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch projects for hub {hub_id}: {str(e)}",
        )


@app.get(
    "/api/acc/models/{project_id}",
    response_model=List[ACCModelItem],
    summary="List Revit models in an ACC Project",
    tags=["Cloud BIM (ACC)"],
)
async def list_acc_models(project_id: str):
    """Retrieve Revit models (.rvt) available in the cloud project without downloading."""
    try:
        models = await cloud_client.get_models(project_id=project_id)
        return [ACCModelItem(**m) for m in models]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch models for project {project_id}: {str(e)}",
        )


@app.post(
    "/api/acc/audit",
    response_model=CloudAuditResponse,
    summary="Execute Read-Only local AI compliance audit on Cloud Model",
    description="Fetches element metadata via Model Derivative API and audits against ISO 19650 and BIM rules.",
    tags=["Cloud BIM (ACC)"],
)
async def audit_cloud_model(req: CloudAuditRequest):
    """Read-only audit: extracts parameter trees from ACC and produces a compliance report without modifying the model."""
    try:
        # 1. Fetch metadata without opening Revit or downloading full RVT
        metadata = await cloud_client.get_model_metadata(req.urn)
        
        # 2. Local AI Audit against ISO 19650 rules
        report = cloud_auditor.audit_model_metadata(metadata)
        return CloudAuditResponse(**report)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cloud BIM audit failed: {str(e)}",
        )


@app.post(
    "/api/acc/apply-changes",
    summary="Human-in-the-Loop approval: commit reviewed parameter changes (Admin Only)",
    description="Authorizes controlled write-back of approved corrections. Requires human administrator confirmation.",
    tags=["Cloud BIM (ACC)"],
)
async def apply_cloud_model_changes(
    req: CloudSyncApprovalRequest,
    admin_user: dict = Depends(verify_admin_token),
):
    """Human-in-the-Loop gateway: ensures no AI modifications occur without explicit admin confirmation."""
    return {
        "status": "authorized",
        "urn": req.urn,
        "approved_elements_count": len(req.approved_elements),
        "approved_elements": req.approved_elements,
        "authorized_by": admin_user.get("sub", "admin"),
        "message": f"Human-in-the-loop approval recorded for {len(req.approved_elements)} element corrections. Ready for staged synchronization.",
    }



