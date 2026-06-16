"""Configuration for the maintenance extension (its own env-var namespace).

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


# Scan/repair caps and the default source encoding for repair.
SCAN_MAX_RESULTS = _env_int("VAULT_MAINTENANCE_SCAN_MAX_RESULTS", 100)
REPAIR_MAX_FILES = _env_int("VAULT_MAINTENANCE_REPAIR_MAX_FILES", 50)
REPAIR_SOURCE_ENCODING = os.environ.get("VAULT_MAINTENANCE_REPAIR_SOURCE_ENCODING", "cp1252").strip() or "cp1252"

# Trash folder name at the vault root for soft-deleted directories.
TRASH_DIR_NAME = os.environ.get("VAULT_MAINTENANCE_TRASH_DIR", ".trash").strip().strip("/\\") or ".trash"


def __getattr__(name: str):
    # Proxy VAULT_PATH to the host server's config dynamically (monkeypatch-friendly).
    if name == "VAULT_PATH":
        from obsidian_vault_mcp.config import VAULT_PATH
        return VAULT_PATH
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
