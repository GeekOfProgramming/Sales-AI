from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from backend.schemas import CompanyEnrichment

class BaseCompanyProvider(ABC):
    """Abstract base class for company enrichment providers."""
    
    @abstractmethod
    async def enrich_company(self, domain: Optional[str] = None, name: Optional[str] = None) -> Optional[CompanyEnrichment]:
        """Attempt to enrich company data. Resolves domain if missing."""
        pass
        
    @abstractmethod
    def get_provider_name(self) -> str:
        pass
