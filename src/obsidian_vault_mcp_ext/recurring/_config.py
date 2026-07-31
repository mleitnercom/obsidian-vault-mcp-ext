"""Configuration for the recurring extension (its own env-var namespace).

VAULT_PATH is intentionally NOT snapshotted here: it is resolved dynamically from the
host server's config on each access (via module __getattr__) so tests that monkeypatch
obsidian_vault_mcp.config.VAULT_PATH still take effect. This mirrors templates/_config.py.

All other knobs are read from VAULT_RECURRING_* env vars at import time. Tests override
them via monkeypatch.setattr on this module, exactly like the templates extension does.
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


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    raw = os.environ.get(name, "").strip().lower()
    if raw in allowed:
        return raw
    return default


# Recurring task materialization knobs (ported from the fork's config).
VAULT_RECURRING_ENABLED = _env_bool("VAULT_RECURRING_ENABLED", True)
VAULT_RECURRING_TEMPLATES_FOLDER = (
    os.environ.get("VAULT_RECURRING_TEMPLATES_FOLDER", "").strip().strip("/\\")
)
VAULT_RECURRING_INTERVAL = _env_int("VAULT_RECURRING_INTERVAL", 0)
VAULT_RECURRING_DONE_STATUS = (
    os.environ.get("VAULT_RECURRING_DONE_STATUS", "done").strip() or "done"
)
# Status stamped onto freshly materialized instances (Tasks-Schema v0.8 requires a
# status; a template can override it via ``frontmatter_to_inherit``).
VAULT_RECURRING_INSTANCE_STATUS = (
    os.environ.get("VAULT_RECURRING_INSTANCE_STATUS", "next").strip() or "next"
)
# Run observability: surface a failing (usually unattended) run where the operator
# looks instead of only in the log. Both are vault-relative paths; empty disables.
#   ALERT_PATH  -> self-clearing task written on errors, deleted on a clean run.
#   REPORT_PATH -> last-run report note overwritten every run.
VAULT_RECURRING_ALERT_PATH = (
    os.environ.get("VAULT_RECURRING_ALERT_PATH", "").strip().strip("/\\")
)
VAULT_RECURRING_REPORT_PATH = (
    os.environ.get("VAULT_RECURRING_REPORT_PATH", "").strip().strip("/\\")
)
VAULT_RECURRING_CATCHUP_MODE = _env_choice(
    "VAULT_RECURRING_CATCHUP_MODE", "next", {"next", "all"}
)


def __getattr__(name: str):
    # Proxy VAULT_PATH to the host server's config dynamically (monkeypatch-friendly).
    if name == "VAULT_PATH":
        from obsidian_vault_mcp.config import VAULT_PATH

        return VAULT_PATH
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
