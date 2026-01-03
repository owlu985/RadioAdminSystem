from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Dict, Optional

from flask import current_app

from app.models import Plugin, db


@dataclass
class PluginInfo:
    name: str
    display_name: str
    blueprint: object
    url_prefix: str
    manage_endpoint: Optional[str] = None
    description: Optional[str] = None


def ensure_plugin_record(name: str) -> Plugin:
    plugin = Plugin.query.filter_by(name=name).first()
    if not plugin:
        plugin = Plugin(name=name, enabled=True)
        db.session.add(plugin)
        db.session.commit()
    return plugin


def load_plugins(app) -> Dict[str, PluginInfo]:
    registry: Dict[str, PluginInfo] = {}
    base_path = Path(__file__).parent
    for module_path in base_path.iterdir():
        if not module_path.is_dir() or module_path.name.startswith("__"):
            continue
        if not (module_path / "__init__.py").exists():
            continue
        module_name = f"app.plugins.{module_path.name}"
        module = import_module(module_name)
        if hasattr(module, "register_plugin"):
            info = module.register_plugin(app)
            if info:
                registry[info.name] = info
    app.config["PLUGIN_REGISTRY"] = registry
    app.config["PLUGIN_DISPLAY_NAMES"] = {k: v.display_name for k, v in registry.items()}
    return registry


def plugin_display_name(name: str) -> str:
    registry: Dict[str, PluginInfo] = current_app.config.get("PLUGIN_REGISTRY", {})
    info = registry.get(name)
    if info:
        return info.display_name
    labels = current_app.config.get("PLUGIN_DISPLAY_NAMES", {})
    return labels.get(name, name)
