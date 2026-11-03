import re
import uuid
from typing import Optional, Tuple
from pydantic import BaseModel

from sales_engine.candidates.candidate_store import CandidateStore, canonicalize_domain


class RespondentResolutionResult(BaseModel):
    is_original_recipient: bool
    original_recipient_email: Optional[str] = None
    actual_respondent_email: str
    actual_respondent_name: Optional[str] = None
    actual_respondent_contact_id: Optional[str] = None
    created_new_contact: bool = False


class RespondentResolver:
    """
    Resolves inbound email sender identity against original outbound recipient.
    Preserves provenance and creates new ContactCandidate when a colleague replies,
    strictly adhering to Phase 6 domain identity rules without same-name merging.
    """

    def __init__(self, candidate_store: CandidateStore):
        self.candidate_store = candidate_store

    def resolve(
        self,
        company_id: Optional[str],
        from_email: str,
        from_name: Optional[str] = None,
        original_contact_id: Optional[str] = None,
        original_recipient_email: Optional[str] = None,
    ) -> RespondentResolutionResult:
        clean_from = from_email.strip().lower() if from_email else ""
        clean_orig = original_recipient_email.strip().lower() if original_recipient_email else ""

        # 1. Exact match with original recipient
        if clean_orig and clean_from == clean_orig:
            return RespondentResolutionResult(
                is_original_recipient=True,
                original_recipient_email=clean_orig,
                actual_respondent_email=clean_from,
                actual_respondent_name=from_name,
                actual_respondent_contact_id=original_contact_id,
                created_new_contact=False,
            )

        # 2. Check if original contact has this email
        if original_contact_id:
            with self.candidate_store._get_connection() as conn:
                row = conn.execute(
                    "SELECT work_email, full_name FROM candidate_contacts WHERE contact_id = ? LIMIT 1",
                    (original_contact_id,),
                ).fetchone()
                if row and row["work_email"] and row["work_email"].strip().lower() == clean_from:
                    return RespondentResolutionResult(
                        is_original_recipient=True,
                        original_recipient_email=clean_from,
                        actual_respondent_email=clean_from,
                        actual_respondent_name=from_name or row["full_name"],
                        actual_respondent_contact_id=original_contact_id,
                        created_new_contact=False,
                    )

        # 3. Colleague / different respondent
        # Search if another contact already exists with this exact email under this company
        existing_contact_id = None
        if company_id and clean_from:
            with self.candidate_store._get_connection() as conn:
                row = conn.execute(
                    "SELECT contact_id FROM candidate_contacts WHERE candidate_id = ? AND LOWER(work_email) = ? LIMIT 1",
                    (company_id, clean_from),
                ).fetchone()
                if row:
                    existing_contact_id = row["contact_id"]

        if existing_contact_id:
            return RespondentResolutionResult(
                is_original_recipient=False,
                original_recipient_email=clean_orig,
                actual_respondent_email=clean_from,
                actual_respondent_name=from_name,
                actual_respondent_contact_id=existing_contact_id,
                created_new_contact=False,
            )

        # 4. Create new contact record under the company if company domain matches
        created_new = False
        new_contact_id = None

        if company_id and clean_from:
            comp = self.candidate_store.get_candidate(company_id)
            comp_domain = canonicalize_domain(comp.company_domain) if comp else None
            sender_domain = canonicalize_domain(clean_from.split("@")[-1]) if "@" in clean_from else None

            # Verify sender domain aligns with company domain or company is known
            if comp_domain and sender_domain and (comp_domain == sender_domain or comp_domain.endswith(f".{sender_domain}") or sender_domain.endswith(f".{comp_domain}")):
                # Valid colleague respondent
                new_contact_id = f"cnt_{uuid.uuid4().hex[:12]}"
                name_parts = (from_name or "").strip().split(maxsplit=1)
                first_name = name_parts[0] if name_parts else clean_from.split("@")[0].capitalize()
                last_name = name_parts[1] if len(name_parts) > 1 else ""
                full_name = from_name.strip() if from_name else f"{first_name} {last_name}".strip()

                with self.candidate_store._get_connection() as conn:
                    conn.execute(
                        """
                        INSERT INTO candidate_contacts (
                            contact_id, candidate_id, first_name, last_name, full_name,
                            job_title, work_email, email_status, buyer_role_match, contact_score
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_contact_id,
                            company_id,
                            first_name,
                            last_name,
                            full_name,
                            "Colleague / Inbound Respondent",
                            clean_from,
                            "verified",
                            "inbound_respondent",
                            85,
                        ),
                    )
                created_new = True

        return RespondentResolutionResult(
            is_original_recipient=False,
            original_recipient_email=clean_orig,
            actual_respondent_email=clean_from,
            actual_respondent_name=from_name,
            actual_respondent_contact_id=new_contact_id,
            created_new_contact=created_new,
        )
