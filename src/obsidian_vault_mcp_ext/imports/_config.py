"""Configuration for the import extension (its own env-var namespace).

Secure-by-default: URL import is OFF until ``VAULT_IMPORT_URL_ENABLED`` is set, and
private/loopback/link-local targets are denied unless explicitly opted in. None of these
knobs are snapshotted in a way that hides ``VAULT_PATH``; the target path is always
resolved through the host's ``resolve_vault_path`` at call time.
"""

import json
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


def _env_ports(name: str, default: set[int]) -> set[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return set(default)
    ports: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ports.add(int(part))
        except ValueError:
            continue
    return ports or set(default)


# media_type -> allowed file extensions. Mirrors the host's binary allowlist shape so the
# same media types that can be written can also be imported. Override with a JSON object.
DEFAULT_ALLOWED_MEDIA_TYPES: dict[str, set[str]] = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/webp": {".webp"},
    "image/gif": {".gif"},
    "image/svg+xml": {".svg"},
    "application/pdf": {".pdf"},
}


def _allowed_media_types() -> dict[str, set[str]]:
    raw = os.environ.get("VAULT_IMPORT_ALLOWED_MEDIA_TYPES_JSON", "").strip()
    if not raw:
        return {mt: set(exts) for mt, exts in DEFAULT_ALLOWED_MEDIA_TYPES.items()}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {mt: set(exts) for mt, exts in DEFAULT_ALLOWED_MEDIA_TYPES.items()}
    merged: dict[str, set[str]] = {}
    for media_type, extensions in payload.items():
        if not isinstance(media_type, str) or not isinstance(extensions, list):
            continue
        cleaned = {
            ("." + e.lstrip(".")).lower()
            for e in extensions
            if isinstance(e, str) and e.strip()
        }
        if cleaned:
            merged[media_type.strip().lower()] = cleaned
    return merged or {mt: set(exts) for mt, exts in DEFAULT_ALLOWED_MEDIA_TYPES.items()}


# URL import.
URL_IMPORT_ENABLED = _env_bool("VAULT_IMPORT_URL_ENABLED", False)
URL_ALLOW_PRIVATE = _env_bool("VAULT_IMPORT_URL_ALLOW_PRIVATE", False)
URL_TIMEOUT_SECONDS = _env_int("VAULT_IMPORT_URL_TIMEOUT", 30)
URL_MAX_REDIRECTS = _env_int("VAULT_IMPORT_URL_MAX_REDIRECTS", 5)
URL_ALLOWED_PORTS = _env_ports("VAULT_IMPORT_URL_ALLOWED_PORTS", {80, 443})

# Shared limits.
MAX_BYTES = _env_int("VAULT_IMPORT_MAX_BYTES", 10 * 1024 * 1024)

# Local file import (separate surface: path traversal, guarded by an explicit root allowlist).
def _allowed_file_roots() -> list[str]:
    raw = os.environ.get("VAULT_IMPORT_FILE_ALLOWED_ROOTS", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(os.pathsep) if p.strip()]


def allowed_media_types() -> dict[str, set[str]]:
    return _allowed_media_types()


def allowed_file_roots() -> list[str]:
    return _allowed_file_roots()
