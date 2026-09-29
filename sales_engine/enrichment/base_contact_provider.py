from abc import ABC, abstractmethod
from typing import List, Optional
from backend.schemas import ContactCandidate

class BaseContactProvider(ABC):
    """Abstract base class for contact discovery and enrichment providers."""
    
    @abstractmethod
    async def search_contacts(self, company_domain: str, titles: List[str], limit: int = 5) -> List[ContactCandidate]:
        """Search for contacts by buyer role titles at a specific company domain."""
        pass
        
    @abstractmethod
    async def find_work_email(self, first_name: str, last_name: str, company_domain: str, person_id: Optional[str] = None) -> Optional[str]:
        """Find a business email for a specific person."""
        pass
        
    @abstractmethod
    async def verify_email(self, email: str) -> Optional[str]:
        """Verify the deliverability status of an email (e.g., 'verified', 'risky')."""
        pass
        
    @abstractmethod
    def get_provider_name(self) -> str:
        pass
