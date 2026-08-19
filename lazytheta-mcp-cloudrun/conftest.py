"""Zorgt dat `import main` / `import mcp_handler` in deze map deze service raakt.

Spiegelbeeld van notes-mcp-cloudrun/conftest.py. Beide services hebben een
top-level `main.py` en `mcp_handler.py`; zonder deze conftest bepaalt de
collectievolgorde welke van de twee in sys.modules blijft staan, en draaien de
tests van de ene service tegen de code van de andere.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent

# Laadvolgorde: mcp_handler eerst, zodat main's `from mcp_handler import
# mcp_endpoint` de module van deze service pakt.
OWNED_MODULES = ("mcp_handler", "main")

_loaded: dict[str, object] = {}


def _ensure_loaded() -> dict[str, object]:
    if _loaded:
        return _loaded
    for path in (str(REPO_ROOT), str(HERE)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    saved = {name: sys.modules.pop(name, None) for name in OWNED_MODULES}
    try:
        for name in OWNED_MODULES:
            spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            _loaded[name] = module
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return _loaded


@pytest.fixture(autouse=True)
def _own_modules():
    """Installeer deze service's modules voor de duur van elke test hier."""
    mine = _ensure_loaded()
    previous = {name: sys.modules.get(name) for name in OWNED_MODULES}
    sys.modules.update(mine)
    try:
        yield
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
