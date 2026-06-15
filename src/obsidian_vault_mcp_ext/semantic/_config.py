"""Configuration for the semantic extension (its own env-var namespace).

Ported from the fork's central config.py SEMANTIC_* block, but scoped to this
self-contained subpackage so it depends on no host-core internals.

VAULT_PATH is intentionally NOT snapshotted here: it is resolved dynamically from
the host server's config on each access (via module __getattr__) so tests that
monkeypatch obsidian_vault_mcp.config.VAULT_PATH still take effect. Likewise the
cache path is computed lazily from VAULT_PATH unless overridden explicitly.
"""

import os
from pathlib import Path


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


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    raw = os.environ.get(name, "").strip().lower()
    return raw if raw in allowed else default


# Whether semantic search is enabled at all.
SEMANTIC_SEARCH_ENABLED = _env_bool("VAULT_SEMANTIC_SEARCH_ENABLED", False)

# Embedding backend selection and model.
SEMANTIC_EMBED_BACKEND = _env_choice(
    "VAULT_SEMANTIC_EMBED_BACKEND",
    "fastembed",
    {"auto", "sentence", "fastembed"},
)
SEMANTIC_EMBED_MODEL = os.environ.get("VAULT_SEMANTIC_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# Index build/refresh behavior.
SEMANTIC_BUILD_ON_DEMAND = _env_bool("VAULT_SEMANTIC_BUILD_ON_DEMAND", False)
SEMANTIC_CHUNK_SIZE = _env_int("VAULT_SEMANTIC_CHUNK_SIZE", 900)
SEMANTIC_CHUNK_OVERLAP = _env_int("VAULT_SEMANTIC_CHUNK_OVERLAP", 150)
SEMANTIC_EMBED_BATCH_SIZE = _env_int("VAULT_SEMANTIC_EMBED_BATCH_SIZE", 64)
SEMANTIC_MAX_RESULTS = _env_int("VAULT_SEMANTIC_MAX_RESULTS", 20)
SEMANTIC_UPDATE_DEBOUNCE_SECONDS = _env_int("VAULT_SEMANTIC_UPDATE_DEBOUNCE_SECONDS", 4)

# Directories never indexed (matches the host's excluded set).
EXCLUDED_DIRS = {".obsidian", ".trash", ".git", ".DS_Store", ".obsidian-vault-mcp"}

# Explicit cache path override; when empty the cache lives under VAULT_PATH.
_SEMANTIC_CACHE_PATH_OVERRIDE = os.environ.get("VAULT_SEMANTIC_CACHE_PATH", "").strip()


def __getattr__(name: str):
    # Proxy VAULT_PATH to the host server's config dynamically (monkeypatch-friendly).
    if name == "VAULT_PATH":
        from obsidian_vault_mcp.config import VAULT_PATH
        return VAULT_PATH
    # Derive the cache directory from the current VAULT_PATH unless overridden, so a
    # monkeypatched vault in tests gets an isolated cache under the temp vault.
    if name == "SEMANTIC_CACHE_PATH":
        if _SEMANTIC_CACHE_PATH_OVERRIDE:
            return Path(_SEMANTIC_CACHE_PATH_OVERRIDE)
        from obsidian_vault_mcp.config import VAULT_PATH
        return Path(VAULT_PATH) / ".obsidian-vault-mcp"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
