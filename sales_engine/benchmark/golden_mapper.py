"""
sales_engine/benchmark/golden_mapper.py
Deterministic converter and mapping layer for SalesAI Golden 10 Synthetic dataset.
Maps compatible legacy scenarios into the S2/S3 7-task Gateway taxonomy while
explicitly preserving expected meaning and flagging unmappable cases.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import json


@dataclass(frozen=True)
class MappedGoldenCase:
    """A Golden 10 case mapped to canonical Gateway task taxonomy."""

    golden_id: str
    legacy_task: str
    canonical_task: str
    canonical_subtask: Optional[str]
    group: str  # CLEAN or MISLEADING
    title: str
    input_text: str
    request_instruction: str
    expected_data: Dict[str, Any]
    never_claim: List[str]
    checks: str
    is_mappable: bool
    unmappable_reason: Optional[str] = None


class GoldenDatasetMapper:
    """
    Loads SalesAI_Golden_10_Synthetic_v1.json, verifies integrity,
    and maps compatible cases to the 7 canonical Gateway task paths.
    """

    # Explicit, defensible mapping table
    # Legacy Task -> (canonical_task, canonical_subtask)
    MAPPING_TABLE: Dict[str, tuple[str, Optional[str]]] = {
        "company_extraction": ("website_extraction", "pass_a"),
        "company_qualification": ("job_analysis", None),
        "email_draft": ("outreach_draft", None),
        "reply_classification": ("inbox_classification", None),
        # meeting_time_extraction is deliberately NOT mapped to any LLM task
    }

    def __init__(self, golden_json_path: Optional[Path] = None):
        if golden_json_path is None:
            self.golden_path = Path("docs/architecture/SalesAI_Golden_10_Synthetic_v1.json")
        else:
            self.golden_path = Path(golden_json_path)

    def load_and_map(self) -> tuple[List[MappedGoldenCase], List[MappedGoldenCase]]:
        """
        Load and map all Golden 10 cases.
        Returns: (mappable_cases, unmappable_cases)
        """
        if not self.golden_path.exists():
            raise FileNotFoundError(f"Golden dataset file not found at: {self.golden_path}")

        raw_data = json.loads(self.golden_path.read_text(encoding="utf-8"))
        cases = raw_data.get("cases", [])

        mappable: List[MappedGoldenCase] = []
        unmappable: List[MappedGoldenCase] = []

        for case in cases:
            cid = case.get("id", "")
            legacy_task = case.get("task", "")
            group = case.get("group", "CLEAN")
            title = case.get("title", "")
            input_text = case.get("input", "")
            request_instr = case.get("request", "")
            expected = case.get("expected", {})
            never_claim = expected.get("never_claim", [])
            checks = case.get("checks", "")

            if legacy_task in self.MAPPING_TABLE:
                canon_task, canon_subtask = self.MAPPING_TABLE[legacy_task]
                mapped = MappedGoldenCase(
                    golden_id=cid,
                    legacy_task=legacy_task,
                    canonical_task=canon_task,
                    canonical_subtask=canon_subtask,
                    group=group,
                    title=title,
                    input_text=input_text,
                    request_instruction=request_instr,
                    expected_data=expected,
                    never_claim=never_claim,
                    checks=checks,
                    is_mappable=True,
                    unmappable_reason=None,
                )
                mappable.append(mapped)
            else:
                unmapped = MappedGoldenCase(
                    golden_id=cid,
                    legacy_task=legacy_task,
                    canonical_task="unmappable",
                    canonical_subtask=None,
                    group=group,
                    title=title,
                    input_text=input_text,
                    request_instruction=request_instr,
                    expected_data=expected,
                    never_claim=never_claim,
                    checks=checks,
                    is_mappable=False,
                    unmappable_reason=(
                        f"Legacy task '{legacy_task}' has no corresponding external LLM Gateway contract "
                        "in SalesAI S2/S3 canonical taxonomy. Meeting scheduling is handled by internal application logic."
                    ),
                )
                unmappable.append(unmapped)

        return mappable, unmappable
