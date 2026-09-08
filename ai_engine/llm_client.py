import os
import re
import time
from typing import AsyncGenerator, Generator, Optional, List, Dict, Any
from pydantic import BaseModel, Field
import ollama


class CodeGenerationRequest(BaseModel):
    """Data model representing a code generation prompt request."""
    user_prompt: str = Field(..., description="User's task or query describing what code to write.")
    environment: str = Field(default="pyrevit", description="Target execution environment: 'pyrevit', 'csharp', or 'dynamo'.")
    language: str = Field(default="python", description="Target programming language: 'python' (pyRevit) or 'csharp' (Revit API).")
    context_rules: Optional[str] = Field(default=None, description="BIM rules, ISO 19650 standards, or Revit API context from RAG.")
    model: Optional[str] = Field(default=None, description="Ollama model override.")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0, description="Sampling temperature. Lower is more deterministic.")
    num_ctx: int = Field(default=4096, description="Context window size in tokens.")


class CodeGenerationResponse(BaseModel):
    """Data model representing the LLM's response and parsed code."""
    raw_response: str = Field(..., description="Full text output produced by the LLM.")
    extracted_code: str = Field(..., description="Cleaned executable code extracted from markdown blocks.")
    model: str = Field(..., description="Name of the model used.")
    duration_seconds: float = Field(..., description="Time taken for inference in seconds.")


# Standardized System Prompts for BIM & Revit API code generation
SYSTEM_PROMPT_REVIT_PYTHON = """You are an expert BIM software engineer and Revit API specialist.
Your task is to generate clean, robust, and production-ready Python code for pyRevit.

Guidelines:
1. Wrap modifications inside an active Revit Transaction (`t = Transaction(doc, 'Action Name'); t.Start(); ... t.Commit()`).
2. Use `FilteredElementCollector(doc)` efficiently to query elements by category, class, or parameter.
3. Import standard Revit API namespaces (`Autodesk.Revit.DB`, `Autodesk.Revit.UI`) and pyRevit modules when needed.
4. If contextual rules or standards (e.g. ISO 19650) are provided, strictly adhere to them.
5. Provide ONLY valid executable Python code enclosed in a ```python ... ``` markdown block.
"""

SYSTEM_PROMPT_REVIT_CSHARP = """You are an expert BIM software engineer and Autodesk Revit API (.NET / C#) architect.
Your task is to generate clean, performant, and safe C# code implementing `IExternalCommand`.

Guidelines:
1. Always implement `IExternalCommand` with `[Transaction(TransactionMode.Manual)]`.
2. Wrap model changes inside a `using (Transaction tx = new Transaction(doc, "Operation")) { tx.Start(); ... tx.Commit(); }`.
3. Use `FilteredElementCollector` properly with `.WherePasses()` or category filters.
4. Include appropriate error handling and return `Result.Succeeded` or `Result.Failed`.
5. If contextual rules or standards (e.g. ISO 19650) are provided, strictly adhere to them.
6. Provide ONLY valid C# code enclosed in a ```csharp ... ``` markdown block.
"""

SYSTEM_PROMPT_REVIT_DYNAMO = """You are an expert BIM software engineer and Autodesk Dynamo Python scripting specialist.
Your task is to generate clean, robust, and production-ready Python code strictly designed for Dynamo Python Script Nodes in Revit.

Mandatory Execution Guidelines (Strict and Inviolable):
1. Import basic required libraries:
   import clr
   clr.AddReference('ProtoGeometry')
   from Autodesk.DesignScript.Geometry import *

   clr.AddReference('RevitNodes')
   import Revit
   clr.ImportExtensions(Revit.Elements)
   clr.ImportExtensions(Revit.GeometryConversion)

   clr.AddReference('RevitServices')
   import RevitServices
   from RevitServices.Persistence import DocumentManager
   from RevitServices.Transactions import TransactionManager

   clr.AddReference('RevitAPI')
   import Autodesk
   from Autodesk.Revit.DB import *

2. Document Initialization:
   Always access the active Revit document via:
   doc = DocumentManager.Instance.CurrentDBDocument

3. Transaction Management:
   Model modifications must be strictly managed via Dynamo's TransactionManager:
   TransactionManager.Instance.EnsureInTransaction(doc)
   # ... perform element or parameter modifications ...
   TransactionManager.Instance.TransactionTaskDone()

4. Input and Output Protocol:
   - Process all inputs using UnwrapElement(IN[x]) (e.g. elements = UnwrapElement(IN[0]) if isinstance(IN[0], list) else [UnwrapElement(IN[0])]).
   - Assign the final result exclusively to the variable OUT (e.g. OUT = result).

5. Provide ONLY valid executable Python code enclosed in a ```python ... ``` markdown block.
"""

SYSTEM_PROMPT_BIM_ADVISOR = """You are an expert BIM software consultant, Revit automation advisor, and ISO 19650 specialist.
Your task is to provide comprehensive, clear, and technically precise explanations, advisory answers, and conceptual guidance for Revit API, Dynamo, pyRevit, and BIM workflows.
Use clear headings, bullet points, and clean formatting.
"""



