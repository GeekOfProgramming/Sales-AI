from typing import List, Dict, Any, Optional

class BuyerRoleRanker:
    """Deterministically evaluates contact relevance based on title matching."""
    
    DEFAULT_ROLES = [
        "BIM Manager",
        "Head of BIM",
        "Digital Delivery Manager",
        "VDC Director",
        "Digital Construction Manager",
        "CTO"
    ]
    
    def __init__(self, custom_roles: Optional[List[str]] = None):
        self.target_roles = [r.lower().strip() for r in (custom_roles or self.DEFAULT_ROLES)]
        
    def evaluate(self, title: str) -> Dict[str, Any]:
        """Returns match type and score bonus for a title."""
        if not title:
            return {"match": "none", "score": 0}
            
        title_lower = title.lower().strip()
        
        # Exact Match
        if title_lower in self.target_roles:
            return {"match": "exact", "score": 40}
            
        # Strong Normalized Match (contains full target role)
        for role in self.target_roles:
            if role in title_lower:
                return {"match": "strong", "score": 30}
                
        # Relevant Department / Broad Match
        relevant_keywords = ["bim", "vdc", "digital delivery", "digital construction"]
        for kw in relevant_keywords:
            if kw in title_lower:
                return {"match": "relevant", "score": 20}
                
        # Weak / Leadership (e.g. CEO, CTO)
        leadership = ["cto", "cio", "director", "vp", "head of"]
        for l in leadership:
            if l in title_lower:
                return {"match": "weak", "score": 10}
                
        return {"match": "none", "score": 0}
