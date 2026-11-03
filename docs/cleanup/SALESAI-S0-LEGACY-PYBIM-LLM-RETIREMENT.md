# SalesAI — S0: Retire pyBIM-LLM Legacy Safely

**Status:** Implementation instruction for Antigravity; NOT a claim that deletion has already occurred.
**Date:** 2026-10-21
**Working repository (verify first):** `C:\Sales-AI\SalesAI`
**Historical snapshot reference:** latest provided `SalesAI_export.zip`, 386 entries; the live working tree is authoritative and may contain additional legacy files.
**Next planned stage:** S1, local lightweight SalesAI + external LLM API migration. **Do not implement S1 in S0.**

## Goal
Remove **provably isolated** components of the *old Revit/pyBIM-LLM workstation product* while retaining the SalesAI product, its historical evidence and acceptance gates. The user already copied the whole project as a backup; still verify Git status and create a reversible repository checkpoint before modifications.

Do not confuse `pyBIM` (the real business/brand and content of SalesAI prompts) with **pyBIM-LLM legacy software**. Retain valid references to pyBIM's services, BIM domain, company identity, emails, and test data.

## Non-negotiable safety
1. Work only inside the verified SalesAI repository; never touch the user's separate backup, sibling repositories, system directories, or user data outside repo.
2. Before editing: `git status`, active branch, HEAD, branch protections, and untracked data. If dirty, do **not** reset, clean, overwrite, stash or delete user changes; report and stop until clarified.
3. Create a dedicated local branch such as `chore/salesai-legacy-retirement` (if safe). Record HEAD; Git history provides rollback. No destructive history rewrite, no force-push.
4. Maintain functioning SalesAI Phase 1–12 business rules, accepted Production Steps 1–6 records, local FastAPI operator UI, discovery, sources, dedup, enrichment, outreach drafting, review, suppression, sending gates, inbox/replies, analytics, import/export, benchmark evidence and tests.
5. **No blanket deletion:** no directory/file deletion purely due to `pybim`, `BIM`, `ollama`, `legacy` or `ai_engine` in its name. Resolve call graph, imports, route registration, runtime file access, launcher references, tests and docs before each removal.
6. Preserve real/historical user content: `.env`, secrets, credentials, data stores, contact histories, sheets, Excel files, output reports, samples, annotations and backed-up artefacts. Exclude sensitive/runtime files from ZIP; never scrub, delete or send them without specific authorization.
7. No live sending, external API use, inbox sync, scraping, model pull, or installation in this cleanup. Tests should be offline/mock.
8. No new cloud gateway implementation, sweeping phase rewrites, dependency upgrades or production deployment during S0.
9. Do not weaken tests, mark passing tests skipped, fake metrics, or silently modify acceptance criteria to make cleanup pass.

## Known cross-dependencies from the provided 386-file ZIP (revalidate live)
- `backend/main.py` is a mixed pyBIM-LLM + SalesAI FastAPI entrypoint. Many SalesAI tests import it. **Do not remove the entire file.** Only detach provably legacy routes/services when their dependencies are handled and SalesAI routes remain stable.
- `backend/schemas.py` is imported by many `sales_engine/*` modules. **KEEP**; prune only isolated old schema types if safe.
- `ai_engine/llm_client.py` is used by `sales_engine/analysis/website_analyzer.py`, `job_analyzer.py`, `sales_engine/discovery/query_generator.py`, `sales_engine/inbox/classifier.py`, `suggestion.py`, `sales_engine/outreach/email_generator.py` and tests. **KEEP as a transitional shared interface** until S1/S2 replace Ollama with API adapters. Do not require or launch Ollama during S0.
- `ai_engine/data_ingestor.py` is imported by `tests/test_admin_gate.py` as well as legacy server code. Investigate before removal.
- `data/rules/*` includes Revit documentation used by the legacy ingestion component; ensure neither active SalesAI scoring nor needed historical snapshots depend on it before excluding/removing. Prefer quarantining as `legacy-required` when uncertain.
- `start_server.py` and `run_sales_ai.bat` are current entrypoints. **KEEP**, but disable their automatic **Ollama start / model pull** behavior safely for the new design. Until external API setup exists, a missing LLM should produce a controlled unavailable status, not crash SalesAI's deterministic routes or attempt a download.
- `sales_engine/benchmark/*`, `scripts/benchmark_ollama.py`, `setup/RUN-LLM-BENCHMARK.bat`, `setup/run_llm_benchmark.ps1`, hardware audit docs and benchmark reports are historical Step 5/6 artefacts. **Do not delete historical evidence by default**; exclude optional local-model tools from the normal runtime/installer profile and document them as non-production legacy tools.
- `tests/golden/snapshots/**` may contain `pybim` in names because pyBIM is the business being qualified. **KEEP**.

## Inventory and decision matrix (mandatory before modifications)
Produce `docs/cleanup/S0-PYBIM-LLM-INVENTORY.md` AND machine-readable `docs/cleanup/s0_file_decisions.json` with fields:
`path`, `purpose`, `category`, `imports_or_callers`, `runtime_refs`, `test_refs`, `decision`, `justification`, `proof`, `action_taken`.
Decision must be one of:
- `KEEP_SALESAI_ACTIVE`
- `KEEP_SHARED_TRANSITIONAL`
- `RETAIN_HISTORICAL_EXCLUDED_FROM_RUNTIME`
- `REMOVE_VERIFIED_LEGACY`
- `DEFER_UNCERTAIN`

