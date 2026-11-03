# SalesAI — Production Readiness Step 6
## Real Local-LLM Benchmark and Model Selection

**Baseline:** Frozen Phase 1–12; accepted Production Steps 1–5. Build on developer laptop; run the real benchmark on Hamed's laptop. Do not freeze Step 6 until actual Hamed hardware results are reviewed.

## 1. Critical distinction

The existing `sales_engine/analytics/evaluator.py` and `fixtures.py` are an *offline evaluation scaffold*. In the inspected baseline `ModelEvaluator.evaluate_model()` uses deterministic fixture evaluation and can substitute synthetic latency values (e.g. 420/1250 ms). **These numbers are not evidence of real local-model performance.** Preserve Phase 12 compatibility, but do NOT use synthetic model scores, benchmark latency, throughput, GPU usage, or fabricated model outputs in Step 6.

Implement a new explicitly named `real_ollama_benchmark` runner using **actual local Ollama inference** and actual Ollama response metrics, plus wall-clock observations. Any metric unavailable must be `null` with `measurement_status=not_measured`.

## 2. Target machine (observed October 20, 2026)

- Windows 11 Home 64-bit; Ryzen 7 5800H, 8C/16T
- 15.4 GB RAM; RTX 3050 Laptop, dedicated 4 GB VRAM, NVIDIA driver 591.74
- C drive ~180 GB free
- Ollama 0.40.6 and Git 2.56 installed later; Ollama `llama3.2:3b` installed and smoke-tested.
- Smoke test observed `ollama ps` CPU/GPU 20%/80% placement with context 4096. This is not utilization or measured throughput.

Never embed personal machine identifiers or Windows usernames in reports.

## 3. Target evaluation models

- Mandatory initial actual run: `llama3.2:3b` (already installed on Hamed laptop).
- Optional comparison after operator confirmation: `qwen2.5:1.5b`, `qwen2.5:7b` Q4 / appropriate Ollama quantization.
- Never automatically pull, delete, or replace models. Do not require 7B to close the 3B baseline.
- Detect Ollama availability and model presence first; missing model must report `NOT_INSTALLED`, not fail the whole benchmark.

## 4. Deliverables

Create new isolated components, e.g.:

```
sales_engine/benchmark/real_ollama.py
sales_engine/benchmark/scoring.py
sales_engine/benchmark/report.py
scripts/benchmark_ollama.py
setup/RUN-LLM-BENCHMARK.bat
setup/run_llm_benchmark.ps1
tests/test_real_llm_benchmark.py
```

Reuse sanitized `sales_engine/analytics/fixtures.py` (`FIXTURE_SET_VERSION=phase12-v1`) wherever a fixture is compatible. Add a versioned benchmark fixture adapter or new sanitized fixtures only if necessary. Do not mutate old fixture expectations without a version bump.

Outputs, all under ignored `runtime/benchmarks/`:

```
benchmark_<run_id>.json
benchmark_<run_id>.md
latest.json
latest.md
```

Do not include raw secrets or full real-business email bodies. Synthetic fixture prompts and outputs may be stored locally; production release excludes runtime outputs.

## 5. Ollama invocation

Call the local endpoint only, default `http://127.0.0.1:11434/api/generate` or `/api/chat`; use `stream:false` and a strict timeout. Honor model exact name, fixed prompt template, explicit temperature 0 where supported, seed if supported, declared context and max output tokens. Log actual effective options. Distinguish HTTP error, missing model, unloaded model, timeout, malformed response, and inference completed.

Gather actual response fields when present:

- `total_duration`, `load_duration`, `prompt_eval_duration`, `eval_duration` (Ollama commonly returns nanoseconds)
- `prompt_eval_count`, `eval_count`
- external elapsed wall time with `time.perf_counter()`
- actual model identity and backend-reported metrics.

Derived tokens/sec = `eval_count / (eval_duration_ns / 1e9)` **only if positive measured fields exist**. Otherwise `null`. Capture first cold run separately from warm measured repetitions. Do not label synthetic latency as actual.

Optional `ollama ps`, `nvidia-smi` before/after and during runs with clearly documented sample semantics; never interpret Ollama's GPU placement percentage as instantaneous GPU utilization. If live memory peak unavailable, report `null` rather than estimates.

## 6. Fixtures and scoring

Cover six independent SalesAI tasks:

1. website_extraction
2. company_classification
3. email_draft_generation
4. reply_classification
5. reply_suggestion
6. meeting_time_extraction

Scoring is deterministic where possible:

- JSON parse validity (strip *only* fenced wrapper in a separately reported lenient parse; distinguish strict JSON-only compliance)
- schema required fields/types
- enum validation against actual SalesAI taxonomy, included in prompts
- exact/normalized factual match for expected fields where feasible
- grounding checks and forbidden-claim detection
- actual model refusal / timeout / error
- no invented meeting dates, pricing, contract claims or proof of past projects
- semantic stylistic quality marked `NEEDS_HUMAN_REVIEW` / `DEFERRED_MODEL`, not automatically PASS.

