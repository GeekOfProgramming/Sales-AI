from typing import List, Dict, Any, Optional

class BuyerRoleRanker:
    """Deterministically evaluates contact relevance based on title matching."""
    
    DEFAULT_ROLES = []
    
    def __init__(self, custom_roles: Optional[List[str]] = None):
        if custom_roles:
            self.target_roles = [r.lower().strip() for r in custom_roles]
        else:
            self.target_roles = []
            
    def evaluate(self, title: str) -> Dict[str, Any]:
        """Returns match type and score bonus for a title."""
        if not title:
            return {"match": "none", "score": 0}
            
        title_lower = title.lower().strip()
        
        if not self.target_roles:
            # Neutral generic fallback strategy: give slight boost to leadership/decision makers
            generic_leadership = ["ceo", "cto", "cio", "founder", "vp", "president", "director", "head"]
            for l in generic_leadership:
                if l in title_lower:
                    return {"match": "relevant", "score": 15}
            return {"match": "none", "score": 0}
            
        # Exact Match
        if title_lower in self.target_roles:
            return {"match": "exact", "score": 40}
            
        # Strong Normalized Match (contains full target role)
        for role in self.target_roles:
            if role in title_lower:
                return {"match": "strong", "score": 30}
                
        # Weak / Leadership (generic fallback even with target roles)
        leadership = ["cto", "cio", "director", "vp", "head"]
        for l in leadership:
            if l in title_lower:
                return {"match": "weak", "score": 10}
                
        return {"match": "none", "score": 0}
