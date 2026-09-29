import os
import httpx
from typing import List
from sales_engine.discovery.search_provider import SearchProvider
from backend.schemas import NormalizedSearchResult

class BraveSearchProvider(SearchProvider):
    def __init__(self):
        self.api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        self.base_url = "https://api.search.brave.com/res/v1/web/search"
        
    async def search(self, query: str, num_results: int = 10) -> List[NormalizedSearchResult]:
        if not self.api_key:
            print("[Warning] BRAVE_SEARCH_API_KEY is not set. Returning empty results.")
            return []
            
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
        except httpx.HTTPError as e:
            print(f"[BraveSearchProvider] HTTP error during search: {e}")
            return []
        except Exception as e:
            print(f"[BraveSearchProvider] Unexpected error: {e}")
            return []
