"""
tests/test_backend_schema_contract.py
Isolated Pydantic schema, JSON-schema, public-export, and dead-symbol removal tests
for `backend/schemas.py` and `backend/__init__.py`.

Tests Pydantic JSON schema generation (`model_json_schema()`) and field validation
without starting the FastAPI application or opening SQLite connections.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

from pydantic import ValidationError
import pytest

import backend
from backend import schemas
from tests.support.isolated_subprocess import run_isolated_subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent

REMOVED_S1_SYMBOLS = (
    "GenerateRequest",
    "ACCHubItem",
    "ACCProjectItem",
    "ACCModelItem",
    "CloudAuditRequest",
    "CloudComplianceIssue",
    "CloudAuditResponse",
    "CloudSyncApprovalRequest",
    "ACCConfigRequest",
    "ACCConfigStatusResponse",
)


def test_removed_s1_acc_and_alias_symbols_absent_everywhere() -> None:
    """Confirm removed S1 schemas do not exist on `backend.schemas` and have no tracked Python refs."""
    for name in REMOVED_S1_SYMBOLS:
        assert not hasattr(schemas, name), f"Obsolete S1 symbol {name} still exists on backend.schemas"

    scan_roots = ("backend", "sales_engine", "scripts", "tests")
    for root in scan_roots:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name in REMOVED_S1_SYMBOLS:
                if name in text:
                    tree = ast.parse(text, filename=str(path))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Name) and node.id == name:
                            raise AssertionError(f"Obsolete reference to {name} in {path}:{node.lineno}")
                        if isinstance(node, ast.ImportFrom) and any(a.name == name for a in node.names):
                            raise AssertionError(f"Obsolete import of {name} in {path}:{node.lineno}")


def test_public_exports_and_s2_revit_schemas_preserved() -> None:
    """Verify `backend.__init__` lazy exports and S2 legacy public schemas remain intact."""
    assert set(backend.__all__) == {
        "ElementMetadata",
        "ScriptGenerationRequest",
        "ScriptGenerationResponse",
        "HealthResponse",
    }
    elem = backend.ElementMetadata(element_id=101, category="OST_Walls")
    req = backend.ScriptGenerationRequest(
        user_prompt="List walls",
        selected_elements=[elem],
    )
    assert req.environment == "pyrevit"
    assert req.language == "python"
    assert req.include_rag_rules is True
    assert req.temperature == 0.1
    assert req.selected_elements and req.selected_elements[0].category == "OST_Walls"

    resp = backend.ScriptGenerationResponse(
        code="print('ok')",
        language="python",
        model_used="qwen2.5-coder:1.5b",
        execution_time_seconds=0.25,
    )
    assert resp.success is True
    assert "properties" in backend.ScriptGenerationRequest.model_json_schema()


def test_health_auth_and_ingestion_schemas_contract() -> None:
    """Verify HealthResponse, Auth, and active Ingestion schemas preserve defaults, Literals, and JSON schemas."""
    health = schemas.HealthResponse(
        status="healthy",
        ollama_connected=True,
        chromadb_connected=False,
    )
    assert health.available_models == []
    assert health.indexed_rules_count == 0
    assert health.version == "0.1.0"
    assert json.loads(health.model_dump_json())["status"] == "healthy"
    assert "ollama_connected" in schemas.HealthResponse.model_json_schema()["properties"]

    with pytest.raises(ValidationError):
        schemas.HealthResponse(status="invalid_status", ollama_connected=True, chromadb_connected=True)

    ingest_req = schemas.IngestRequest(url="https://docs.example.com/bim")
    assert ingest_req.slug is None
    assert ingest_req.submitter == "Revit Client / Web User"

    token_resp = schemas.TokenResponse(access_token="tok", username="admin")
    assert token_resp.token_type == "bearer"
    assert token_resp.role == "admin"
    assert token_resp.expires_in_minutes == 1440

    rej = schemas.RejectionRequest(request_id="req_1")
    assert rej.reason is not None and "compliance" in rej.reason


def test_sales_discovery_analysis_enrichment_and_lead_schemas_contract() -> None:
    """Verify active Sales-AI request/response schemas, bounds, defaults, and validators."""
    # URL & bounds validation
    with pytest.raises(ValidationError):
        schemas.WebsiteAnalyzeRequest(url="not-a-url")
    w_req = schemas.WebsiteAnalyzeRequest(url="https://acme-bim.example")
    assert str(w_req.url).startswith("https://acme-bim.example")

    with pytest.raises(ValidationError):
        schemas.DiscoveryRequest(website_url="https://acme-bim.example", max_queries=25)
    with pytest.raises(ValidationError):
        schemas.DiscoveryRequest(website_url="https://acme-bim.example", results_per_query=11)

    with pytest.raises(ValidationError):
        schemas.GeneratedQuery(query="test", type="invalid_type", priority=5)
    with pytest.raises(ValidationError):
        schemas.GeneratedQuery(query="test", type="general_job", priority=11)

    # StructuredJob validator `populate_tech_stack` & property
    job = schemas.StructuredJob(
        company_name="Acme BIM",
        tech_stack=["Revit", "Python"],
        relevant_signals=[schemas.JobSignal(signal="Hiring BIM", evidence="Revit API role")],
    )
    assert job.technologies == ["Revit", "Python"]
    assert job.tech_stack == ["Revit", "Python"]
    assert job.source == "generic"

    # ExtractJobsRequest max_length=50
    with pytest.raises(ValidationError):
        schemas.ExtractJobsRequest(urls=[f"https://example.com/job/{i}" for i in range(51)])

    # CompanyLead properties & defaults
    lead = schemas.CompanyLead(
        company_name="Acme BIM",
        source_company_keys=["domain:acme.example"],
        source_company_identities=["acme.example"],
        lead_score=75,
        qualified=True,
    )
    assert lead.source_company_key == "domain:acme.example"
    assert lead.source_company_identity == "acme.example"
    assert lead.qualification_threshold == 60

    # EnrichLeadsRequest bounds & defaults
    enrich_req = schemas.EnrichLeadsRequest(leads=[lead])
    assert enrich_req.qualified_only is True
    assert enrich_req.max_contacts_per_lead == 5
    assert enrich_req.providers == ["apollo", "hunter"]
    with pytest.raises(ValidationError):
        schemas.EnrichLeadsRequest(leads=[lead], max_contacts_per_lead=6)

    # EnrichLeadsResponse & BuildLeadsResponse serialization & JSON schema generation
    enriched = schemas.EnrichedLead(base_lead=lead)
    enrich_resp = schemas.EnrichLeadsResponse(
        status="ok",
        leads_received=1,
        leads_attempted=1,
        leads_enriched=0,
        partial=0,
        failed=0,
        provider_usage=schemas.ProviderUsageTracker(),
        leads=[enriched],
    )
    build_resp = schemas.BuildLeadsResponse(
        status="ok",
        jobs_received=1,
        companies_found=1,
        qualified_leads=1,
        leads=[lead],
    )
    assert json.loads(enrich_resp.model_dump_json())["leads_received"] == 1
    assert json.loads(build_resp.model_dump_json())["qualified_leads"] == 1
    assert "provider_usage" in schemas.EnrichLeadsResponse.model_json_schema()["properties"]
    assert "jobs_received" in schemas.BuildLeadsResponse.model_json_schema()["properties"]


def test_backend_schemas_import_isolation(tmp_path: Path) -> None:
    """Importing `backend.schemas` directly loads zero FastAPI app, Ollama, ChromaDB, or SQLite modules."""
    proc = run_isolated_subprocess(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys; "
                "import backend.db_path; "
                "before = {'pydantic': 'pydantic' in sys.modules, 'schemas': 'backend.schemas' in sys.modules}; "
                "import backend.schemas; "
                "loaded = {m: (m in sys.modules) for m in ("
                "'fastapi', 'backend.main', 'ai_engine', 'sales_engine.llm_client', 'ollama', 'chromadb', 'sqlite3')}; "
                "print(json.dumps({'before': before, 'loaded': loaded}))"
            ),
        ],
        isolated_db_path=tmp_path / "isolated_schema_probe.db",
        trusted_dir=tmp_path,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    res = json.loads(proc.stdout.strip())
    assert res["before"] == {"pydantic": False, "schemas": False}
    assert res["loaded"] == {
        "fastapi": False,
        "backend.main": False,
        "ai_engine": False,
        "sales_engine.llm_client": False,
        "ollama": False,
        "chromadb": False,
        "sqlite3": False,
    }
