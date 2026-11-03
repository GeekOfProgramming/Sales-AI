# SalesAI Release Package Contents & Boundaries

This document defines the contents of the official SalesAI release distribution (`SalesAI_export.zip`) packaged by `scripts/rebuild_zip.py`.

## Release Packaging Philosophy

Release packaging uses an **allowlist + defense-in-depth denylist** strategy:
1. Only known-safe source, test, config, documentation, and asset trees are traversed.
2. An explicit security denylist intercepts any runtime databases, secrets, credentials, environment files, cache files, and nested archives.

---

## Included in Release Package

| Component | Path | Description |
| :--- | :--- | :--- |
| **Sales Engine** | `sales_engine/` | Core business logic: Discovery v2, Qualification, Scoring, Outreach, Suppression, Analytics |
| **Backend & Web Console**| `backend/` | FastAPI routes, Web Console controllers, HTML templates, view models |
| **AI Engine** | `ai_engine/` | LLM client wrappers, prompt generators, data ingestion |
| **Configuration** | `config/` | Default source registry config (`discovery_sources.yaml`) |
| **Static Reference Data** | `data/rules/` | BIM API and ISO standards markdown documentation |
| **Web Static Assets** | `static/` | CSS stylesheets, JavaScript client helpers, icons |
| **Documentation** | `docs/` | System architecture, repository structure, legacy audit, and historical archives |
| **Project Docs** | `README.md`, `README-TEST.md` | Primary documentation and test/verification guide |
| **Dependencies & Config** | `requirements.txt`, `pytest.ini`, etc. | Python dependencies and linter/tester settings |
| **Launch Scripts** | `run_sales_ai.bat`, `start_server.py` | Local execution entrypoint and server runner |
| **Test Suites** | `tests/` | Comprehensive test suites (870+ tests) enabling standalone verification on target laptop |
| **Maintenance Scripts** | `scripts/` | Audit tools, report generators, and deterministic packagers |
| **Sanitized Examples** | `exports/sample_export/` | Synthetic fixtures for CRM and Excel export testing |

---

## Excluded from Release Package (Strict Denylist)

| Category | Patterns / Paths | Rationale |
| :--- | :--- | :--- |
| **Runtime Databases** | `*.db`, `*.sqlite*`, `*.db-wal`, `*.db-shm` | Runtime state must never ship with code; databases are initialized dynamically |
| **Vector DBs** | `chroma_db/`, `chroma_data/` | Embedding caches are built at runtime; excluded to avoid bloating and state pollution |
| **Secrets & Keys** | `.env*`, `token.json`, `client_secret*`, `credentials*` | Zero credentials policy; credentials must be set strictly via environment variables |
| **Virtual Environments** | `.venv/`, `venv/`, `env/` | Target machine uses its own clean virtual environment |
| **Cache & Bytecode** | `__pycache__/`, `*.pyc`, `*.pyo`, `.pytest_cache/` | Non-deterministic compiled artifacts |
| **Third-Party Binaries** | `cloudflared.exe`, `*.exe` | Binaries must not be bundled without verification; local execution target is localhost |
| **Nested Archives** | `*.zip`, `SalesAI_export.zip` | Prevents recursive packing and distribution bloat |
| **Runtime Logs** | `*.log`, `logs/` | Ephemeral operational output |
