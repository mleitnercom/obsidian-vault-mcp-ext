"""Maintenance tools: scan/repair markdown encoding and soft-delete directories.

Ported from the fork's vault.py (scan_markdown_encoding_issues,
repair_markdown_encoding_issues, delete_directory_path) onto upstream-public APIs only:
resolve_vault_path / write_file_atomic from obsidian_vault_mcp.vault and dumps from
obsidian_vault_mcp.serialization, with VAULT_PATH read from obsidian_vault_mcp.config.
No host-core internals: the fork-only walk helpers (_included_root_paths /
is_vault_path_allowed) are replaced with a plain rglob that skips hidden/excluded dirs
and symlinks.
"""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from obsidian_vault_mcp.serialization import dumps as vault_json_dumps
from obsidian_vault_mcp.vault import resolve_vault_path, write_file_atomic

from . import _config as config

logger = logging.getLogger(__name__)


def _vault_root() -> Path:
    return config.VAULT_PATH.resolve()


def _is_hidden_or_excluded(rel_parts: tuple[str, ...]) -> bool:
    """True if any path component is a hidden/excluded dir (e.g. .git, .obsidian, .trash)."""
    return any(part.startswith(".") for part in rel_parts)


def _iter_markdown_files(path_prefix: str):
    """Yield (path, relative_str) for .md files under the scan root.

    Walks VAULT_PATH (or path_prefix under it when given). Skips any file whose
    vault-relative path includes a hidden/excluded component and skips symlinks.
    """
    vault_root = _vault_root()
    root = resolve_vault_path(path_prefix) if path_prefix else vault_root
    if not root.exists():
        raise FileNotFoundError(f"Path does not exist: {path_prefix}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {path_prefix}")

    for path in root.rglob("*.md"):
        try:
            rel = path.resolve().relative_to(vault_root)
        except ValueError:
            continue
        if _is_hidden_or_excluded(rel.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        yield path, rel.as_posix()


def vault_scan_encoding(path_prefix: str = "", max_results: int = 100) -> str:
    """Return markdown files under the vault that are not valid UTF-8."""
    try:
        issues: list[dict] = []
        for path, rel in _iter_markdown_files(path_prefix):
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                issues.append({"path": rel, "position": exc.start, "reason": exc.reason})
                if len(issues) >= max_results:
                    break
        return vault_json_dumps(
            {
                "path_prefix": path_prefix,
                "issues": issues,
                "count": len(issues),
                "truncated": len(issues) >= max_results,
            }
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return vault_json_dumps({"error": str(exc), "path": path_prefix})
    except Exception as exc:  # noqa: BLE001
        logger.error("vault_scan_encoding error for %s: %s", path_prefix, exc)
        return vault_json_dumps({"error": str(exc), "path": path_prefix})


def vault_repair_encoding(
    path_prefix: str = "",
    max_files: int = 50,
    source_encoding: str = "cp1252",
    dry_run: bool = False,
) -> str:
    """Repair markdown files that are not valid UTF-8 by re-decoding from source_encoding."""
    try:
        repaired: list[dict] = []
        failed: list[dict] = []
        truncated = False

        for path, rel in _iter_markdown_files(path_prefix):
            raw = path.read_bytes()
            try:
                raw.decode("utf-8")
                continue
            except UnicodeDecodeError:
                pass

            try:
                decoded = raw.decode(source_encoding)
            except UnicodeDecodeError as exc:
                failed.append(
                    {
                        "path": rel,
                        "source_encoding": source_encoding,
                        "position": exc.start,
                        "reason": exc.reason,
                    }
                )
            else:
                if not dry_run:
                    write_file_atomic(rel, decoded)
                repaired.append(
                    {
                        "path": rel,
                        "source_encoding": source_encoding,
                        "bytes_before": len(raw),
                        "bytes_after": len(decoded.encode("utf-8")),
                        "changed": not dry_run,
                    }
                )

            if len(repaired) + len(failed) >= max_files:
                truncated = True
                break

        return vault_json_dumps(
            {
                "path_prefix": path_prefix,
                "source_encoding": source_encoding,
                "dry_run": dry_run,
                "repaired": repaired,
                "failed": failed,
                "repaired_count": len(repaired),
                "failed_count": len(failed),
                "truncated": truncated,
            }
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return vault_json_dumps({"error": str(exc), "path": path_prefix})
    except Exception as exc:  # noqa: BLE001
        logger.error("vault_repair_encoding error for %s: %s", path_prefix, exc)
        return vault_json_dumps({"error": str(exc), "path": path_prefix})


def vault_delete_directory(path: str, only_if_empty: bool = True) -> str:
    """Soft-delete a directory by moving it into the vault's trash folder."""
    try:
        resolved = resolve_vault_path(path)
        if not resolved.exists():
            return vault_json_dumps({"error": f"Path does not exist: {path}", "path": path})
        if not resolved.is_dir():
            return vault_json_dumps({"error": f"Not a directory: {path}", "path": path})
        if only_if_empty and any(resolved.iterdir()):
            return vault_json_dumps(
                {
                    "error": f"Refusing to delete non-empty directory: {path}. "
                    "Set only_if_empty=false to delete it anyway.",
                    "path": path,
                }
            )

        trash_dir = _vault_root() / config.TRASH_DIR_NAME
        trash_dir.mkdir(exist_ok=True)

        dest = trash_dir / resolved.name
        if dest.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            dest = trash_dir / f"{resolved.name}_{ts}"

        shutil.move(str(resolved), str(dest))
        trashed_to = dest.resolve().relative_to(_vault_root()).as_posix()
        return vault_json_dumps({"path": path, "deleted": True, "trashed_to": trashed_to})
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        return vault_json_dumps({"error": str(exc), "path": path})
    except Exception as exc:  # noqa: BLE001
        logger.error("vault_delete_directory error for %s: %s", path, exc)
        return vault_json_dumps({"error": str(exc), "path": path})
