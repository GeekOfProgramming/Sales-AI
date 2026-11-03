# README-TEST — SalesAI QA / Acceptance Testing

## هدف این فایل

این فایل باید بعد از اجرای تست‌های SalesAI به‌صورت قابل‌خواندن برای انسان نشان دهد:

- چه چیزی را تست کردیم
- منبع واقعی یا Fixture هر تست چه بود
- چه ورودی‌ای به سیستم دادیم
- انتظار ما چه بود
- سیستم واقعاً چه خروجی‌ای داد
- نتیجه Pass / Fail / Review چه شد
- اگر Fail شد، اختلاف دقیقاً چه بود
- تست با چه نسخه مدل، Provider، Commit و تاریخ اجرا شده است

هدف فقط سبز شدن Unit Test نیست. هدف این است که مطمئن شویم خروجی از نظر فنی و از نظر بیزینسی درست است.

---

# 1. انواع تست

SalesAI باید پنج لایه تست داشته باشد:

### A. Unit Tests
برای توابع deterministic و ماژول‌های کوچک.

نمونه:
- URL normalization
- source detection
- score calculation
- deduplication
- date buckets
- email-status normalization

### B. Integration Tests
برای اتصال واقعی چند ماژول.

نمونه:
- WebsiteFetcher -> WebsiteAnalyzer
- Discovery -> Search Provider
- Job Source -> JobAnalyzer
- Lead Aggregator -> Lead Scorer
- Enrichment Orchestrator -> mocked providers

### C. Golden Dataset Tests
مهم‌ترین لایه QA.

برای نمونه‌های واقعی، خروجی مورد انتظار را از قبل توسط انسان تعریف می‌کنیم و نتیجه سیستم با آن مقایسه می‌شود.

### D. Live Source Tests
تعداد محدودی تست روی URLها / Providerهای واقعی.

این تست‌ها باید جدا از Unit Test باشند، چون داده وب و API ممکن است تغییر کند.

### E. Human Acceptance Review
برای مواردی که Pass/Fail صرفاً ماشینی کافی نیست.

نمونه:
- آیا Job واقعاً Lead خوبی برای pyBIM است؟
- آیا Buyer Role انتخاب‌شده منطقی است؟
- آیا evidence واقعاً ادعای سیستم را پشتیبانی می‌کند؟
- آیا Lead Score از نظر بیزینسی قابل قبول است؟

---

# 2. ساختار پیشنهادی فایل‌ها

```text
tests/
├── golden/
│   ├── manifest.json
│   ├── phase2_websites.json
│   ├── phase3_discovery.json
│   ├── phase4_jobs.json
│   ├── phase5_leads.json
│   ├── phase6_enrichment.json
│   │
│   └── snapshots/
│       ├── websites/
│       ├── job_pages/
│       └── provider_responses/
│
├── acceptance/
│   ├── test_phase2_acceptance.py
│   ├── test_phase3_acceptance.py
│   ├── test_phase4_acceptance.py
│   ├── test_phase5_acceptance.py
│   └── test_phase6_acceptance.py
│
└── reports/
    └── latest_test_report.json
```

و در Root پروژه:

```text
README-TEST.md
```

---

# 3. Golden Test Case Format

هر Test Case باید حداقل این ساختار را داشته باشد:

```json
{
  "case_id": "P4-JOB-001",
  "phase": 4,
  "title": "Greenhouse BIM Manager extraction",
  "source_type": "live_url_snapshot",
  "source_url": "https://...",
  "snapshot_path": "tests/golden/snapshots/job_pages/P4-JOB-001.html",
  "captured_at": "2026-09-29T18:00:00+02:00",

  "input": {
    "url": "https://..."
  },

  "expected": {
    "company_name": "Example Engineering",
    "job_title": "BIM Manager",
    "source": "greenhouse",
    "must_contain_technologies": ["Revit"],
    "must_not_contain_technologies": ["InventedTool"]
  },

  "human_notes": "This job is relevant because the posting explicitly mentions Revit workflow ownership."
}
```

نکته مهم:

برای Sourceهای وب، تا جای ممکن Snapshot ذخیره شود تا اگر صفحه چند هفته بعد تغییر کرد، Regression Test هنوز reproducible باشد.

---

# 4. Phase 1 — Local LLM Core