class BIMLLMClient:
    """Client for local AI inference using Ollama with specialization for BIM and Revit workflows."""

    def __init__(
        self,
        host: Optional[str] = None,
        default_model: str = "qwen2.5-coder:1.5b",
        timeout: float = 120.0,
    ):
        """Initialize Ollama sync and async clients.
        
        Args:
            host: Ollama server URL (defaults to OLLAMA_HOST env or 'http://127.0.0.1:11434').
            default_model: Model name to use for code generation.
            timeout: Request timeout in seconds.
        """
        self.host = host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        self.default_model = default_model
        self.timeout = timeout
        self._sync_client = ollama.Client(host=self.host, timeout=self.timeout)
        self._async_client = ollama.AsyncClient(host=self.host, timeout=self.timeout)

    def is_healthy(self) -> bool:
        """Check if the Ollama server is reachable and responsive."""
        try:
            self._sync_client.list()
            return True
        except Exception:
            return False

    def list_available_models(self) -> List[str]:
        """Return a list of installed models on the local Ollama instance."""
        try:
            res = self._sync_client.list()
            return [m.model for m in res.models]
        except Exception as e:
            raise RuntimeError(f"Failed to query Ollama models at {self.host}: {e}")

    def _build_messages(self, request: CodeGenerationRequest) -> List[Dict[str, str]]:
        """Construct system and user messages incorporating RAG context and rules."""
        # Select appropriate system prompt based on environment and language
        env = getattr(request, "environment", "").lower()
        lang = getattr(request, "language", "").lower()

        if env in ("text", "chat", "advisory", "nlp") or lang in ("text", "markdown", "nlp"):
            base_system = SYSTEM_PROMPT_BIM_ADVISOR
        elif env == "dynamo" or lang == "dynamo":
            base_system = SYSTEM_PROMPT_REVIT_DYNAMO
        elif env == "csharp" or lang in ("c#", "csharp", "cs"):
            base_system = SYSTEM_PROMPT_REVIT_CSHARP
        else:
            base_system = SYSTEM_PROMPT_REVIT_PYTHON


        # Inject RAG / BIM context if available
        if request.context_rules and request.context_rules.strip():
            system_prompt = f"{base_system}\n\n[BIM & ISO 19650 CONTEXT RULES]:\n{request.context_rules.strip()}"
        else:
            system_prompt = base_system

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]

    @staticmethod
    def extract_code(raw_text: str, language: str = "python") -> str:
        """Extract clean code from markdown code fences or return raw text if no fence found."""
        if language.lower() in ("text", "markdown", "nlp"):
            return raw_text.strip()

        patterns = [
            rf"```(?:{language}|py|cs|csharp)?\s*([\s\S]*?)```",
            r"```\s*([\s\S]*?)```",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return raw_text.strip()

    def generate_code(
        self,
        request: CodeGenerationRequest,
        model_name: Optional[str] = None,
    ) -> CodeGenerationResponse:
        """Synchronously generate code using Ollama with dynamically supplied model_name."""
        target_model = model_name or request.model or self.default_model
        messages = self._build_messages(request)
        start_time = time.perf_counter()

        response = self._sync_client.chat(
            model=target_model,
            messages=messages,
            options={
                "temperature": request.temperature,
                "num_ctx": request.num_ctx,
            },
        )
        duration = time.perf_counter() - start_time
        raw_content = response.message.content or ""
        extracted = self.extract_code(raw_content, language=request.language)

        return CodeGenerationResponse(
            raw_response=raw_content,
            extracted_code=extracted,
            model=target_model,
            duration_seconds=round(duration, 3),
        )

    async def generate_code_async(
        self,
        request: CodeGenerationRequest,
        model_name: Optional[str] = None,
    ) -> CodeGenerationResponse:
        """Asynchronously generate code using Ollama with dynamically supplied model_name."""
        target_model = model_name or request.model or self.default_model
        messages = self._build_messages(request)
        start_time = time.perf_counter()

        response = await self._async_client.chat(
            model=target_model,
            messages=messages,
            options={
                "temperature": request.temperature,
                "num_ctx": request.num_ctx,
            },
        )
        duration = time.perf_counter() - start_time
        raw_content = response.message.content or ""
        extracted = self.extract_code(raw_content, language=request.language)

        return CodeGenerationResponse(
            raw_response=raw_content,
            extracted_code=extracted,
            model=target_model,
            duration_seconds=round(duration, 3),
        )

    def stream_code_sync(
        self,
        request: CodeGenerationRequest,
        model_name: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """Stream generated tokens synchronously using dynamic model_name."""
        target_model = model_name or request.model or self.default_model
        messages = self._build_messages(request)

        stream = self._sync_client.chat(
            model=target_model,
            messages=messages,
            stream=True,
            options={
                "temperature": request.temperature,
                "num_ctx": request.num_ctx,
            },
        )
        for chunk in stream:
            if chunk.message and chunk.message.content:
                yield chunk.message.content

    async def stream_code_async(
        self,
        request: CodeGenerationRequest,
        model_name: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream generated tokens asynchronously using dynamic model_name."""
        target_model = model_name or request.model or self.default_model
        messages = self._build_messages(request)

        stream = await self._async_client.chat(
            model=target_model,
            messages=messages,
            stream=True,
            options={
                "temperature": request.temperature,
                "num_ctx": request.num_ctx,
            },
        )
        async for chunk in stream:
            if chunk.message and chunk.message.content:
                yield chunk.message.content
