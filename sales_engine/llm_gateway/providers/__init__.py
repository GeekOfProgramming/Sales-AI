"""
sales_engine/llm_gateway/providers/__init__.py
Provider adapter implementations for SalesAI LLM Gateway.
"""

from sales_engine.llm_gateway.providers.openai_provider import OpenAIProvider

__all__ = ["OpenAIProvider"]
