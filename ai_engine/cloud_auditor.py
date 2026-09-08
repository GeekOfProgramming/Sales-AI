"""Local RAG Auditor for Cloud BIM Models (Autodesk Construction Cloud - ACC).
Performs read-only compliance checks against ISO 19650 standards and mandatory BIM parameters
without opening Revit or writing directly to the cloud project.
"""

import re
from datetime import datetime, timezone
from typing import Dict, Any, List


class CloudBIMAuditor:
    """Read-only compliance auditor verifying cloud BIM metadata against ISO 19650 rules."""

    # ISO 19650 Container standard: <Project>-<Originator>-<Volume>-<Level>-<Type>-<Role>-<Number>
    ISO_CONTAINER_REGEX = re.compile(
        r"^[A-Z0-9]{3,6}-[A-Z0-9]{2,4}-[A-Z0-9]{2}-[A-Z0-9]{2}-[A-Z0-9]{2}-[A-Z0-9]{1,2}-[0-9]{4}",
        re.IGNORECASE,
    )

    def __init__(self, rag_retriever=None):
        self.rag_retriever = rag_retriever

    def audit_model_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Perform comprehensive read-only audit on extracted cloud model metadata."""
        model_name = metadata.get("model_name", "Unknown_Model.rvt")
        elements = metadata.get("elements_sample", [])
        total_elements = metadata.get("total_elements", len(elements))

        issues: List[Dict[str, Any]] = []
        passed_checks = 0
        total_checks = 0

        # Check 1: ISO 19650 Model Filename Standard
        total_checks += 1
        base_name = model_name.replace(".rvt", "").split("_")[0]
        if self.ISO_CONTAINER_REGEX.match(base_name):
            passed_checks += 1
        else:
            issues.append({
                "element_id": None,
                "category": "Project Container",
                "parameter": "Filename",
                "issue_type": "ISO 19650 Naming Convention Violation",
                "current_value": model_name,
                "proposed_value": "PRJ-ZZ-00-M3-A-0001_Architecture.rvt",
                "severity": "HIGH",
                "description": "Model filename does not conform to ISO 19650 standard naming structure.",
            })

        # Check 2: Element Parameter Auditing (FireRating, OmniClass, Family Naming)
        for elem in elements:
            elem_id = elem.get("element_id")
            category = elem.get("category", "")
            family = elem.get("family_name", "")
            type_name = elem.get("type_name", "")
            params = elem.get("parameters", {})

            # Rule: Walls and Doors must have FireRating
            if category in ("OST_Walls", "OST_Doors"):
                total_checks += 1
                fire_rating = str(params.get("FireRating", "")).strip()
                if not fire_rating or fire_rating.lower() in ("none", "unrated", ""):
                    issues.append({
                        "element_id": elem_id,
                        "category": category,
                        "parameter": "FireRating",
                        "issue_type": "Missing Mandatory Parameter",
                        "current_value": fire_rating or "[EMPTY]",
                        "proposed_value": "2 Hours" if category == "OST_Walls" else "1 Hour",
                        "severity": "MEDIUM",
                        "description": f"Mandatory parameter 'FireRating' is not defined for {type_name}.",
                    })
                else:
                    passed_checks += 1

            # Rule: Elements should have Classification (OmniClass or AssemblyCode)
            total_checks += 1
            omniclass = str(params.get("OmniClass", "")).strip()
            assembly = str(params.get("AssemblyCode", "")).strip()
            if not omniclass and not assembly:
                issues.append({
                    "element_id": elem_id,
                    "category": category,
                    "parameter": "OmniClass / AssemblyCode",
                    "issue_type": "Missing Classification Code",
                    "current_value": "[EMPTY]",
                    "proposed_value": "23.10.10.00" if category == "OST_Walls" else "23.30.10.10",
                    "severity": "LOW",
                    "description": f"No BIM classification code (OmniClass/UniFormat) assigned to {type_name}.",
                })
            else:
                passed_checks += 1

            # Rule: Family Naming Standards (no 'Custom_' or 'temp' prefixes)
            total_checks += 1
            if family.lower().startswith("custom_") or "temp" in family.lower():
                issues.append({
                    "element_id": elem_id,
                    "category": category,
                    "parameter": "FamilyName",
                    "issue_type": "Non-Standard Family Naming",
                    "current_value": family,
                    "proposed_value": family.replace("Custom_", "STD_").replace("_NonStandard", ""),
                    "severity": "LOW",
                    "description": f"Family name '{family}' violates firm BIM object library naming conventions.",
                })
            else:
                passed_checks += 1

        # Calculate compliance score
        compliance_pct = round((passed_checks / max(total_checks, 1)) * 100, 1)

        return {
            "model_name": model_name,
            "urn": metadata.get("urn", ""),
            "total_elements_audited": total_elements,
            "total_checks_evaluated": total_checks,
            "compliance_score": compliance_pct,
            "status": "COMPLIANT" if compliance_pct >= 90 else ("NEEDS_REVIEW" if compliance_pct >= 60 else "NON_COMPLIANT"),
            "audit_mode": "READ_ONLY",
            "requires_human_approval": True,
            "issues": issues,
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "summary_notes": f"Read-Only audit complete. Evaluated {total_checks} quality checkpoints. Found {len(issues)} parameter discrepancy issues.",
        }


# Global instance
cloud_auditor = CloudBIMAuditor()
