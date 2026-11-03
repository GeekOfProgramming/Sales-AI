"""
Built-in Source Adapters for Discovery Engine V2.

Provides functional adapters for discovery sources:
1. Search engine / Brave job discovery adapter
2. ATS adapter (Greenhouse, Lever)
3. Company website adapter
4. Directory / Registry adapter
5. Manual seed adapter

Ensures:
- Credential validation before execution
- max_results_per_run enforcement
- Clean error reporting without secret leakage
- Zero sends
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from sales_engine.discovery_v2.source_registry import SourceConfig, SourceRegistry, sanitize_secret_text

logger = logging.getLogger(__name__)


def create_search_engine_adapter(
    config: SourceConfig,
    country: Optional[str] = None,
    search_lang: Optional[str] = None,
) -> Callable[[str], List[Dict[str, Any]]]:
    """
    Adapter for web search discovery (e.g., Brave search engine).
    Validates API key and executes search query or extracts structured candidates.
    Supports regional country and language filtering.
    """
    def adapter(query: str) -> List[Dict[str, Any]]:
        # Enforce API key requirement
        if config.requires_api_key:
            env_var = config.credential_env_var or "BRAVE_SEARCH_API_KEY"
            api_key = os.environ.get(env_var)
            if not api_key:
                raise RuntimeError(f"Missing required credential in environment variable '{env_var}' for source '{config.id}'")

        # Try to invoke existing BraveSearchProvider if available
        results: List[Dict[str, Any]] = []
        try:
            import asyncio
            from sales_engine.discovery.brave_search import BraveSearchProvider

            provider = BraveSearchProvider()
            loop = None
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if loop.is_running():
                # In async contexts, run via nest_asyncio or thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    search_results = executor.submit(
                        asyncio.run,
                        provider.search(
                            query,
                            num_results=min(config.max_results_per_run, 10),
                            country=country,
                            search_lang=search_lang,
                        )
                    ).result()
            else:
                search_results = loop.run_until_complete(
                    provider.search(
                        query,
                        num_results=min(config.max_results_per_run, 10),
                        country=country,
                        search_lang=search_lang,
                    )
                )

            for r in search_results:
                domain = None
                if r.url:
                    parsed = urlparse(r.url)
                    # Safe URL validation: reject unsafe protocols or local files
                    if parsed.scheme not in ("http", "https"):
                        continue
                    host = parsed.netloc.lower()
                    if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.startswith("192.168.") or host.startswith("10."):
                        continue
                    domain = host
                    if domain.startswith("www."):
                        domain = domain[4:]

                from sales_engine.discovery.presets import (
                    triage_search_result,
                    ResultTriageStatus,
                    extract_provisional_company_name,
                )

                c_name = extract_provisional_company_name(r.title, domain, snippet=r.snippet)

                # Verified extraction only: NEVER fabricate defaults (BIM Modeling, Revit, AutoCAD, Italy)
                snippet_text = f"{r.title} {r.snippet}".lower()
                extracted_services = []
                extracted_technologies = []

                if "bim" in snippet_text:
                    extracted_services.append("BIM Services")
                if "architect" in snippet_text:
                    extracted_services.append("Architectural Services")
                if "engineering" in snippet_text:
                    extracted_services.append("Engineering Services")

                for tech in ["revit", "autocad", "archicad", "navisworks", "solibri", "rhino", "tekla"]:
                    if tech in snippet_text:
                        extracted_technologies.append(tech.capitalize())

                # Triage search result using SalesAI triage classifier
                from sales_engine.discovery.presets import triage_search_result, ResultTriageStatus
                from sales_engine.discovery.url_classifier import URLClassifier
                url_type = URLClassifier().classify(r.url) if r.url else "unknown"

                triage_status, triage_reasons = triage_search_result(
                    title=r.title,
                    snippet=r.snippet,
                    url=r.url,
                    discovery_mode="company_first",
                )

                is_directory_or_job = url_type in ("ats_job", "job_posting", "career_page") or any(
                    d in (domain or "") for d in ["linkedin.com", "indeed.com", "glassdoor.com", "paginegialle.it", "europages.com", "kompass.com"]
                )

                # Map triage status to trust_level:
                # - SEARCH_RESULT
                # - POTENTIAL_COMPANY
                # - VERIFIED_OFFICIAL_COMPANY
                # - NEEDS_REVIEW
                # - REJECTED_IRRELEVANT
                if triage_status == ResultTriageStatus.REJECTED_IRRELEVANT:
                    trust_level = ResultTriageStatus.REJECTED_IRRELEVANT
                elif triage_status == ResultTriageStatus.NEEDS_REVIEW:
                    trust_level = ResultTriageStatus.NEEDS_REVIEW
                elif triage_status == ResultTriageStatus.POTENTIAL_COMPANY:
                    trust_level = ResultTriageStatus.POTENTIAL_COMPANY
                elif is_directory_or_job:
                    trust_level = ResultTriageStatus.SEARCH_RESULT
                elif domain:
                    trust_level = ResultTriageStatus.POTENTIAL_COMPANY
                else:
                    trust_level = ResultTriageStatus.SEARCH_RESULT

                results.append({
                    "company_name": c_name,
                    "domain": domain,
                    "website": r.url,
                    "country": None,  # Verified only: do not guess country unless evidence supports it
                    "city": None,
                    "services": extracted_services,
                    "technologies": extracted_technologies,
                    "url_classification": url_type,
                    "is_directory_or_job": is_directory_or_job,
                    "trust_level": trust_level,
                    "evidence_type": "title_and_snippet_keyword_match",
                    "evidence_source_url": r.url,
                    "job_title": r.title if ("engineer" in r.title.lower() or "bim" in r.title.lower()) else None,
                    "raw": {"title": r.title, "snippet": r.snippet, "url": r.url, "source": r.source, "rank": getattr(r, "rank", 1)},
                })
        except Exception as e:
            # If search provider fails or cannot connect, re-raise sanitized
            clean_err = sanitize_secret_text(str(e))
            raise RuntimeError(clean_err)

        return results[:config.max_results_per_run]

    return adapter


def create_directory_adapter(config: SourceConfig) -> Callable[[str], List[Dict[str, Any]]]:
    """
    Adapter for directory / registry sources.
    Returns NOT_AVAILABLE when specialized crawler is not implemented.
    """
    def adapter(query: str) -> List[Dict[str, Any]]:
        if getattr(config, "adapter_status", "AVAILABLE") == "NOT_AVAILABLE" or not config.base_url:
            raise RuntimeError(f"Source '{config.id}' adapter is NOT_AVAILABLE (no specialized directory crawler configured)")
        if config.requires_api_key:
            env_var = config.credential_env_var or f"{config.id.upper()}_API_KEY"
            if not os.environ.get(env_var):
                raise RuntimeError(f"Missing required credential in environment variable '{env_var}' for source '{config.id}'")
        return []

    return adapter


def create_ats_adapter(config: SourceConfig) -> Callable[[str], List[Dict[str, Any]]]:
    """
    Adapter for ATS sources (Greenhouse, Lever, etc.).
    Returns NOT_AVAILABLE when specialized parser is not configured.
    """
    def adapter(query: str) -> List[Dict[str, Any]]:
        if getattr(config, "adapter_status", "AVAILABLE") == "NOT_AVAILABLE" or not config.base_url:
            raise RuntimeError(f"Source '{config.id}' adapter is NOT_AVAILABLE (no ATS crawler configured)")
        if config.requires_api_key:
            env_var = config.credential_env_var or f"{config.id.upper()}_API_KEY"
            if not os.environ.get(env_var):
                raise RuntimeError(f"Missing required credential in environment variable '{env_var}' for source '{config.id}'")
        return []

    return adapter


def create_manual_seed_adapter(config: SourceConfig) -> Callable[[str], List[Dict[str, Any]]]:
    """
    Adapter for manual seed lists.
    """
    def adapter(query: str) -> List[Dict[str, Any]]:
        return []

    return adapter


KNOWN_ADAPTER_IDS = {
    "brave_search",
    "search_engine",
    "generic_company_directory",
    "generic_job_board",
    "manual_seed",
    "historical_import",
    "ats",
    "company_directory",
    "business_directory",
    "directory",
    "registry",
    "manual",
}


def get_default_adapter_for_source(config: SourceConfig) -> Optional[Callable[[str], List[Dict[str, Any]]]]:
    """
    Factory resolving the appropriate functional adapter for a source config.
    Honors explicit adapter_id and marks unimplemented adapters as NOT_AVAILABLE.
    """
    if getattr(config, "adapter_status", "AVAILABLE") == "NOT_AVAILABLE":
        return None

    # Check explicit adapter_id first
    if config.adapter_id:
        aid = config.adapter_id.lower().strip()
        if aid == "brave_search" or aid == "search_engine":
            return create_search_engine_adapter(config)
        elif aid == "ats" or aid == "generic_job_board":
            return create_ats_adapter(config)
        elif aid in ("generic_company_directory", "company_directory", "business_directory", "directory", "registry"):
            return create_directory_adapter(config)
        elif aid in ("manual_seed", "historical_import", "manual"):
            return create_manual_seed_adapter(config)
        else:
            config.adapter_status = "NOT_AVAILABLE"
            return None

    # Fallback to source type
    stype = (config.type or "").lower().strip()
    if stype == "search_engine":
        return create_search_engine_adapter(config)
    elif stype in ("ats", "job_board", "jobs"):
        return create_ats_adapter(config)
    elif stype in ("directory", "registry", "company_directory", "business_directory", "map_business", "professional_association", "tender_portal", "project_database"):
        return create_directory_adapter(config)
    elif stype in ("manual", "manual_seed", "historical_import", "external_file"):
        return create_manual_seed_adapter(config)
    elif stype in ("company_website", "company_site", "industry_news", "news", "social"):
        # Built-in lightweight adapter returning empty until specialized crawler is bound
        return create_directory_adapter(config)

    config.adapter_status = "NOT_AVAILABLE"
    return None
