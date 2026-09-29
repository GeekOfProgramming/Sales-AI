from typing import List
from backend.schemas import WebsiteProfile, DiscoveryResponse, DiscoveryResultItem
from sales_engine.discovery.query_generator import QueryGenerator
from sales_engine.discovery.brave_search import BraveSearchProvider
from sales_engine.discovery.url_classifier import URLClassifier

class DiscoveryOrchestrator:
    def __init__(self):
        self.search_provider = BraveSearchProvider()
        self.classifier = URLClassifier()
        
    async def discover(self, website_url: str, profile: WebsiteProfile, countries: List[str], max_queries: int = 20, results_per_query: int = 10) -> DiscoveryResponse:
        max_queries = min(max_queries, 20)
        results_per_query = min(results_per_query, 10)
        
        generator = QueryGenerator(max_queries=max_queries)
        
        # 1. Generate Queries
        try:
            query_resp = await generator.generate_queries(profile, countries)
            # Sort queries by priority descending
            sorted_queries = sorted(query_resp.queries, key=lambda q: q.priority, reverse=True)
            # Limit exactly to max_queries in case LLM generated more
            queries = sorted_queries[:max_queries]
        except Exception as e:
            raise Exception(f"Query generation failed: {e}")
        # 2. Execute Searches
        raw_results = []
        for q in queries:
            try:
                results = await self.search_provider.search(q.query, num_results=results_per_query)
                for r in results:
                    raw_results.append((q, r))
            except Exception as e:
                print(f"[DiscoveryOrchestrator] Search failed for query '{q.query}': {e}")
                # Re-raise to abort and tell the user there is a configuration/provider issue,
                # unless we want to continue. The prompt says: "A missing or broken provider must not be reported as status = ok"
                raise e
                
        # 3. Normalize & Deduplicate
        unique_urls = set()
        deduped = []
        
        for q, r in raw_results:
            norm_url = self.classifier.normalize_url(r.url)
            if norm_url not in unique_urls:
                unique_urls.add(norm_url)
                # Keep normalized url
                r.url = norm_url
                deduped.append((q, r))
                
        # 4. Classify
        final_results = []
        candidate_count = 0
        
        for q, r in deduped:
            classification = self.classifier.classify(r.url)
            
            final_results.append(DiscoveryResultItem(
                title=r.title,
                url=r.url,
                classification=classification,
                query_type=q.type
            ))
            
            if classification in ("ats_job", "job_posting", "career_page"):
                candidate_count += 1
                
        return DiscoveryResponse(
            status="ok",
            website=website_url,
            queries_generated=len(queries),
            raw_results=len(raw_results),
            unique_results=len(final_results),
            candidate_urls=candidate_count,
            results=final_results
        )
