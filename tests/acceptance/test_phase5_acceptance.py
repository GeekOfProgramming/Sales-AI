import json
import pytest
from pathlib import Path

def load_golden_cases(filename):
    p = Path(__file__).parent.parent / "golden" / filename
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.mark.acceptance
@pytest.mark.parametrize("case", load_golden_cases("phase5_leads.json"), ids=lambda c: c["case_id"])
def test_phase5_leads_golden(case, qa_logger):
    qa_logger.update(case)
    if case.get("status") == "NOT_RUN":
        pytest.skip("Placeholder")
