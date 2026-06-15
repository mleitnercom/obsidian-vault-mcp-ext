"""Import tools: download a URL or copy a local allowlisted file into the vault.

Self-contained against stock upstream: uses only ``resolve_vault_path`` from the host
(to map a vault-relative path safely into the vault) and ``serialization.dumps`` for the
response shape. Binary bytes are written atomically here (upstream exposes only the text
``write_file_atomic``), so the extension does not depend on a fork-only binary primitive.
"""

import hashlib
import logging
import os
import uuid
from pathlib import Path

from obsidian_vault_mcp.serialization import dumps as vault_json_dumps
from obsidian_vault_mcp.vault import resolve_vault_path

from . import _config
from ._fetch import ImportFetchError, ImportSecurityError, fetch_url

logger = logging.getLogger(__name__)


def _validate_binary_target(path: str, media_type: str) -> Path:
    """Resolve the vault target and enforce the media-type / extension allowlist."""
    resolved = resolve_vault_path(path)
    allowlist = _config.allowed_media_types()
    allowed_extensions = allowlist.get(media_type.strip().lower())
    if not allowed_extensions:
        raise ValueError(f"Unsupported media_type: {media_type}")
    if Path(path).suffix.lower() not in allowed_extensions:
        raise ValueError(
            f"Extension '{Path(path).suffix.lower()}' is not allowed for media_type '{media_type}'"
        )
    return resolved


def _write_bytes_atomic(resolved: Path, data: bytes, *, create_dirs: bool, overwrite: bool) -> tuple[bool, int]:
    """Write bytes atomically (temp file + os.replace) with a read-back size check."""
    if create_dirs:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    is_new = not resolved.exists()
    tmp = resolved.with_name(f"{resolved.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        written = tmp.stat().st_size
        if written != len(data):
            raise RuntimeError(f"Write verification failed (expected {len(data)} bytes, wrote {written})")
        os.replace(tmp, resolved)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return is_new, len(data)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def vault_import_url(
    path: str,
    url: str,
    media_type: str,
    overwrite: bool = False,
    create_dirs: bool = True,
    expected_sha256: str | None = None,
) -> str:
    """Import an allowed binary file by letting the server download it from a URL (SSRF-hardened)."""
    try:
        if not _config.URL_IMPORT_ENABLED:
            return vault_json_dumps(
                {
                    "error": "URL import is disabled; set VAULT_IMPORT_URL_ENABLED=true to enable it",
                    "path": path,
                }
            )
        resolved = _validate_binary_target(path, media_type)
        if resolved.exists() and not overwrite:
            return vault_json_dumps(
                {
                    "error": f"File already exists: {path}. Set overwrite=true to replace it.",
                    "path": path,
                    "media_type": media_type,
                }
            )
        try:
            content_type, data = fetch_url(
                url,
                allow_private=_config.URL_ALLOW_PRIVATE,
                allowed_ports=_config.URL_ALLOWED_PORTS,
                max_bytes=_config.MAX_BYTES,
                max_redirects=_config.URL_MAX_REDIRECTS,
                timeout=_config.URL_TIMEOUT_SECONDS,
            )
        except (ImportSecurityError, ImportFetchError) as exc:
            return vault_json_dumps({"error": str(exc), "path": path, "media_type": media_type, "url": url})

        if content_type and content_type != media_type.strip().lower():
            return vault_json_dumps(
                {
                    "error": f"URL content-type '{content_type}' does not match requested media_type '{media_type}'",
                    "path": path,
                    "media_type": media_type,
                    "url": url,
                }
            )
        actual_sha256 = _sha256(data)
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            return vault_json_dumps(
                {
                    "error": "Downloaded content checksum mismatch",
                    "path": path,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                }
            )
        is_new, size = _write_bytes_atomic(resolved, data, create_dirs=create_dirs, overwrite=overwrite)
        return vault_json_dumps(
            {
                "path": path,
                "created": is_new,
                "size": size,
                "media_type": media_type,
                "sha256": actual_sha256,
                "source_url": url,
            }
        )
    except ValueError as exc:
        return vault_json_dumps({"error": str(exc), "path": path, "media_type": media_type, "url": url})
    except Exception as exc:  # noqa: BLE001
        logger.error("vault_import_url error for %s: %s", path, exc)
        return vault_json_dumps({"error": str(exc), "path": path, "media_type": media_type, "url": url})


def _validate_file_source(source_path: str) -> Path:
    """Validate a local source file against the explicit root allowlist (no traversal)."""
    roots = _config.allowed_file_roots()
    if not roots:
        raise ValueError("vault_import_file is disabled until VAULT_IMPORT_FILE_ALLOWED_ROOTS is configured")
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"Source file not found: {source_path}")
    if not source.is_file():
        raise ValueError(f"Source path is not a file: {source_path}")
    allowed_roots = [Path(r).expanduser().resolve() for r in roots]
    if not any(source == root or root in source.parents for root in allowed_roots):
        raise ValueError("Source path is outside VAULT_IMPORT_FILE_ALLOWED_ROOTS")
    return source


def vault_import_file(
    path: str,
    source_path: str,
    media_type: str,
    overwrite: bool = False,
    create_dirs: bool = True,
    expected_sha256: str | None = None,
) -> str:
    """Import an allowed binary file from a local allowlisted source path."""
    try:
        resolved = _validate_binary_target(path, media_type)
        source = _validate_file_source(source_path)
        if resolved.exists() and not overwrite:
            return vault_json_dumps(
                {
                    "error": f"File already exists: {path}. Set overwrite=true to replace it.",
                    "path": path,
                    "media_type": media_type,
                }
            )
        data = source.read_bytes()
        if len(data) > _config.MAX_BYTES:
            return vault_json_dumps(
                {
                    "error": f"Source content exceeds limit of {_config.MAX_BYTES} bytes",
                    "path": path,
                    "media_type": media_type,
                }
            )
        actual_sha256 = _sha256(data)
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            return vault_json_dumps(
                {
                    "error": "Source content checksum mismatch",
                    "path": path,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                }
            )
        is_new, size = _write_bytes_atomic(resolved, data, create_dirs=create_dirs, overwrite=overwrite)
        return vault_json_dumps(
            {
                "path": path,
                "created": is_new,
                "size": size,
                "media_type": media_type,
                "sha256": actual_sha256,
                "source_path": str(source),
            }
        )
    except ValueError as exc:
        return vault_json_dumps({"error": str(exc), "path": path, "media_type": media_type})
    except Exception as exc:  # noqa: BLE001
        logger.error("vault_import_file error for %s: %s", path, exc)
        return vault_json_dumps({"error": str(exc), "path": path, "media_type": media_type})
