import re

class CompanyNormalizer:
    # Common legal suffixes to strip for deduplication
    LEGAL_SUFFIXES = [
        r'\bgmbh\b', r'\bltd\b', r'\blimited\b', r'\bllc\b', r'\binc\b',
        r'\bs\.r\.l\.', r'\bsrl\b', r'\bb\.v\.', r'\bbv\b', r'\bplc\b',
        r'\bcorp\b', r'\bcorporation\b'
    ]
    
    @classmethod
    def normalize(cls, company_name: str) -> str:
        if not company_name:
            return ""
            
        # Lowercase
        normalized = company_name.lower()
        
        # Strip legal suffixes
        for suffix in cls.LEGAL_SUFFIXES:
            normalized = re.sub(suffix, '', normalized, flags=re.IGNORECASE)
            
        # Strip punctuation
        normalized = re.sub(r'[^\w\s]', '', normalized)
        
        # Normalize whitespace
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        return normalized