## باید چه چیزی را مطمئن شویم؟

- FastAPI بالا می‌آید.
- `/health` واقعاً وضعیت Ollama را درست گزارش می‌کند.
- مدل Sales تنظیم‌شده واقعاً وجود دارد.
- اگر مدل وجود نداشت، خطای واضح می‌گیریم.
- Sales request از BIM system prompt استفاده نمی‌کند.
- JSON generation failure به شکل controlled مدیریت می‌شود.

## Acceptance Cases

### P1-001 — Healthy Runtime
Expected:
- HTTP 200
- Ollama reachable
- configured Sales model visible

### P1-002 — Missing Model
Input:
- `SALES_LLM_MODEL` روی مدل نصب‌نشده

Expected:
- controlled error
- نه fake success

### P1-003 — Sales Context Isolation
Input:
- درخواست تحلیل B2B

Expected:
- پاسخ BIM/Revit-specific نباشد مگر input خودش مرتبط باشد.

---

# 5. Phase 2 — Website Analyzer

این Phase یکی از مهم‌ترین جاهای Human QA است.

## Golden Website Set

حداقل 10 وب‌سایت واقعی انتخاب شود:

- 3 شرکت واضحاً مناسب pyBIM
- 3 شرکت متوسط / borderline
- 2 شرکت نامرتبط
- 1 سایت با محتوای کم
- 1 سایت با navigation / redirects / noise زیاد

برای هر سایت انسان باید قبل از اجرای AI این موارد را ثبت کند:

```text
services
target_industries
target_company_types
buyer_roles
primary_job_signals
secondary_job_signals
negative_signals
```

## چیزی که بررسی می‌کنیم

### Accuracy
آیا Serviceهای اصلی درست استخراج شده‌اند؟

### Grounding
آیا هر ادعا واقعاً در سایت پشتیبانی می‌شود؟

### Hallucination
آیا service / industry / role اختراع شده؟

### Buyer-role quality
آیا نقش‌هایی که برای تماس پیشنهاد شده‌اند از نظر بیزینسی منطقی‌اند؟

### Job-signal quality
آیا Job Titleهای پیشنهادی واقعاً نشانه نیاز به خدمات ما هستند؟

## Negative Cases

- Privacy page نباید service محسوب شود.
- Careers page در Phase 2 نباید business service را خراب کند.
- متن footer نباید buyer role بسازد.
- نبود اطلاعات باید `[]` یا `null` بدهد، نه حدس.

---

# 6. Phase 3 — Discovery Engine

Phase 3 را فقط با این معیار تست نکنیم که URL پیدا شده.

## Golden Discovery Cases

برای هر WebsiteProfile، انسان 5 تا 15 query خوب تعریف کند.

سپس Query Generator را با Expected Query Intent مقایسه کنیم.

لازم نیست متن query دقیقاً یکسان باشد؛ باید intent درست باشد.

## معیارهای Human Review

- آیا query مرتبط است؟
- آیا job title درست است؟
- آیا کشور درست وارد شده؟
- آیا ATS queryها منطقی‌اند؟
- آیا query بیش از حد broad است؟
- آیا query احتمالاً noise زیادی می‌دهد؟

## Live Search Evaluation

برای تعداد محدودی query واقعی، Top-K resultها manual review شوند.

ثبت شود:

```text
Top 10 Results
Relevant Results = X
Irrelevant Results = Y
Duplicate Results = Z
```

و Metric ساده:

```text
Precision@10 = relevant_results / 10
```

این Metric برای مقایسه نسخه‌های Query Generator بسیار مفید است.

## Negative Tests

- UTM duplicates
- fragment duplicates
- unrelated LinkedIn/profile pages
- generic blog pages
- privacy/terms
- unrelated jobs with same keyword

---

# 7. Phase 4 — Job Extraction

در این Phase accuracy باید با Source اصلی مقایسه شود.

## Golden Job Set

حداقل:

- 3 Lever
- 3 Greenhouse
- 3 Ashby
- 5 Generic JobPosting / JSON-LD
- چند page خراب یا ناقص

برای هر Job قبل از تست Expected Ground Truth ثبت شود:

```text
company
job title
location
employment type
posted date
technologies explicitly present
seniority
remote status
```

## Evidence QA