Avoid mislabeling the opportunity `job posting` as a taxonomy error if the fixture's prompt does not specify the allowed enums; benchmark prompts must specify exact enums before judging enum compliance.

Require source text treated as untrusted; include at least one prompt-injection fixture. No action execution from model output.

## 7. Test procedure

For every model use the same fixture IDs, prompts, options and scoring rules. Use one short warm-up then measured repetitions (default 2, configurable 1–3) so runtime stays manageable. Show task-level results as well as aggregate descriptive totals. Compare models only when fixture-set version and test settings match. Small fixture sample must not imply statistical superiority. Model selection is manual and documents tradeoffs between quality, throughput, RAM/VRAM fit, and operator experience.

Default first run must be bounded (~6–12 synthetic fixture prompts, repetition count 1–2). No long benchmark or automatic multi-model downloads without an operator opt-in.

## 8. Safety

- Local Ollama calls allowed; **zero outbound emails**, zero actual source discovery, zero inbox sync, zero live API providers.
- Zero Phase 5 score, Phase 9 suppression/send, Phase 10 discovery/source configuration, Phase 11 state, or Phase 12 analytics mutation.
- Never treat model output as commands or write model-provided code to disk as executable.
- Bind host to localhost, no public server/tunnel.
- Do not run `ollama serve` blindly if already running.
- No model pull by default.
- Protect user-provided host URL: localhost/loopback only by default.

## 9. CLI / UX

Provide a clear command from repo root, for example:

```
python scripts/benchmark_ollama.py --model llama3.2:3b --hardware-profile runtime/hardware/hardware_profile.json --repetitions 2
```

A PowerShell/BAT wrapper should find local Python, explain missing dependencies, confirm the Ollama service is reachable, and show the report location. Support `--list-models`, `--dry-run` (no model inference), `--task`, `--max-fixtures` and `--output-dir`. Model override should never auto-download it.

Crucially, on Hamed laptop the project might be a clean ZIP extraction with Python 3.10.10 and no `.venv`; benchmark dependencies should be minimal (prefer Python standard library) and the runner should work without installing the full SalesAI stack (`chromadb`, `scrapling`, FastAPI etc.) if possible. If full dependencies genuinely required, provide a small isolated requirements file and setup instructions. Avoid changing main requirements merely for benchmark.

## 10. Regression tests

Create deterministic mocks; no actual running Ollama required in CI. At minimum:

```
PRD-BENCH-001 correct local endpoint and model
PRD-BENCH-002 unknown / uninstalled model reports NOT_INSTALLED
PRD-BENCH-003 cold and warm timings separated
PRD-BENCH-004 real durations from Ollama nanoseconds converted accurately
PRD-BENCH-005 no synthetic latency injected
PRD-BENCH-006 missing metrics stay null
PRD-BENCH-007 token throughput computed only from real durations and counts
PRD-BENCH-008 strict vs lenient JSON compliance differentiated
PRD-BENCH-009 schema/type validation
PRD-BENCH-010 enum validation uses supplied enums
PRD-BENCH-011 grounding/forbidden claims checked
PRD-BENCH-012 meeting times never fabricated
PRD-BENCH-013 six tasks represented
PRD-BENCH-014 prompt injection text never executes
PRD-BENCH-015 no send/sync/discovery/production mutation
PRD-BENCH-016 no model pull by default
PRD-BENCH-017 timeout and API errors recorded without whole-batch loss
PRD-BENCH-018 hardware label + fixture version + options in report
PRD-BENCH-019 no secrets or runtime reports inside ZIP
PRD-BENCH-020 Python 3.10 baseline compatibility and full regression green
```

Run:

```
python -m pytest tests/test_real_llm_benchmark.py -v
python -m pytest tests/ -q
```

## 11. Report and acceptance

Report real measurements with provenance, clear `MEASURED`/`NOT_MEASURED`, cases attempted/succeeded/failed, JSON/schema/enum/grounding counts, average/median measured latency (warm only), actual token throughput, failures, machine hardware profile, installed model and effective inference settings. Add a human-readable interpretation, not fabricated scores. Document any limitations of simple deterministic grounding heuristics.

Release ZIP from developer laptop must contain benchmark scripts/tests but exclude `runtime/benchmarks/`, `runtime/hardware/`, secrets, DB and caches; denylist=0.

**Build complete** once mock tests and existing regressions pass and runnable scripts are packaged. **Actual Step 6 acceptance** requires benchmark execution on Hamed laptop and review of real JSON/Markdown reports. Do not mark final model selected automatically. Do not reopen frozen Phase 12 just to replace the old offline evaluator; distinguish the new real runner clearly.

## 12. Required Antigravity completion report

1. files added/modified
2. reuse vs separation from Phase 12 offline evaluator
3. exact Ollama local request + captured metrics
4. fixture list/tasks and evaluation logic
5. real vs synthetic metric safeguards
6. first run command on Hamed laptop
7. test counts and full repository regression
8. git commit and ZIP count/denylist
9. final `SalesAI_export.zip`
10. explicitly state: waiting for Hamed laptop real benchmark before final model selection.
