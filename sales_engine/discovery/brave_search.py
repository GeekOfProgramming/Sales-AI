import os
import httpx
from typing import List
from sales_engine.discovery.search_provider import (
    SearchProvider, ConfigurationError, AuthenticationError, 
    RateLimitError, TimeoutError, SearchProviderError
)
from backend.schemas import NormalizedSearchResult

class BraveSearchProvider(SearchProvider):
    def __init__(self):
        self.api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        self.base_url = "https://api.search.brave.com/res/v1/web/search"
        
    async def search(self, query: str, num_results: int = 10) -> List[NormalizedSearchResult]:
        if not self.api_key:
            raise ConfigurationError("BRAVE_SEARCH_API_KEY is not configured.")
            
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key
        }
        
        params = {
            "q": query,
            "count": min(num_results, 20)
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.base_url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                results = []
                for idx, item in enumerate(data.get("web", {}).get("results", [])):
                    results.append(NormalizedSearchResult(
                        query=query,
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("description", ""),
                        source="brave",
                        rank=idx + 1
                    ))
                return results
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Brave Search API timeout: {e}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Brave Search API authentication failed: {e}")
            elif e.response.status_code == 429:
                raise RateLimitError(f"Brave Search API rate limit exceeded: {e}")
            else:
                raise SearchProviderError(f"Brave Search API error {e.response.status_code}: {e}")
        except Exception as e:
            raise SearchProviderError(f"Unexpected error calling Brave Search API: {e}")