هر `relevant_signal` باید Evidence داشته باشد.

Human reviewer باید بتواند Evidence را در متن واقعی Job پیدا کند.

اگر Evidence در source نیست:

```text
FAIL — hallucinated evidence
```

## Critical Negative Tests

- future posted date
- missing company
- missing title
- malformed JSON-LD
- job page redirected to careers homepage
- expired job
- duplicate job URL
- unrelated job with common technology keyword

---

# 8. Phase 5 — Lead Aggregation & Scoring

اینجا فقط Unit Test کافی نیست.

باید چند Scenario دستی بسازیم که نتیجه scoring از قبل توسط انسان تعریف شده باشد.

## Golden Scoring Scenarios

### Strong Lead
مثال:
- 4 relevant jobs
- 2 recent
- 1 leadership role
- multiple matching technologies
- multiple independent evidence signals

Expected:
- Qualified
- Score در محدوده بالا

### Weak Lead
مثال:
- 1 old job
- weak relevance
- no leadership
- no strong evidence

Expected:
- Low score
- Not qualified

### Mixed Lead
مثال:
- 1 relevant old job
- 3 unrelated recent jobs

Expected:
- unrelated jobs نباید Recency یا Intent را بالا ببرند.

## Identity Cases

حتماً تست شود:

- same domain -> merge
- same source_company_key -> merge
- same normalized name + different domains -> DO NOT MERGE
- no identity -> DO NOT merge unrelated unknown companies

## Explainability

هر Score باید breakdown داشته باشد.

README باید نشان دهد:

```text
Fit Score:      24/30
Intent Score:   21/30
Recency Score:  15/20
Evidence Score: 18/20
Total:          78/100
```

و دلیل هر component ثبت شود.

---

# 9. Phase 6 — Company & Contact Enrichment

این Phase از نظر داده حساس‌تر است و باید audit خیلی شفاف داشته باشد.

## Company QA

برای چند شرکت واقعی manually verify شود:

```text
company name
domain
industry
employee range
country
website
```

اگر provider اطلاعات متفاوت داد، Source و اختلاف در README ثبت شود.

## Buyer Role QA

برای هر CompanyLead:

- buyer_roles_requested
- contacts_found
- best_contact
- reason_for_selection

باید در report دیده شود.

## Contact QA

برای هر Contact:

```text
name
title
company
work email
email status
provider
source
score
```

## Critical checks

- Contact متعلق به شرکت اشتباه نباشد.
- Same name در دو شرکت merge نشود.
- personal email وارد pipeline نشود.
- phone/mobile به صورت پیش‌فرض ذخیره نشود.
- `confidence` به تنهایی `verified` نشود.
- provider failure با `not_found` قاطی نشود.
- verified email source مشخص باشد.

---

# 10. AI-Blind-Spot Tests

این بخش برای گرفتن خطاهایی است که AI code reviewer یا generated tests معمولاً از دست می‌دهد.

## Business-semantic Errors

- technically valid but irrelevant lead
- buyer role exists but is wrong decision maker
- company has jobs, but not indicating demand for our service
- technology keyword appears only incidentally
- hiring activity is stale

## Identity Errors

- company subsidiaries incorrectly merged
- companies with same name incorrectly merged
- same company split across ATS and company domain
- same person at two employers incorrectly merged

## Evidence Errors

- signal has no textual support
- evidence points to wrong job
- duplicate evidence inflates confidence

## Provider Errors

- rate limit reported as empty result
- auth failure reported as no contacts
- stale provider data overwrites better first-party data

## Scoring Errors

- unrelated recent jobs increase score
- duplicate URLs increase score
- unknown/future date treated as recent
- low-quality evidence receives same weight as multiple independent signals

---

# 11. Human Review Status

هر Golden Case باید یکی از این وضعیت‌ها را داشته باشد:

```text
PASS
FAIL
REVIEW
SOURCE_CHANGED
NOT_RUN
```

`REVIEW` یعنی خروجی technically valid است ولی نیاز به تصمیم انسانی دارد.

---

# 12. README-TEST.md Output Format

بعد از هر QA Run، فایل `README-TEST.md` باید به‌صورت خودکار آپدیت شود.

ساختار پیشنهادی:

