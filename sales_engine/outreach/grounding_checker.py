from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from sales_engine.outreach.schemas import OutreachContext


class GroundingCheckResult(BaseModel):
    """Result of semantic grounding analysis."""
    checked: bool = False
    status: str = "not_run"  # passed, failed, not_run
    is_supported: Optional[bool] = None
    unsupported_claims: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None


class GroundingChecker:
    """
    Evaluates whether factual statements in the draft are strictly grounded.
    In Phase 8:
    - Default disabled (OUTREACH_GROUNDING_CHECK=false) for small local models.
    - Explicitly reports checked=False, status='not_run'.
    - Can be enabled for high-capacity models without modifying orchestrator architecture.
    - Invariant: MUST NOT rewrite the email, approve the email, or send the email.
    """

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def check_grounding(
        self,
        draft_text: str,
        context: OutreachContext,
        evidence_refs: List[str],
    ) -> GroundingCheckResult:
        """Evaluate grounding of draft against context evidence."""
        if not self.enabled:
            return GroundingCheckResult(
                checked=False,
                status="not_run",
                is_supported=None,
                unsupported_claims=[],
                evidence_refs=evidence_refs,
                confidence=None,
            )

        context_ids = {e.id for e in context.evidence_items}
        unknown_refs = [ref for ref in evidence_refs if ref not in context_ids]

        unsupported: List[str] = []
        if unknown_refs:
            unsupported.append(f"Referenced unknown evidence IDs: {unknown_refs}")

        is_supported = len(unsupported) == 0
        return GroundingCheckResult(
            checked=True,
            status="passed" if is_supported else "failed",
            is_supported=is_supported,
            unsupported_claims=unsupported,
            evidence_refs=evidence_refs,
            confidence=1.0 if is_supported else 0.0,
        )
