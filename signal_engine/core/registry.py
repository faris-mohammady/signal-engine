"""Plugin registry. Plugins register by name; packs refer to them by that name.

Source plugin:  class with  discover(pack, settings, ctx) -> list[AccountCandidate]
Signal plugin:  class with  detect(account, pack, signal_type, config, ctx) -> list[Signal]

A pack can ship its own plugins in packs/<slug>/plugins/*.py; they are loaded after
the built-in ones, so a pack can add or override a plugin without touching core.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

SOURCES: dict[str, type] = {}
SIGNALS: dict[str, type] = {}

BUILTIN_MODULES = [
    "signal_engine.plugins.sources.csv_import",
    "signal_engine.plugins.signals.hiring_role",
]


def source(name: str):
    def wrap(cls):
        SOURCES[name] = cls
        return cls

    return wrap


def signal(name: str):
    def wrap(cls):
        SIGNALS[name] = cls
        return cls

    return wrap


def load_builtin_plugins() -> None:
    for module in BUILTIN_MODULES:
        importlib.import_module(module)


def load_pack_plugins(pack_root: Path) -> None:
    plugin_dir = pack_root / "plugins"
    if not plugin_dir.is_dir():
        return
    for path in sorted(plugin_dir.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"pack_plugin_{pack_root.name}_{path.stem}", path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)


def get_source(name: str):
    if name not in SOURCES:
        raise KeyError(f"Unknown source plugin '{name}'. Registered: {sorted(SOURCES)}")
    return SOURCES[name]()


def get_signal(name: str):
    if name not in SIGNALS:
        raise KeyError(f"Unknown signal plugin '{name}'. Registered: {sorted(SIGNALS)}")
    return SIGNALS[name]()
