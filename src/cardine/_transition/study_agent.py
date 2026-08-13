"""Identity-preserving leaves shared by the moved Cardine domain modules."""

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _leaf_module(name: str) -> ModuleType:
    """Load a domain leaf without executing the package initializer mid-cycle."""

    try:
        return importlib.import_module(name)
    except ImportError as error:
        if "partially initialized module" not in str(error):
            raise
        # A direct ``cardine.domain.course`` import can reach this seam before
        # Python has initialized ``study_agent.domain``.  Install a temporary
        # package shell so the two leaf modules load under their canonical
        # names, preserving class identity without running the cyclic __init__.
        package_name = "study_agent.domain"
        package_path = Path(__file__).resolve().parents[2] / "study_agent" / "domain"
        package = ModuleType(package_name)
        package.__path__ = [str(package_path)]
        package.__package__ = package_name
        sys.modules[package_name] = package
        try:
            return _load_leaf(name, package_path)
        finally:
            sys.modules.pop(package_name, None)


def _load_leaf(name: str, package_path: Path) -> ModuleType:
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    leaf = name.rsplit(".", 1)[-1]
    path = package_path / f"{leaf}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load transition leaf {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_validation = _leaf_module("study_agent.domain._validation")
_identifiers = _leaf_module("study_agent.domain.identifiers")
require_aware = _validation.require_aware
require_text = _validation.require_text
CourseId = _identifiers.CourseId
EventId = _identifiers.EventId
InteractionId = _identifiers.InteractionId
SessionId = _identifiers.SessionId
StatementId = _identifiers.StatementId

__all__ = [
    "CourseId",
    "EventId",
    "InteractionId",
    "SessionId",
    "StatementId",
    "require_aware",
    "require_text",
]
