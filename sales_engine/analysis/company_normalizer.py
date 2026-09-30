import re

class CompanyNormalizer:
    # Common legal suffixes to strip for deduplication
    LEGAL_SUFFIXES = [
        r'\bgmbh\b', r'\bltd\b', r'\blimited\b', r'\bllc\b', r'\binc\b',
        r'\bs\.r\.l\.', r'\bsrl\b', r'\bs\.p\.a\.', r'\bspa\b',
        r'\bb\.v\.', r'\bbv\b', r'\bplc\b',
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

    ATS_DOMAINS = {
        "lever.co", "greenhouse.io", "ashbyhq.com",
        "workable.com", "breezy.hr", "applytojob.com"
    }

    @classmethod
    def canonicalize_domain(cls, url_or_domain: str) -> str:
        """Canonicalize web domain for company identity deduplication.
        
        Normalizes scheme, case, path, port, trailing dot, and leading www.
        Preserves meaningful subdomains (e.g. careers.company.com).
        Returns None for ATS hosting platforms (lever.co, greenhouse.io, ashbyhq.com).
        """
        if not url_or_domain or not isinstance(url_or_domain, str):
            return None
            
        raw = url_or_domain.strip().lower()
        if not raw:
            return None
            
        # Parse URL or host
        if "://" in raw:
            # Strip scheme and path
            raw = raw.split("://", 1)[1]
        if "/" in raw:
            raw = raw.split("/", 1)[0]
            
        # Strip port if present
        if ":" in raw:
            raw = raw.split(":", 1)[0]
            
        # Strip trailing dots and whitespace
        raw = raw.strip(". \t\r\n")
        
        # Strip leading www.
        if raw.startswith("www."):
            raw = raw[4:]
            
        if not raw:
            return None
            
        # ATS domain safety: never allow ATS platforms as company domain
        for ats in cls.ATS_DOMAINS:
            if raw == ats or raw.endswith("." + ats):
                return None
                
        return raw
