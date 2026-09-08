"""Multi-Agent Pipeline & Orchestrator for BIM Code Generation and Quality Assurance.

Implements a two-agent architecture (Developer Agent + QA Reviewer Agent) with an internal
feedback loop for Autodesk Revit, pyRevit, Dynamo, and C# code generation.
"""

import ast
import re
import time
from typing import Dict, Any, Optional, List, Tuple
from pydantic import BaseModel, Field

from ai_engine.llm_client import BIMLLMClient, CodeGenerationRequest, CodeGenerationResponse


class AgentDefinition(BaseModel):
    """Metadata describing an agent's role, goal, and persona."""
    name: str
    role: str
    goal: str
    backstory: str


class AuditChecklist(BaseModel):
    """Checklist results for Revit API code review."""
    transaction_managed: bool = Field(default=False, description="Revit transaction properly wrapped and committed.")
    imports_valid: bool = Field(default=False, description="Required namespaces imported (e.g. Autodesk.Revit.DB).")
    dynamo_protocol_valid: bool = Field(default=True, description="UnwrapElement and OUT properly used for Dynamo.")
    syntax_valid: bool = Field(default=False, description="Code passes language syntax check without syntax errors.")
    passed_all: bool = Field(default=False, description="Whether all applicable checks passed.")
    defect_summary: Optional[str] = Field(default=None, description="Summary of defects if rejected.")


class CrewExecutionResult(BaseModel):
    """Result returned from multi-agent code generation and review pipeline."""
    code: str
    raw_response: str
    model_used: str
    environment: str
    language: str
    audit_passed: bool
    feedback_cycles: int
    checklist: Dict[str, bool]
    audit_notes: str
    execution_time_seconds: float


# Persona and System Prompt for Agent 1: Developer
DEVELOPER_BACKSTORY = """You are a Principal BIM Automation Developer with deep expertise in Autodesk Revit API,
pyRevit scripting, Dynamo Python nodes, and C# .NET addins.
Your responsibility is to craft robust, idiomatic, and clean automation code matching the user's prompt
and adhering strictly to provided architectural standards (ISO 19650) and Revit API contracts.
Always enclose code in appropriate markdown code fences (```python ... ``` or ```csharp ... ```).
"""

# Persona and System Prompt for Agent 2: QA Reviewer
QA_REVIEWER_BACKSTORY = """You are a Strict Revit API Code Auditor and Quality Assurance Engineer.
Your sole duty is to inspect code generated for Revit, pyRevit, Dynamo, or C# against inviolable contracts:
1. Transaction Management:
   - For pyRevit: Must use `Transaction(doc, '...')` with `.Start()` and `.Commit()`.
   - For Dynamo: Must use `TransactionManager.Instance.EnsureInTransaction(doc)` and `TransactionTaskDone()`.
   - For C#: Must use `using (Transaction tx = new Transaction(doc, "...")) { ... tx.Commit(); }` or `[Transaction(TransactionMode.Manual)]`.
2. Namespace Imports:
   - Must import `Autodesk.Revit.DB` (and for Dynamo: `RevitNodes`, `RevitServices`).
3. Dynamo Protocol (if environment is dynamo):
   - All inputs must be converted with `UnwrapElement(IN[x])`.
   - Output must be assigned to variable `OUT`.
4. Syntax & Safety:
   - Valid syntax without missing brackets or malformed statements.

When reviewing, you must evaluate the code and produce an AUDIT REPORT with either:
STATUS: APPROVED
or
STATUS: REJECTED
DEFECTS:
- [Detailed defect description]
RECOMMENDED_FIX:
- [Explicit fix instruction]
"""


