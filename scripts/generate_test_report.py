import json
import os
from datetime import datetime
from pathlib import Path

def generate_report():
    project_root = Path(__file__).parent.parent
    report_file = project_root / "tests" / "reports" / "latest_test_report.json"
    readme_file = project_root / "README-TEST.md"
    
    if not report_file.exists():
        print("No test report found. Run tests first.")
        return
        
    with open(report_file, "r", encoding="utf-8") as f:
        results = json.load(f)
        
    # Gather stats
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "PASS")
    failed = sum(1 for r in results if r.get("status") == "FAIL")
    review = sum(1 for r in results if r.get("status") == "REVIEW")
    skipped = sum(1 for r in results if r.get("status") == "NOT_RUN")
    source_changed = sum(1 for r in results if r.get("status") == "SOURCE_CHANGED")
    
    # Phase stats
    phases = {}
    for r in results:
        p = r.get("phase", "Unknown")
        if p not in phases:
            phases[p] = {"total": 0, "pass": 0, "fail": 0, "review": 0}
        phases[p]["total"] += 1
        st = r.get("status")
        if st == "PASS": phases[p]["pass"] += 1
        elif st == "FAIL": phases[p]["fail"] += 1
        elif st == "REVIEW": phases[p]["review"] += 1
        
    md = [
        "# SalesAI QA / Acceptance Test Report",
        "",
        "## Run Information",
        f"- **Date:** {datetime.utcnow().isoformat()[:10]}",
        f"- **Time:** {datetime.utcnow().isoformat()[11:19]} UTC",
        "",
        "## Summary",
        f"- **Total Cases:** {total}",
        f"- **Passed:** {passed}",
        f"- **Failed:** {failed}",
        f"- **Review Required:** {review}",
        f"- **Skipped / Not Run:** {skipped}",
        f"- **Source Changed:** {source_changed}",
        "",
        "## Phase Summary",
        "| Phase | Cases | Pass | Fail | Review |",
        "|---|---|---|---|---|"
    ]
    
    for p, stats in sorted(phases.items()):
        md.append(f"| {p} | {stats['total']} | {stats['pass']} | {stats['fail']} | {stats['review']} |")
        
    md.extend([
        "",
        "## Failures",
    ])
    
    fail_cases = [r for r in results if r.get("status") == "FAIL"]
    if not fail_cases:
        md.append("_No failures detected._")
    else:
        for r in fail_cases:
            md.append(format_case(r))
            
    md.extend([
        "",
        "## Human Review Required",
        "These cases executed successfully but require business logic confirmation, or represent ambiguous situations.",
        ""
    ])
    
    review_cases = [r for r in results if r.get("status") == "REVIEW"]
    if not review_cases:
        md.append("_No review required._")
    else:
        for r in review_cases:
            md.append(format_case(r))
            
    md.extend([
        "",
        "## Detailed Cases (All Results)",
        ""
    ])
    
    for r in results:
        if r.get("status") not in ["FAIL", "REVIEW"]:
            md.append(format_case(r))
            
    with open(readme_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
        
    print(f"Generated {readme_file}")

def format_case(r):
    case_id = r.get("case_id", "Unknown")
    title = r.get("title", "Unknown")
    phase = r.get("phase", "Unknown")
    status = r.get("status", "NOT_RUN")
    
    lines = [
        f"### {case_id} — {title}",
        f"**Phase:** {phase}",
        f"**Source:** {r.get('source', 'internal')}"
    ]
    
    lines.append("\n**Input:**")
    lines.append("```json\n" + json.dumps(r.get("input", {}), indent=2) + "\n```")
    
    lines.append("**Expected:**")
    lines.append("```json\n" + json.dumps(r.get("expected", {}), indent=2) + "\n```")
    
    if status != "NOT_RUN":
        lines.append("**Actual:**")
        lines.append("```json\n" + json.dumps(r.get("actual", {}), indent=2) + "\n```")
        
    lines.append(f"\n**Result:** {status}")
    
    if r.get("differences"):
        lines.append("\n**Differences / Reason:**")
        for diff in r.get("differences"):
            lines.append(f"- {diff}")
            
    if r.get("reason"):
        lines.append(f"\n**Reason:** {r.get('reason')}")
        
    if r.get("human_notes"):
        lines.append(f"\n**Human Notes:**\n{r.get('human_notes')}")
        
    lines.append("\n---")
    return "\n".join(lines)

if __name__ == "__main__":
    generate_report()
