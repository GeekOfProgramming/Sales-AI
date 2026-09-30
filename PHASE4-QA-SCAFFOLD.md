# SalesAI Phase 4 QA Scaffold — Job Extraction & Company Intelligence

## Purpose
Prepare Phase 4 QA infrastructure now, while deferring heavy semantic LLM evaluation until a stronger local model is available.

Run now:
- source detection
- structured ATS parsing
- JSON-LD parsing
- company normalization
- company-domain logic
- source_company_key preservation
- error classification

Prepare now but run later:
- LLM relevance
- technologies/seniority interpretation
- relevant_signals
- evidence grounding
- business relevance

## Files
```text
tests/golden/
├── phase4_jobs.json
└── snapshots/job_pages/
    ├── greenhouse/
    ├── lever/
    ├── ashby/
    └── generic/

tests/acceptance/
└── test_phase4_acceptance.py

tests/reports/
└── phase4_latest.json
```

## Deterministic cases to implement now
- P4-DET-001 Source Detection: Greenhouse / Lever / Ashby / Generic.
- P4-DET-002 Greenhouse extraction: company, title, location, employment_type, posted_date, job_url, source_company_key.
- P4-DET-003 Lever extraction: company, title, workplace/remote metadata, source_company_key.
- P4-DET-004 Ashby extraction: company, title, location, source metadata.
- P4-DET-005 Generic JSON-LD JobPosting extraction.
- P4-DET-006 JSON-LD @graph JobPosting extraction.
- P4-DET-007 Company domain resolution. Never use greenhouse.io / lever.co / ashbyhq.com as hiring-company domain.
- P4-DET-008 Company normalization for GmbH/Ltd/Inc/LLC/S.r.l./S.p.A., punctuation and whitespace.
- P4-DET-009 source_company_key preservation through raw job -> analyzer -> StructuredJob.
- P4-DET-010 Error classification: fetch_error / timeout / parse_error / analysis_error.

## Negative deterministic cases
- P4-NEG-001 Missing company.
- P4-NEG-002 Missing title.
- P4-NEG-003 Malformed JSON-LD.
- P4-NEG-004 Expired/removed job: do not fabricate.
- P4-NEG-005 Careers homepage, not a job: do not accept without actual job data.
- P4-NEG-006 Duplicate URL variants.
- P4-NEG-007 ATS provider domain must not become company_domain.

## Semantic cases for later
Create placeholder Golden cases with status:
`NOT_RUN_MODEL_LIMITATION`

Future fields:
- requirements
- technologies
- seniority
- remote_status
- relevant_signals
- evidence

Example:
```json
{
  "case_id": "P4-SEM-001",
  "source_url": "...",
  "snapshot_path": "...",
  "expected": {
    "company_name": "...",
    "job_title": "...",
    "technologies": [],
    "seniority": null,
    "remote_status": null,
    "relevant_signals": []
  },
  "status": "NOT_RUN_MODEL_LIMITATION"
}
```

## Evidence rule for later
Every relevant signal must contain grounded evidence traceable to the stored job snapshot.
Unsupported evidence, invented technology, or invented requirements must fail.

## README-TEST output
For each Phase 4 case show:
- Source
- Snapshot
- Input
- Expected
- Actual
- Differences
- Evidence check
- Result: PASS / FAIL / REVIEW / NOT_RUN_MODEL_LIMITATION

## Current policy
Do not tune production code specifically to qwen2.5:1.5b just to make semantic Phase 4 tests pass.