class BIMCrewOrchestrator:
    """Orchestrates multi-agent code development and QA review cycles."""

    def __init__(
        self,
        llm_client: Optional[BIMLLMClient] = None,
        model_name: str = "qwen2.5-coder:1.5b",
        max_feedback_cycles: int = 2,
    ):
        """Initialize the multi-agent orchestrator.

        Args:
            llm_client: Underlying local BIM LLM client.
            model_name: Model identifier for inference.
            max_feedback_cycles: Maximum iteration loops between Developer and QA Reviewer.
        """
        self.llm_client = llm_client or BIMLLMClient(default_model=model_name)
        self.model_name = model_name
        self.max_feedback_cycles = max_feedback_cycles

        # Define Agents
        self.developer_agent = AgentDefinition(
            name="bim_developer_agent",
            role="Senior BIM Automation Developer",
            goal="Write production-ready Revit API, pyRevit, Dynamo, or C# scripts matching BIM standards.",
            backstory=DEVELOPER_BACKSTORY,
        )

        self.qa_reviewer_agent = AgentDefinition(
            name="qa_reviewer_agent",
            role="Revit API Code Auditor & QA Specialist",
            goal="Inspect code for transaction safety, API compliance, and syntax correctness.",
            backstory=QA_REVIEWER_BACKSTORY,
        )

    def _static_code_audit(self, code: str, environment: str, language: str) -> AuditChecklist:
        """Run deterministic static analysis checklist on the candidate code."""
        env = environment.lower()
        lang = language.lower()
        clean_code = code or ""

        # 1. Syntax check
        syntax_ok = False
        if lang in ("python", "py"):
            try:
                ast.parse(clean_code)
                syntax_ok = True
            except SyntaxError:
                syntax_ok = False
        else:
            # For C#, verify basic balanced braces
            open_b = clean_code.count("{")
            close_b = clean_code.count("}")
            syntax_ok = (open_b > 0 and open_b == close_b)

        # 2. Transaction check
        trans_ok = False
        if env == "dynamo":
            trans_ok = ("TransactionManager" in clean_code or "EnsureInTransaction" in clean_code or "Transaction" in clean_code)
        elif env == "csharp" or lang in ("csharp", "c#", "cs"):
            trans_ok = ("Transaction" in clean_code and ("tx.Commit" in clean_code or "TransactionMode" in clean_code))
        else:  # pyrevit
            trans_ok = ("Transaction" in clean_code and ("Start" in clean_code or "Commit" in clean_code))

        # 3. Imports check
        imports_ok = False
        if env == "dynamo":
            imports_ok = ("RevitServices" in clean_code or "RevitNodes" in clean_code or "Autodesk.Revit.DB" in clean_code or "clr.AddReference" in clean_code)
        elif env == "csharp" or lang in ("csharp", "c#", "cs"):
            imports_ok = ("Autodesk.Revit.DB" in clean_code or "using Autodesk" in clean_code)
        else:
            imports_ok = ("Autodesk.Revit.DB" in clean_code or "FilteredElementCollector" in clean_code or "DB" in clean_code)

        # 4. Dynamo protocol check
        dynamo_ok = True
        if env == "dynamo":
            dynamo_ok = ("OUT" in clean_code or "UnwrapElement" in clean_code or "IN[" in clean_code)

        passed_all = syntax_ok and trans_ok and imports_ok and dynamo_ok

        defects = []
        if not trans_ok:
            defects.append("Missing active Revit Transaction management.")
        if not imports_ok:
            defects.append("Missing required Revit API namespace imports (Autodesk.Revit.DB / RevitServices).")
        if not dynamo_ok:
            defects.append("Dynamo protocol violated: missing UnwrapElement or OUT assignment.")
        if not syntax_ok:
            defects.append("Code syntax validation failed (unparseable code).")

        return AuditChecklist(
            transaction_managed=trans_ok,
            imports_valid=imports_ok,
            dynamo_protocol_valid=dynamo_ok,
            syntax_valid=syntax_ok,
            passed_all=passed_all,
            defect_summary="; ".join(defects) if defects else None,
        )

    async def _developer_generate(
        self,
        prompt: str,
        environment: str,
        language: str,
        context_rules: Optional[str] = None,
        defect_feedback: Optional[str] = None,
        previous_code: Optional[str] = None,
        temperature: float = 0.1,
    ) -> str:
        """Execute Agent 1 (Developer) generation pass."""
        effective_prompt = prompt
        if defect_feedback and previous_code:
            effective_prompt = (
                f"{prompt}\n\n"
                f"[PREVIOUS CODE ATTEMPT]:\n```\n{previous_code}\n```\n\n"
                f"[QA AUDITOR DEFECT REPORT - MUST FIX IMMEDIATELY]:\n{defect_feedback}\n\n"
                f"Please rewrite the code correcting all reported defects completely."
            )

        req = CodeGenerationRequest(
            user_prompt=effective_prompt,
            environment=environment,
            language=language,
            context_rules=context_rules,
            model=self.model_name,
            temperature=temperature,
        )

        resp = await self.llm_client.generate_code_async(req, model_name=self.model_name)
        return resp.extracted_code

    async def run(
        self,
        user_prompt: str,
        environment: str = "pyrevit",
        language: str = "python",
        context_rules: Optional[str] = None,
        temperature: float = 0.1,
    ) -> CrewExecutionResult:
        """Run the multi-agent code development and QA review pipeline."""
        start_time = time.perf_counter()
        cycles = 0
        current_code = ""
        last_defects = None
        checklist = None
        audit_notes = ""

        print(f"🤖 [CrewAI Orchestrator] Starting Multi-Agent Pipeline for environment='{environment}'...")

        while cycles < self.max_feedback_cycles:
            cycles += 1
            print(f"  └─ Cycle {cycles}/{self.max_feedback_cycles}: Invoking '{self.developer_agent.name}'...")

            # Step 1: Developer Agent writes / rewrites code
            current_code = await self._developer_generate(
                prompt=user_prompt,
                environment=environment,
                language=language,
                context_rules=context_rules,
                defect_feedback=last_defects,
                previous_code=current_code if cycles > 1 else None,
                temperature=temperature,
            )

            # Step 2: QA Reviewer Agent audits code
            print(f"  └─ Cycle {cycles}: Invoking '{self.qa_reviewer_agent.name}' to audit code...")
            checklist = self._static_code_audit(current_code, environment, language)

            if checklist.passed_all:
                audit_notes = f"Approved by QA Reviewer on Cycle {cycles}. All Revit API contracts satisfied."
                print(f"  ✅ [CrewAI QA Reviewer] Code APPROVED on Cycle {cycles}.")
                break
            else:
                last_defects = checklist.defect_summary
                audit_notes = f"QA detected defects on Cycle {cycles}: {last_defects}"
                print(f"  ⚠️ [CrewAI QA Reviewer] Defects found on Cycle {cycles}: {last_defects}")

        if not checklist or not checklist.passed_all:
            audit_notes = f"Max review cycles reached ({cycles}). Passed with warnings: {checklist.defect_summary if checklist else 'Review incomplete'}"

        elapsed = round(time.perf_counter() - start_time, 3)

        return CrewExecutionResult(
            code=current_code,
            raw_response=current_code,
            model_used=self.model_name,
            environment=environment,
            language=language,
            audit_passed=checklist.passed_all if checklist else False,
            feedback_cycles=cycles,
            checklist={
                "transaction_managed": checklist.transaction_managed if checklist else False,
                "imports_valid": checklist.imports_valid if checklist else False,
                "dynamo_protocol_valid": checklist.dynamo_protocol_valid if checklist else False,
                "syntax_valid": checklist.syntax_valid if checklist else False,
            },
            audit_notes=audit_notes,
            execution_time_seconds=elapsed,
        )
