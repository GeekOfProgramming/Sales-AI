from typing import List, Optional
from abc import ABC, abstractmethod
from backend.schemas import NormalizedSearchResult

class SearchProviderError(Exception):
    pass

class AuthenticationError(SearchProviderError):
    pass

class RateLimitError(SearchProviderError):
    pass

class TimeoutError(SearchProviderError):
    pass

class ConfigurationError(SearchProviderError):
    pass

class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, num_results: int = 10) -> List[NormalizedSearchResult]:
        """Execute a search query and return normalized results."""
        pass
