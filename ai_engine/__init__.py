"""AI Engine package for pyBIM-LLM.
Provides local LLM inference and RAG integration for Revit and BIM workflows.
"""

from importlib import import_module
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BIMLLMClient": (".llm_client", "BIMLLMClient"),
    "CodeGenerationRequest": (".llm_client", "CodeGenerationRequest"),
    "CodeGenerationResponse": (".llm_client", "CodeGenerationResponse"),
    "BIMRAGRetriever": (".rag_retriever", "BIMRAGRetriever"),
    "BIMDataIngestor": (".data_ingestor", "BIMDataIngestor"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