Scan at least these candidates:
- Old Revit plugin / `.addin`, `.extension`, C# / pyRevit source where present in live repo (may not appear in exported ZIP).
- `ai_engine/acc_client.py`, `cloud_auditor.py`, `rag_retriever.py`, `data_ingestor.py`, `crew_orchestrator.py`, `ingest_queue.py`.
- Historical pyBIM launchers `run_pybim.bat`, `stop_pybim.bat`.
- `scripts/legacy_diagnostics/*`, old Revit/ACC-specific routes and schemas, old studio UI assets under `backend/static/` that are demonstrably unused by SalesAI.
- Legacy `setup/pull_models.bat`, `setup/full_install.bat`, `setup/install_requirements.bat` and dependency entries for `ollama`, `langchain-ollama`, Chroma/RAG tools; remove only if unused by retained SalesAI code/tests or defer for S1.
- `repo_manifest.json`, `release_manifest.json`, `scripts/rebuild_zip.py`, repository audit, README, docs and references affected by deletions.

Use `rg`, Python AST/import search, actual FastAPI route inspection, startup and unit tests, and cross-reference runtime assets. **Do not** mistake a clean textual `rg` result for sufficient proof if code uses dynamic imports or file-path strings.

## Safe deletion / retirement pass
1. Delete by `git rm` **only** paths categorized `REMOVE_VERIFIED_LEGACY` that have no required SalesAI runtime/test consumers. For pure legacy standalone directories, tracked removals are acceptable because the full backup and Git history are retained.
2. For **shared modules**, preserve file and behavior; if safely possible, isolate the legacy endpoints/initializers into a separately retired module and remove the old route registration without touching SalesAI capabilities. If doing so requires broad refactoring, classify as `KEEP_SHARED_TRANSITIONAL` and **defer** to S1/S2; don't force deletion.
3. Update launch scripts so normal SalesAI startup never auto-starts Ollama, never auto-pulls a model, never kills unrelated processes by PID/port, never exposes the server to LAN/public internet by default. Maintain `127.0.0.1` binding. If full startup cannot be made independent of Ollama with a minimal safe change, leave launcher explicitly blocked with a clear message and document the S1 blocker; do not silently claim the app runs.
4. Keep the old Ollama benchmark **available as archived developer diagnostics**, not a production runtime requirement. Never present the old local model as the target architecture.
5. Update dependency/release manifests **only where justified**, preserve reproducible developer-source packaging, denylist and documentation integrity. Do not auto-install packages.
6. Make explicit that new intended architecture is: laptop (light FastAPI/UI/DB/deterministic workflow) + HTTPS external LLM APIs, manual runs, **10 qualified new firms per active day / about 300 per 30-day planning month**. The API adapter work comes later.

## Preserve the current business/reference context
- Current reference set: `Milan` 16, `Turin` 20, `Rome` 17, `Bozen` 3, and the last 3 new LinkedIn rows = 59 reference rows. These source rows are **not** automatically ground truth; preserve provenance and future importer plans.
- Turin 3BA is email-sourced with a meeting being scheduled; not confirmed until slot agreed.
- Other external LinkedIn meeting does not count in email conversion. A LinkedIn `not_now / future_follow_up` reply is a distinct record.
- Ten synthetic fixtures (5 clean + 5 misleading) and separately held answer key for next API benchmark.
- Four model tiers × four candidates, measured token/cost tracking, only escalate a failing task to higher tier.
- Existing Phase 9 authority, legal/channel permission and human send approval remain untouched.
- `Hamid - Email` is out of scope.

## Acceptance / evidence required
- Pre/post inventory count and **exact** removal list, with justification for each deletion.
- Import smoke test of `backend.main`, core SalesAI FastAPI routes and Sales UI; intentional legacy routes may be gone but must be listed.
- Run standalone business test suites and full `python -m pytest tests/ -q`. Report actual passed/failed/skipped and compare baseline. Legacy-only tests may be consciously retired with documented reasoning, but no SalesAI business/authority test may be silently dropped.
- Verify deterministic discovery, lead identity/dedup, scoring, source settings, Excel/CSV output, outreach draft validation, human approval, suppression/sending restrictions, reply workflows and analytics.
- Verify zero Ollama process started, zero automatic model download, zero email sent, zero live provider requests during test.
- Run `git diff --check` and `scripts/audit_repository.py` if still applicable; verify denylist count = 0 and generated ZIP contains active SalesAI modules, UI and tests without runtime secrets or databases.
- Produce `docs/cleanup/S0-LEGACY-RETIREMENT-REPORT.md`: current branch/HEAD, changed files, removed paths, retained/deferred shared components, test results, ZIP count, known blockers, rollback instructions and next S1 tasks.
- Commit **only when** required checks pass and without committing secrets or user data. Do not push to remote or merge automatically; report commit hash and paths to artefacts. If there is a blocker or an ambiguous deletion, stop, preserve state, and ask for operator review.

## STOP condition
End the task with a **report**, not additional phases. Do not start API provider adapters, model benchmark, S1 code rewrite, Phase 1–12 reimplementation, hosted deployment or live email work. The operator reviews the S0 report before authorizing further steps.
