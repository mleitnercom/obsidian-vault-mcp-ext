"""Configuration for the extension package (its own env-var namespace).

VAULT_PATH is intentionally NOT snapshotted here: it is resolved dynamically from the
host server's config on each access (via module __getattr__) so tests that monkeypatch
obsidian_vault_mcp.config.VAULT_PATH still take effect.
"""

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


# Obsidian Local REST API bridge (optional; features fail soft when unset).
VAULT_OBSIDIAN_REST_URL = os.environ.get("VAULT_OBSIDIAN_REST_URL", "").strip().rstrip("/")
VAULT_OBSIDIAN_REST_API_KEY = os.environ.get("VAULT_OBSIDIAN_REST_API_KEY", "").strip()
VAULT_OBSIDIAN_REST_VERIFY_TLS = _env_bool("VAULT_OBSIDIAN_REST_VERIFY_TLS", False)
VAULT_OBSIDIAN_REST_TIMEOUT = _env_int("VAULT_OBSIDIAN_REST_TIMEOUT", 15)

# Templates / Dataview.
VAULT_TEMPLATER_FOLDER = os.environ.get("VAULT_TEMPLATER_FOLDER", "").strip().strip("/\\")
VAULT_DATAVIEW_TIMEOUT = _env_int("VAULT_DATAVIEW_TIMEOUT", 15)


def __getattr__(name: str):
    # Proxy VAULT_PATH to the host server's config dynamically (monkeypatch-friendly).
    if name == "VAULT_PATH":
        from obsidian_vault_mcp.config import VAULT_PATH
        return VAULT_PATH
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
