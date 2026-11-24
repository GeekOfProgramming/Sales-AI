"""Backend API Package for pyBIM-LLM.
Provides FastAPI endpoints for Revit integration, RAG retrieval, and AI code generation.
"""

from importlib import import_module
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ElementMetadata": (".schemas", "ElementMetadata"),
    "ScriptGenerationRequest": (".schemas", "ScriptGenerationRequest"),
    "ScriptGenerationResponse": (".schemas", "ScriptGenerationResponse"),
    "HealthResponse": (".schemas", "HealthResponse"),
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
