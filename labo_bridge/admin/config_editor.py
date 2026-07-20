"""
Safe, surgical editor for the two files that control the clinic API
integration: labo_bridge/api_client.py (ENDPOINT, API_TOKEN) and
labo_bridge/config.py (USE_MACHINE_RESULT_API). Same approach as
mappings_editor.py - rewrite one line via an anchored regex, leave every
comment/docstring untouched, rather than regenerating the file.
"""

import re
import threading
from pathlib import Path

API_CLIENT_PATH = Path(__file__).resolve().parent.parent / "api_client.py"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.py"

_lock = threading.Lock()


def _set_top_level_str_assignment(path: Path, var_name: str, new_value: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf'^{re.escape(var_name)}\s*=\s*"([^"]*)"', re.MULTILINE)
    quoted = new_value.replace("\\", "\\\\").replace('"', '\\"')
    new_text, count = pattern.subn(f'{var_name} = "{quoted}"', text, count=1)
    if count == 0:
        raise ValueError(f"{var_name} assignment not found in {path.name}")
    path.write_text(new_text, encoding="utf-8")


def _set_top_level_bool_assignment(path: Path, var_name: str, new_value: bool) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf'^{re.escape(var_name)}\s*=\s*(True|False)', re.MULTILINE)
    new_text, count = pattern.subn(f'{var_name} = {new_value}', text, count=1)
    if count == 0:
        raise ValueError(f"{var_name} assignment not found in {path.name}")
    path.write_text(new_text, encoding="utf-8")


def get_current():
    """Read current values by importing the live modules (always up to date)."""
    from labo_bridge import api_client, config
    import importlib
    importlib.reload(api_client)
    importlib.reload(config)
    return {
        "endpoint": api_client.ENDPOINT,
        "api_token": api_client.API_TOKEN,
        "use_machine_result_api": config.USE_MACHINE_RESULT_API,
    }


def update(endpoint: str = None, api_token: str = None, use_machine_result_api: bool = None) -> dict:
    """Update any subset of the three settings. Returns the new full state."""
    with _lock:
        if endpoint is not None:
            if not endpoint.startswith(("http://", "https://")):
                raise ValueError("endpoint must start with http:// or https://")
            _set_top_level_str_assignment(API_CLIENT_PATH, "ENDPOINT", endpoint)
        if api_token is not None:
            _set_top_level_str_assignment(API_CLIENT_PATH, "API_TOKEN", api_token)
        if use_machine_result_api is not None:
            _set_top_level_bool_assignment(CONFIG_PATH, "USE_MACHINE_RESULT_API",
                                           bool(use_machine_result_api))
    return get_current()
