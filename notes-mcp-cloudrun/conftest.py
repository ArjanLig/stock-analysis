"""Zorgt dat `import main` / `import mcp_handler` in deze map deze service raakt.

Twee services in dezelfde repo hebben allebei een top-level `main.py` en
`mcp_handler.py`. Zonder pakketstructuur staan beide mappen op sys.path en wint
de module die als eerste in sys.modules belandt -- ook voor tests van de andere
service. Dat maakte de gecombineerde run waardeloos: notities-tests kregen
LazyTheta's modules, en de test die "de app bestaat" controleerde slaagde
zonder de notities-app ooit aan te raken.

`--import-mode=importlib` lost dit niet op: dat verandert alleen hoe pytest de
testmodules zelf laadt, niet hoe een `import mcp_handler` binnen zo'n test
wordt opgelost. Daarom laden we de botsende namen hier expliciet uit hun eigen
bestand en zetten we ze per test in sys.modules.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent

# Namen die ook in lazytheta-mcp-cloudrun bestaan. In laadvolgorde: mcp_handler
# eerst, zodat main's `from mcp_handler import mcp_endpoint` de juiste pakt.
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
