"""Unit and Integration Tests for BIMCrewOrchestrator Multi-Agent System."""

import sys
import io
import asyncio

# Ensure UTF-8 output encoding for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from ai_engine.crew_orchestrator import BIMCrewOrchestrator


async def run_tests():
    print("=" * 65)
    print("🤖 CrewAI Multi-Agent Architecture Verification")
    print("=" * 65)

    orchestrator = BIMCrewOrchestrator(model_name="qwen2.5-coder:1.5b", max_feedback_cycles=2)

    # 1. Test Static Audit Logic directly
    print("\n🔹 Test 1: QA Reviewer Static Code Audit Checklist")
    flawed_code = "print('Hello Revit without transactions')"
    checklist_bad = orchestrator._static_code_audit(flawed_code, environment="pyrevit", language="python")
    print("Checklist on flawed code:", checklist_bad.dict())
    assert checklist_bad.passed_all is False
    assert checklist_bad.transaction_managed is False
    print("✅ Test 1.1 Passed: QA Reviewer correctly flagged missing transactions.")

    good_pyrevit_code = """
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import FilteredElementCollector, Transaction

t = Transaction(doc, 'Test Operation')
t.Start()
walls = FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Walls).ToElements()
t.Commit()
"""
    checklist_good = orchestrator._static_code_audit(good_pyrevit_code, environment="pyrevit", language="python")
    print("Checklist on compliant code:", checklist_good.dict())
    assert checklist_good.passed_all is True
    assert checklist_good.transaction_managed is True
    assert checklist_good.imports_valid is True
    print("✅ Test 1.2 Passed: QA Reviewer approved compliant pyRevit code.")

    # 2. Test Multi-Agent Pipeline for pyRevit Code Generation
    print("\n" + "-" * 50)
    print("🔹 Test 2: Multi-Agent Pipeline for pyRevit (Developer + QA Reviewer)")
    print("-" * 50)
    prompt = "Filter all doors in the active document and set FireRating to '1 Hour' inside a safe Transaction."
    result = await orchestrator.run(
        user_prompt=prompt,
        environment="pyrevit",
        language="python",
        temperature=0.1,
    )
    print(f"Cycles Run: {result.feedback_cycles}")
    print(f"QA Audit Passed: {result.audit_passed}")
    print(f"Checklist Status: {result.checklist}")
    print(f"Audit Notes: {result.audit_notes}")
    print(f"Execution Time: {result.execution_time_seconds}s")
    print("\nFinal Approved Code:\n", result.code)

    assert result.code and len(result.code) > 20
    assert result.feedback_cycles >= 1
    assert "Transaction" in result.code
    print("✅ Test 2 Passed: Multi-Agent pyRevit pipeline successfully executed and audited.")

    # 3. Test Multi-Agent Pipeline for Dynamo Environment
    print("\n" + "-" * 50)
    print("🔹 Test 3: Multi-Agent Pipeline for Dynamo Python")
    print("-" * 50)
    prompt_dyn = "Process elements from IN[0], start TransactionManager, set Comments parameter to 'Checked', and output to OUT."
    result_dyn = await orchestrator.run(
        user_prompt=prompt_dyn,
        environment="dynamo",
        language="python",
        temperature=0.1,
    )
    print(f"Dynamo Cycles Run: {result_dyn.feedback_cycles}")
    print(f"Dynamo QA Audit Passed: {result_dyn.audit_passed}")
    print(f"Dynamo Checklist: {result_dyn.checklist}")
    print("\nDynamo Final Code:\n", result_dyn.code)

    assert "OUT" in result_dyn.code or "TransactionManager" in result_dyn.code or "RevitServices" in result_dyn.code
    print("✅ Test 3 Passed: Multi-Agent Dynamo pipeline verified.")

    print("\n" + "=" * 65)
    print("🎉 ALL MULTI-AGENT ORCHESTRATOR TESTS PASSED!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(run_tests())