```markdown
# SalesAI Test Report

## Run Information

Date:
Commit:
Python:
Sales Model:
Embedding Model:
Providers:
Live Tests Enabled:

## Summary

Total:
Passed:
Failed:
Review:
Skipped:
Source Changed:

## Phase Summary

| Phase | Cases | Pass | Fail | Review |
|------|------:|-----:|-----:|------:|
| 1 | ... | ... | ... | ... |
| 2 | ... | ... | ... | ... |
...

## Detailed Cases

### P4-JOB-001 — Greenhouse BIM Manager

Source:
https://...

Snapshot:
tests/golden/snapshots/...

Input:
...

Expected:
...

Actual:
...

Result:
PASS

Differences:
None

Human Notes:
...

## Failures

...

## Review Required

...

## Live Source Changes

...

## Conclusion

...
```

---

# 13. Source Traceability Requirement

برای هر Golden / Live Case باید مشخص باشد:

```text
source_url
source_type
captured_at
snapshot_path
provider
```

در README لینک یا path منبع نمایش داده شود.

برای Provider API responseها، secret/API key هرگز ذخیره نشود.

Raw response فقط در صورت نیاز و با حذف اطلاعات حساس snapshot شود.

---

# 14. Expected vs Actual Requirement

برای هر Case باید Expected و Actual جدا ثبت شوند.

مثال:

```text
Expected:
Company: Example GmbH
Title: BIM Manager
Location: Berlin
Technology: Revit

Actual:
Company: Example GmbH
Title: BIM Manager
Location: Berlin
Technology: Revit, AutoCAD

Result:
REVIEW

Reason:
AutoCAD in source exists, but was not part of original golden expectation.
Golden dataset may need update.
```

این مهم است چون همیشه اختلاف به معنی bug نیست؛ گاهی Golden expectation ناقص است.

---

# 15. Test Run Modes

سه Mode تعریف شود:

```text
unit
acceptance
live
```

### unit
بدون اینترنت / Ollama / Providers

### acceptance
Golden snapshots + deterministic checks

### live
واقعاً اینترنت، Ollama، Brave/Apollo/Hunter در صورت config بودن

Live tests باید optional باشند.

---

# 16. Suggested Commands

```powershell
# Unit tests
python -m pytest tests/ -v -m "not integration and not live"

# Acceptance / Golden tests
python -m pytest tests/acceptance -v

# Live tests
python -m pytest tests/ -v -m "live"

# Everything available
python -m pytest tests/ -v
```

---

# 17. Definition of "Phase Complete"

یک Phase فقط وقتی Complete است که:

1. Unit tests پاس شوند.
2. Golden acceptance cases پاس شوند.
3. Negative cases پاس شوند.
4. Human-review cases بررسی شوند.
5. Source / Expected / Actual در README-TEST ثبت شده باشد.
6. هیچ High-Severity unresolved failure نداشته باشد.
7. تصمیمات AI قابل audit باشند.

صرفاً `pytest = green` برای Complete بودن Phase کافی نیست.

---

# 18. Minimum Golden Dataset Before Production

قبل از استفاده واقعی برای outreach، پیشنهاد حداقلی:

```text
Phase 2: 10 websites
Phase 3: 10 WebsiteProfiles / query sets
Phase 4: 15-20 real job postings
Phase 5: 10 company aggregation scenarios
Phase 6: 10 company/contact enrichment scenarios
```

بعداً این dataset با هر bug واقعی که پیدا می‌کنیم بزرگ‌تر شود.

هر Bug واقعی باید تبدیل شود به یک Regression Test دائمی.

---

# 19. Bug-to-Test Rule

وقتی یک bug واقعی پیدا شد:

```text
Bug
↓
Create minimal reproducible fixture
↓
Add Expected behavior
↓
Add regression test
↓
Fix code
↓
README-TEST records PASS
```

هیچ bug مهمی نباید فقط fix شود و بدون regression test رها شود.

---

# 20. Final QA Principle

SalesAI باید برای هر تصمیم مهم پاسخ این سؤال را قابل مشاهده نگه دارد:

> «این نتیجه از کجا آمد و چرا سیستم به این نتیجه رسید؟»

اگر نتوانیم Source، Input، Evidence، Expected و Actual را ببینیم، آن بخش هنوز production-grade QA ندارد.
