import json
import os
import sys
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

def get_git_commit(project_root: Path) -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=project_root)
        return out.decode().strip()
    except Exception:
        return "Unknown"

def get_ollama_version() -> str:
    try:
        req = urllib.request.Request("http://localhost:11434/api/version", headers={"User-Agent": "SalesAI-QA"})
        with urllib.request.urlopen(req, timeout=1) as resp:
            data = json.loads(resp.read().decode())
            return data.get("version", "running")
    except Exception:
        return "Not detected / unreachable"

def compute_differences(expected, actual):
    """Compute basic differences between expected and actual dicts if not provided."""
    diffs = []
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        if expected != actual:
            diffs.append(f"Expected `{expected}` but got `{actual}`")
        return diffs
        
    for k, v in expected.items():
        if k not in actual:
            diffs.append(f"Missing key in actual: `{k}`")
        elif actual[k] != v:
            # Special check for lists
            if isinstance(v, list) and isinstance(actual[k], list):
                missing = [item for item in v if item not in actual[k]]
                extra = [item for item in actual[k] if item not in v]
                if missing:
                    diffs.append(f"Field `{k}` missing expected items: {missing}")
                if extra:
                    diffs.append(f"Field `{k}` contains extra items: {extra}")
            else:
                diffs.append(f"Field `{k}` mismatch: expected `{v}`, got `{actual[k]}`")
                
    for k in actual:
        if k not in expected:
            diffs.append(f"Extra key in actual: `{k}`")
            
    return diffs

def generate_report():
    project_root = Path(__file__).parent.parent
    report_file = project_root / "tests" / "reports" / "latest_test_report.json"
    readme_file = project_root / "README-TEST.md"
    
    if not report_file.exists():
        print("No test report found. Run tests first.")
        return
        
    with open(report_file, "r", encoding="utf-8") as f:
        results = json.load(f)
        
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit(project_root)
    ollama_ver = get_ollama_version()
    llm_model = os.environ.get("SALES_LLM_MODEL", "qwen2.5:1.5b (default)")
    has_live = any(str(r.get("source", "")).startswith("http") and r.get("status") != "NOT_RUN" for r in results)
    
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
        f"- **Date:** {now.strftime('%Y-%m-%d')}",
        f"- **Time:** {now.strftime('%H:%M:%S')} UTC",
        f"- **Git Commit:** `{git_commit}`",
        f"- **Python Version:** `{sys.version.split()[0]}`",
        f"- **SALES_LLM_MODEL:** `{llm_model}`",
        f"- **Ollama Version:** `{ollama_ver}`",
        f"- **Brave enabled?** {'Yes' if os.environ.get('BRAVE_API_KEY') else 'No'}",
        f"- **Apollo enabled?** {'Yes' if os.environ.get('APOLLO_API_KEY') else 'No'}",
        f"- **Hunter enabled?** {'Yes' if os.environ.get('HUNTER_API_KEY') else 'No'}",
        f"- **Live Tests:** {'Yes' if has_live else 'No'}",
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
    lines.append("```json\n" + json.dumps(r.get("input", {}), indent=2, ensure_ascii=False) + "\n```")
    
    lines.append("**Expected:**")
    lines.append("```json\n" + json.dumps(r.get("expected", {}), indent=2, ensure_ascii=False) + "\n```")
    
    lines.append("**Actual:**")
    actual = r.get("actual")
    if actual and status != "NOT_RUN":
        lines.append("```json\n" + json.dumps(actual, indent=2, ensure_ascii=False) + "\n```")
    else:
        lines.append("_(Not executed yet)_\n")
        
    lines.append(f"**Result:** {status}")
    
    # Calculate or retrieve differences
    diffs = r.get("differences", [])
    if not diffs and status not in ["NOT_RUN"] and actual:
        diffs = compute_differences(r.get("expected", {}), actual)
        
    lines.append("\n**Differences:**")
    if diffs:
        for diff in diffs:
            if diff.startswith("**") and diff.endswith("**"):
                lines.append(f"\n{diff}")
            elif diff.startswith("- "):
                lines.append(diff)
            else:
                lines.append(f"- {diff}")
    else:
        if status == "NOT_RUN":
            lines.append("_(Pending execution)_")
        else:
            lines.append("_(None / In sync)_")
            
    if r.get("reason"):
        lines.append(f"\n**Reason:** {r.get('reason')}")
        
    if r.get("human_notes"):
        lines.append(f"\n**Human Notes:**\n{r.get('human_notes')}")
        
    lines.append("\n---")
    return "\n".join(lines)

if __name__ == "__main__":
    generate_report()
