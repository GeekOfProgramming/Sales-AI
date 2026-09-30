"""
QA Validator Engine
Ensures strict contractual integrity between Golden Expected and Actual test outputs.
No case is permitted to PASS if any declared Expected field does not match Actual.
"""
from typing import Any, Dict, List, Set, Tuple

def validate_expected_vs_actual(
    expected: Any, 
    actual: Any, 
    ignored_keys: Set[str] = None
) -> List[str]:
    """
    Validates that every contractually declared key in expected matches actual.
    
    Supported validation semantics:
    1. Primitive equality: expected == actual
    2. Range checks: e.g. "fit_range": [0, 30] checks that actual metric is between 0 and 30
    3. Min/Max constraints: e.g. "min_primary_signals_covered": 3, "max_duplicates": 0
    4. Required list containment: e.g. "required_countries": ["Italy", "Germany"]
    5. List containment/equality: order-independent equality for set-like lists
    6. Nested dict validation
    
    Returns:
        List of difference strings. If empty, validation succeeded.
    """
    diffs = []
    ignored = ignored_keys or set()
    system_metadata_keys = {
        "ground_truth_version", 
        "enrichment_rules_version", 
        "scoring_rules_version",
        "provider_contract_version", 
        "human_approved",
        "diagnostic_only"
    }
    
    if not isinstance(expected, dict):
        if expected != actual:
            diffs.append(f"Expected `{expected}` but got `{actual}`")
        return diffs

    if not isinstance(actual, dict):
        diffs.append(f"Expected dictionary but got `{type(actual).__name__}`")
        return diffs

    for k, exp_val in expected.items():
        if k in ignored or k in system_metadata_keys:
            continue

        # -------------------------------------------------------------
        # 1. Range validation (e.g., "fit_range": [0, 30])
        # -------------------------------------------------------------
        if k.endswith("_range") and isinstance(exp_val, (list, tuple)) and len(exp_val) == 2:
            base_key = k[:-6]
            score_key = f"{base_key}_score"
            
            actual_val = None
            if k in actual:
                actual_val = actual[k]
            elif score_key in actual:
                actual_val = actual[score_key]
            elif base_key in actual:
                actual_val = actual[base_key]
            elif base_key == "total" and "lead_score" in actual:
                actual_val = actual["lead_score"]

            if actual_val is None:
                diffs.append(f"Missing metric for range check `{k}`: expected value in [{exp_val[0]}, {exp_val[1]}]")
            elif not (exp_val[0] <= actual_val <= exp_val[1]):
                diffs.append(f"Field `{k}` value {actual_val} is out of expected range [{exp_val[0]}, {exp_val[1]}]")
            continue

        # -------------------------------------------------------------
        # 2. Key resolution in actual (top-level or metrics subdict)
        # -------------------------------------------------------------
        act_val = None
        key_found = False
        
        if k in actual:
            act_val = actual[k]
            key_found = True
        elif "metrics" in actual and isinstance(actual["metrics"], dict) and k in actual["metrics"]:
            act_val = actual["metrics"][k]
            key_found = True
        elif k.startswith("min_") and "metrics" in actual and isinstance(actual["metrics"], dict):
            # E.g. min_primary_signals_covered -> primary_signals_covered_count or primary_signals_covered
            sub_k = k[4:]
            count_k = f"{sub_k}_count" if not sub_k.endswith("_count") else sub_k
            if count_k in actual["metrics"]:
                act_val = actual["metrics"][count_k]
                key_found = True
            elif sub_k in actual["metrics"]:
                val = actual["metrics"][sub_k]
                act_val = len(val) if isinstance(val, (list, set, dict)) else val
                key_found = True
        elif k.startswith("max_") and "metrics" in actual and isinstance(actual["metrics"], dict):
            # E.g. max_duplicates -> duplicate_queries or duplicate_count
            sub_k = k[4:]
            if sub_k in actual["metrics"]:
                act_val = actual["metrics"][sub_k]
                key_found = True
            elif f"{sub_k}_queries" in actual["metrics"]:
                act_val = actual["metrics"][f"{sub_k}_queries"]
                key_found = True
            elif f"{sub_k}_pattern_count" in actual["metrics"]:
                act_val = actual["metrics"][f"{sub_k}_pattern_count"]
                key_found = True

        if not key_found:
            diffs.append(f"Missing key in actual: `{k}` (expected `{exp_val}`)")
            continue

        # -------------------------------------------------------------
        # 3. Min/Max constraint validation
        # -------------------------------------------------------------
        if k.startswith("min_") and isinstance(exp_val, (int, float)):
            if not isinstance(act_val, (int, float)) or act_val < exp_val:
                diffs.append(f"Field `{k}` value {act_val} < required minimum {exp_val}")
            continue

        if k.startswith("max_") and isinstance(exp_val, (int, float)):
            if not isinstance(act_val, (int, float)) or act_val > exp_val:
                diffs.append(f"Field `{k}` value {act_val} > allowed maximum {exp_val}")
            continue

        if k.endswith("_capped") and isinstance(exp_val, (int, float)):
            if not isinstance(act_val, (int, float)) or act_val > exp_val:
                diffs.append(f"Field `{k}` value {act_val} > allowed cap {exp_val}")
            continue

        # -------------------------------------------------------------
        # 4. Required list containment (e.g. required_countries)
        # -------------------------------------------------------------
        if k.startswith("required_") and isinstance(exp_val, list):
            # Check if all items in exp_val are in act_val
            target_list = act_val
            if isinstance(act_val, dict):
                target_list = list(act_val.keys())
            elif not isinstance(act_val, (list, set, tuple)):
                target_list = [act_val]
            
            missing_items = [item for item in exp_val if item not in target_list]
            if missing_items:
                diffs.append(f"Field `{k}` missing required elements: {missing_items}")
            continue

        # -------------------------------------------------------------
        # 5. List comparisons
        # -------------------------------------------------------------
        if isinstance(exp_val, list):
            if not isinstance(act_val, list):
                diffs.append(f"Field `{k}` expected list but got `{type(act_val).__name__}`")
            else:
                # Compare as sorted strings if primitive items
                exp_sorted = sorted([str(x) for x in exp_val])
                act_sorted = sorted([str(x) for x in act_val])
                if exp_sorted != act_sorted:
                    missing = [item for item in exp_val if item not in act_val]
                    extra = [item for item in act_val if item not in exp_val]
                    details = []
                    if missing:
                        details.append(f"missing: {missing}")
                    if extra:
                        details.append(f"extra: {extra}")
                    diffs.append(f"Field `{k}` list mismatch ({', '.join(details)}): expected `{exp_val}`, got `{act_val}`")
            continue

        # -------------------------------------------------------------
        # 6. Nested dictionary comparisons
        # -------------------------------------------------------------
        if isinstance(exp_val, dict):
            sub_diffs = validate_expected_vs_actual(exp_val, act_val, ignored)
            for sd in sub_diffs:
                diffs.append(f"In `{k}`: {sd}")
            continue

        # -------------------------------------------------------------
        # 7. Primitive equality
        # -------------------------------------------------------------
        if exp_val != act_val:
            diffs.append(f"Field `{k}` mismatch: expected `{exp_val}`, got `{act_val}`")

    return diffs
