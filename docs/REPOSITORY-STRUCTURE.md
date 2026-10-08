# SalesAI Repository Structure & Classification

This document details the authoritative repository directory organization and classification taxonomy established in Production Readiness Step 4.

## Root Directory (Minimal & Clean)

The root folder is kept minimal to serve strictly as an entrypoint and project configuration root:

```text
C:\Sales-AI\SalesAI
├── .gitignore               # Strict exclusion of secrets, DBs, cache, and virtual environments
├── pyrefly.toml             # Pyrefly linter/formatter config
├── pyrightconfig.json       # Type-checker configuration
├── pytest.ini               # Pytest root configuration
├── requirements.txt         # Production and development dependencies
├── README.md                # Project introduction & high-level architecture
├── README-TEST.md           # Authoritative Golden QA matrix and test documentation
├── run_sales_ai.bat         # Production launcher for SalesAI (Ollama check + FastAPI on :8000)
├── start_server.py          # Server bootstrap entrypoint (Localhost by default)
├── run_pybim.bat            # Legacy pyBIM launcher (preserved for backward compatibility)
└── stop_pybim.bat           # Legacy pyBIM terminator
```

---

## Directory Organization

```text
├── ai_engine/               # RAG, LLM clients, classification prompts, embedding logic
├── backend/                 # FastAPI application, REST endpoints, UI console router, templates
│   ├── sales_ui/            # Web Console (Discovery, Sources, Campaigns, Analytics, Settings)
│   │   ├── templates/       # Jinja2 templates for operator-facing pages
│   │   └── router.py        # Synchronous UI controllers with CSRF and error guards
│   └── main.py              # Central FastAPI app, route definitions, health endpoints
├── config/                  # Configuration files (discovery_sources.yaml, settings)
├── data/                    # Static reference data (e.g. data/rules/ for BIM domain knowledge)
├── docs/                    # Structural and operational documentation
│   ├── REPOSITORY-STRUCTURE.md
│   ├── LEGACY-COMPONENTS.md
│   ├── RELEASE-CONTENTS.md
│   └── archive/             # Historical non-authoritative build specs, QA specs, and patches
├── exports/                 # Export fixtures and sanitized sample outputs
│   └── sample_export/       # Sanitized synthetic CRM and Excel export fixtures
├── revit_plugin/            # Legacy pyBIM Autodesk Revit add-in (preserved, release-excluded)
├── sales_engine/            # Authoritative business logic (Discovery v2, Scoring, Outreach, Analytics)
│   ├── discovery_v2/        # Scope, SourceRegistry, Adapters, Runner
│   ├── scoring/             # Phase 5 qualification and scoring algorithms
│   ├── outreach/            # Phase 8 generation and prompt building
│   ├── suppression/         # Phase 9 sending and suppression authority
│   └── analytics/           # Phase 12 learning loops, projections, and reports
├── scripts/                 # Operational, release packaging, and audit scripts
│   ├── audit_repository.py  # Repository cleanliness and safety linter
│   ├── rebuild_zip.py       # Deterministic release packager with strict denylist
│   └── legacy_diagnostics/  # Manual test and verification scripts from legacy pyBIM
├── setup/                   # Environment setup and dependency installation batch scripts
└── tests/                   # Automated regression, golden, and acceptance test suites (870+ tests)
```

---

## Repository Classification Taxonomy

Every file and directory in this repository belongs to one of the following 10 classes:

1. **`production_runtime`**: Core executable code required for running SalesAI (`sales_engine/`, `backend/`, `ai_engine/`, `start_server.py`, `run_sales_ai.bat`).
2. **`production_config`**: Configuration defining system settings and data sources (`config/discovery_sources.yaml`, `requirements.txt`).
3. **`frozen_legacy_required`**: Legacy pyBIM modules and rules retained to maintain backward compatibility (`data/rules/`, `revit_plugin/`, `run_pybim.bat`, `stop_pybim.bat`).
4. **`tests_and_qa`**: Comprehensive automated test suites, golden snapshots, and test reports (`tests/`).
5. **`developer_tools`**: Project configs, linters, packaging, and auditing tools (`scripts/audit_repository.py`, `scripts/rebuild_zip.py`, `pyrightconfig.json`, `pytest.ini`, `pyrefly.toml`).
6. **`documentation_active`**: Active structural, operational, and architectural documentation (`README.md`, `README-TEST.md`, `docs/REPOSITORY-STRUCTURE.md`, `docs/LEGACY-COMPONENTS.md`, `docs/RELEASE-CONTENTS.md`).
7. **`documentation_archive`**: Non-authoritative historical phase specs, QA plans, and patch notes (`docs/archive/`).
8. **`sanitized_examples`**: Safe, synthetic sample exports containing no real PII, credentials, or client data (`exports/sample_export/`).
9. **`generated_runtime_excluded`**: Runtime state, databases, vectors, and logs that must never be committed or packaged (`*.db`, `chroma_db/`, `logs/`, `data/sales_outreach.db*`).
10. **`release_excluded`**: Third-party executables, public tunnels, or local development files excluded from the production release bundle (`cloudflared.exe`, `PUBLIC_URL.txt`, `.venv/`, `.env`).
