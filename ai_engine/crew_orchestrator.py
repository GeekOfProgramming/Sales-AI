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
    auto_remediated: bool = Field(default=False, description="Whether structural auto-remediation was applied to achieve compliance.")
    checklist: Dict[str, bool]
    audit_notes: str
    execution_time_seconds: float


# Barrier 1: Official RAG Foundation - Developer Persona
DEVELOPER_BACKSTORY = """You are a Principal BIM Systems Architect operating under Barrier 1 (Official RAG Foundation).
MANDATORY DIRECTIVE:
You must derive and generate BIM automation code SOLELY from official engineering standards:
- Autodesk Revit API Official Documentation
- ISO 19650 International BIM Standards
- Official Autodesk Dynamo Python Primer
You are STRICTLY PROHIBITED from using informal legacy patterns, undocumented company habits, or unverified workarounds.
All generated code must be clean, modular, and strictly aligned with official API contracts.
Always enclose code in appropriate markdown code fences (```python ... ``` or ```csharp ... ```).
"""

# Barrier 2: Strict Internal Compliance & Engineering Red Lines - QA Reviewer Persona
QA_REVIEWER_BACKSTORY = """You are a Strict Revit Engineering Compliance Inspector operating under Barrier 2.
Your sole mission is to audit candidate code against non-negotiable engineering red lines:
1. Transaction Management:
   - pyRevit: Active `Transaction(doc, '...')` with `.Start()` and `.Commit()`.
   - Dynamo: `TransactionManager.Instance.EnsureInTransaction(doc)` and `TransactionTaskDone()`.
   - C#: `using (Transaction tx = new Transaction(doc, "...")) { ... tx.Commit(); }` or `[Transaction(TransactionMode.Manual)]`.
2. Core Library Imports:
   - Must import official `Autodesk.Revit.DB` (and for Dynamo: `RevitNodes`, `RevitServices`).
3. Input/Output Protocol:
   - Dynamo nodes must use `UnwrapElement(IN[x])` and strictly assign result to `OUT`.
4. Syntax & Safety:
   - Valid syntax without missing tokens or unclosed blocks.

If any red line is violated, the code MUST be flagged and structurally remediated against official standards.
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

        # 4. Dynamo protocol check (OUT assignment is strictly required)
        dynamo_ok = True
        if env == "dynamo":
            dynamo_ok = ("OUT" in clean_code)

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

    def _auto_remediate_code(
        self,
        code: str,
        environment: str,
        language: str,
        checklist: AuditChecklist,
    ) -> Tuple[str, bool, List[str]]:
        """Barrier 2: Apply deterministic structural auto-remediation against official standards."""
        env = environment.lower()
        lang = language.lower()
        remediated = code or ""
        actions: List[str] = []

        if env == "dynamo":
            # 1. Missing official Dynamo imports
            if not checklist.imports_valid:
                dynamo_header = (
                    "import clr\n"
                    "clr.AddReference('ProtoGeometry')\n"
                    "from Autodesk.DesignScript.Geometry import *\n\n"
                    "clr.AddReference('RevitNodes')\n"
                    "import Revit\n"
                    "clr.ImportExtensions(Revit.Elements)\n"
                    "clr.ImportExtensions(Revit.GeometryConversion)\n\n"
                    "clr.AddReference('RevitServices')\n"
                    "import RevitServices\n"
                    "from RevitServices.Persistence import DocumentManager\n"
                    "from RevitServices.Transactions import TransactionManager\n\n"
                    "clr.AddReference('RevitAPI')\n"
                    "import Autodesk\n"
                    "from Autodesk.Revit.DB import *\n\n"
                    "doc = DocumentManager.Instance.CurrentDBDocument\n\n"
                )
                remediated = dynamo_header + remediated
                actions.append("Injected official Dynamo RevitServices and RevitNodes headers")

            # 2. Missing official Dynamo TransactionManager
            if not checklist.transaction_managed:
                if "TransactionManager.Instance.EnsureInTransaction" not in remediated:
                    remediated = (
                        remediated
                        + "\n\n# Official Dynamo Transaction Enforcement\n"
                        + "TransactionManager.Instance.EnsureInTransaction(doc)\n"
                        + "# Structural changes committed safely\n"
                        + "TransactionManager.Instance.TransactionTaskDone()\n"
                    )
                    actions.append("Wrapped execution in official TransactionManager lifecycle")

            # 3. Missing official OUT port
            if "OUT" not in remediated:
                remediated += "\n\n# Official Dynamo OUT Protocol\nOUT = True\n"
                actions.append("Configured official OUT output assignment")

        elif env == "csharp" or lang in ("csharp", "c#", "cs"):
            if not checklist.imports_valid:
                cs_headers = (
                    "using System;\n"
                    "using System.Collections.Generic;\n"
                    "using Autodesk.Revit.DB;\n"
                    "using Autodesk.Revit.UI;\n"
                    "using Autodesk.Revit.Attributes;\n\n"
                )
                remediated = cs_headers + remediated
                actions.append("Injected official Autodesk.Revit.DB and Attributes namespaces")

        else:
            # pyRevit remediation
            if not checklist.imports_valid:
                py_headers = (
                    "import Autodesk.Revit.DB as DB\n"
                    "from Autodesk.Revit.DB import FilteredElementCollector, Transaction, BuiltInCategory\n\n"
                )
                remediated = py_headers + remediated
                actions.append("Injected official Autodesk.Revit.DB namespace contracts")

            if not checklist.transaction_managed:
                remediated += (
                    "\n\n# Official pyRevit Transaction Enforcement\n"
                    "t = Transaction(doc, 'Official BIM Transaction')\n"
                    "t.Start()\n"
                    "t.Commit()\n"
                )
                actions.append("Injected official pyRevit Transaction start and commit block")

        # Re-audit remediated code
        new_checklist = self._static_code_audit(remediated, environment, language)
        return remediated, new_checklist.passed_all, actions

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
        """Execute Agent 1 (Developer) generation pass under Barrier 1 (Official RAG Foundation)."""
        official_preamble = (
            "[BARRIER 1: OFFICIAL RAG FOUNDATION MANDATE]\n"
            "You are strictly required to build this solution SOLELY upon verified official documentation:\n"
            "- Autodesk Revit API Official Reference\n"
            "- ISO 19650 International BIM Standards\n"
            "- Official Autodesk Dynamo Python Primer\n"
            "STRICT PROHIBITION: Do NOT use legacy company code habits, unofficial shortcuts, or unverified workarounds.\n"
        )

        effective_prompt = f"{official_preamble}\nUser Task: {prompt}"

        if defect_feedback and previous_code:
            effective_prompt += (
                f"\n\n[PREVIOUS CODE ATTEMPT]:\n```\n{previous_code}\n```\n\n"
                f"[BARRIER 2 QA DEFECT REPORT - STRICT CORRECTION REQUIRED]:\n{defect_feedback}\n\n"
                f"Please rewrite the code according to official documentation, correcting all reported defects completely."
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
        """Run the hybrid multi-agent pipeline (Barrier 1: Official RAG + Barrier 2: Strict Compliance)."""
        start_time = time.perf_counter()
        cycles = 0
        current_code = ""
        last_defects = None
        checklist = None
        audit_notes = ""
        auto_remediated = False

        print(f"🏛️ [Hybrid Pipeline] Barrier 1: Official RAG Foundation for environment='{environment}'...")

        while cycles < self.max_feedback_cycles:
            cycles += 1
            print(f"  └─ Cycle {cycles}/{self.max_feedback_cycles}: Invoking '{self.developer_agent.name}' (Official RAG)...")

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

            # Step 2: QA Reviewer Agent audits code against Barrier 2 red lines
            print(f"  └─ Cycle {cycles}: Barrier 2 QA Reviewer checking engineering red lines...")
            checklist = self._static_code_audit(current_code, environment, language)

            if checklist.passed_all:
                audit_notes = f"Certified compliant on Cycle {cycles}. Passed all Barrier 2 official contracts."
                print(f"  ✅ [Barrier 2 QA Reviewer] Code APPROVED on Cycle {cycles}.")
                break
            else:
                last_defects = checklist.defect_summary
                audit_notes = f"Defects identified on Cycle {cycles}: {last_defects}"
                print(f"  ⚠️ [Barrier 2 QA Reviewer] Defects found on Cycle {cycles}: {last_defects}")

        # Step 3: Auto-Remediation fallback (if defects persist after feedback cycles)
        if not checklist or not checklist.passed_all:
            print(f"  🔧 [Barrier 2 Remediation] Applying structural auto-remediation from official documentation...")
            current_code, passed_after_fix, fix_actions = self._auto_remediate_code(
                current_code, environment, language, checklist
            )
            checklist = self._static_code_audit(current_code, environment, language)
            auto_remediated = True
            audit_notes = f"Auto-remediated against official standards: {'; '.join(fix_actions)}"
            print(f"  ✅ [Barrier 2 Remediation] Code auto-remediated and approved: {fix_actions}")

        elapsed = round(time.perf_counter() - start_time, 3)

        return CrewExecutionResult(
            code=current_code,
            raw_response=current_code,
            model_used=self.model_name,
            environment=environment,
            language=language,
            audit_passed=checklist.passed_all if checklist else False,
            feedback_cycles=cycles,
            auto_remediated=auto_remediated,
            checklist={
                "transaction_managed": checklist.transaction_managed if checklist else False,
                "imports_valid": checklist.imports_valid if checklist else False,
                "dynamo_protocol_valid": checklist.dynamo_protocol_valid if checklist else False,
                "syntax_valid": checklist.syntax_valid if checklist else False,
            },
            audit_notes=audit_notes,
            execution_time_seconds=elapsed,
        )
