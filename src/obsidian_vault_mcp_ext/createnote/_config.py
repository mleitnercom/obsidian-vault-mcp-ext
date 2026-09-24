"""Configuration for the create-note extension.

The same VAULT_CREATE_NOTE_* variables the fork read, so a production environment carries
over unchanged. The tool is inert until VAULT_CREATE_NOTE_PATH_PATTERN is set: with no
statement about which paths are eligible, any creation would be a guess, so the
unconfigured state refuses rather than allows.

PATH_PATTERN            regex the vault-relative path must fully match
REQUIRED_FRONTMATTER    JSON object {field: regex or true}; each field must be present
                        and, for a regex, its value must fully match
ALLOWED_FRONTMATTER     comma-separated field allowlist; empty means "any field",
                        non-empty must cover every required field
ID_FIELD                frontmatter field whose value must equal the filename stem
REQUIRE_BODY_SECTION    literal string the body must contain (e.g. a heading)
MAX_BYTES               per-note ceiling

VAULT_PATH is resolved from the host config on each access, so tests that monkeypatch it
take effect.
"""

import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_csv(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


VAULT_CREATE_NOTE_PATH_PATTERN = os.environ.get("VAULT_CREATE_NOTE_PATH_PATTERN", "").strip()
VAULT_CREATE_NOTE_REQUIRED_FRONTMATTER = os.environ.get("VAULT_CREATE_NOTE_REQUIRED_FRONTMATTER", "").strip()
VAULT_CREATE_NOTE_ALLOWED_FRONTMATTER = _env_csv("VAULT_CREATE_NOTE_ALLOWED_FRONTMATTER")
VAULT_CREATE_NOTE_ID_FIELD = os.environ.get("VAULT_CREATE_NOTE_ID_FIELD", "").strip()
VAULT_CREATE_NOTE_REQUIRE_BODY_SECTION = os.environ.get("VAULT_CREATE_NOTE_REQUIRE_BODY_SECTION", "").strip()
VAULT_CREATE_NOTE_MAX_BYTES = _env_int("VAULT_CREATE_NOTE_MAX_BYTES", 16000)


def __getattr__(name: str):
    if name == "VAULT_PATH":
        from obsidian_vault_mcp.config import VAULT_PATH

        return VAULT_PATH
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
