"""FastAPI Gateway for pyBIM-LLM.
Coordinates requests from Revit Addin (C# / pyRevit) with local ChromaDB RAG and Ollama LLM.
"""

import sys
import io
import ast
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, List

# Ensure UTF-8 stdout encoding for Windows console
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest
from ai_engine.rag_retriever import BIMRAGRetriever
from ai_engine.data_ingestor import BIMDataIngestor
from backend.schemas import (
    ScriptGenerationRequest,
    ScriptGenerationResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
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

    # 3. Determine Execution Environment and Target Language
    target_env = getattr(request, "environment", "pyrevit")
    target_lang = getattr(request, "language", "python") or "python"

    if target_env == "dynamo" or target_lang.lower() == "dynamo":
        target_env = "dynamo"
        target_lang = "python"
    elif target_env == "csharp" or target_lang.lower() in ("csharp", "cs", "c#"):
        target_env = "csharp"
        target_lang = "csharp"
    else:
        target_env = "pyrevit"
        target_lang = "python"

    llm_req = CodeGenerationRequest(
        user_prompt=effective_prompt,
        environment=target_env,
        language=target_lang,
        context_rules=combined_context,
        model=request.model,
        temperature=request.temperature,
    )

    try:
        llm_resp = await llm_client.generate_code_async(llm_req)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Inference engine error: {str(e)}",
        )

    total_duration = round(time.perf_counter() - start_time, 3)
    validation_note = _validate_code(llm_resp.extracted_code, target_lang)

    return ScriptGenerationResponse(
        success=True,
        code=llm_resp.extracted_code,
        language=f"{target_lang} ({target_env})",
        model_used=llm_resp.model,
        retrieved_rules_count=len(rag_context_parts),
        retrieved_sources=retrieved_sources,
        execution_time_seconds=total_duration,
        validation_notes=validation_note,
    )


def _run_background_ingest(url: str, slug: Optional[str] = None):
    """Worker function executed in background thread to scrape and index documentation."""
    try:
        print(f"🕸️ [Background Ingest] Starting ingestion for: {url}")
        ingestor = BIMDataIngestor()
        result = ingestor.ingest_url(url, slug=slug)
        print(
            f"✅ [Background Ingest] Completed {url} -> "
            f"{result.get('file_path')} ({result.get('indexed_chunks')} chunks indexed)"
        )
    except Exception as e:
        print(f"❌ [Background Ingest] Error ingesting {url}: {e}")


@app.post(
    "/api/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest online documentation into knowledge base",
    description="Admin endpoint: Scrapes, sanitizes into Markdown, and injects knowledge into ChromaDB in background.",
    tags=["Admin / Knowledge Ingestion"],
)
async def ingest_documentation(request: IngestRequest, background_tasks: BackgroundTasks):
    """Queue an online documentation URL for scraping and vector store ingestion."""
    target_url = str(request.url).strip()
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL scheme. Only http:// and https:// URLs are supported.",
        )

    background_tasks.add_task(_run_background_ingest, target_url, request.slug)
    return IngestResponse(
        status="accepted",
        message=f"Ingestion started in background for: {target_url}",
        url=target_url,
    )

