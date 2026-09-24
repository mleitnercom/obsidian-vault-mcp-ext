"""Fork-era tool names on top of the host's own tools.

Clients and skills written against the fork call vault_str_replace, vault_patch,
vault_batch_replace and vault_tree (together over 6,200 calls a month in production).
Upstream has the behaviour but not the names: vault_edit replaces text exactly once, or
everywhere with replace_all (#86), and vault_list walks directories. These wrappers keep
the old names and response fields working without a fork.

Every text change goes through the host's vault_edit implementation, so everything
upstream enforces on an edit applies unchanged: containment, the hardlink refusal, the
refusal to decode binaries, atomic writes, near-miss diagnostics and the write event.
The wrappers add only the audit record, which the host writes in its tool wrapper rather
than inside vault_edit.
"""

import json
import logging
from pathlib import Path

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp.serialization import dumps
from obsidian_vault_mcp.tools.write import vault_edit as _host_vault_edit
from obsidian_vault_mcp.vault import resolve_vault_path

from .._mutations import mutation

logger = logging.getLogger(__name__)

MAX_BATCH = 20
MAX_TREE_DEPTH = 5


def _size_of(path: str) -> int | None:
    try:
        return resolve_vault_path(path).stat().st_size
    except (OSError, ValueError):
        return None


class _EditFailed(Exception):
    """Raised inside the audit context so a refused edit is recorded as an error."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("error", "edit failed"))
        self.payload = payload


def _replace(operation: str, path: str, old: str, new: str, replace_all: bool) -> dict:
    """One exact replacement through the host's vault_edit, in the fork's result shape."""
    size_before = _size_of(path)
    try:
        with mutation(operation, path, announce=False):
            result = _host_vault_edit_payload(path, old, new, replace_all)
            if "error" in result:
                raise _EditFailed(result)
    except _EditFailed as failed:
        return failed.payload
    size_after = result.get("size")
    return {
        "path": path,
        "replaced": True,
        "changed": result.get("changed", False),
        "occurrences_found": result.get("replacements", 1),
        "size_before": size_before,
        "size_after": size_after,
        "size_delta": (size_after - size_before) if size_before is not None and size_after is not None else None,
        "replace_all": replace_all,
    }


def _host_vault_edit_payload(path: str, old: str, new: str, replace_all: bool) -> dict:
    raw = _host_vault_edit(path, [{"old_text": old, "new_text": new, "replace_all": replace_all}])
    try:
        payload = json.loads(raw)
    except ValueError:
        return {"error": "unexpected response from vault_edit", "path": path}
    if "error" in payload:
        payload.setdefault("path", path)
    return payload


def vault_str_replace(path: str, old_string: str, new_string: str = "", replace_all: bool = False) -> str:
    """Replace an exact string; unique by default, every occurrence with replace_all."""
    return dumps(_replace("vault_str_replace", path, old_string, new_string, replace_all))


def vault_patch(path: str, old_text: str, new_text: str = "") -> str:
    """Replace one unique exact occurrence of old_text."""
    return dumps(_replace("vault_patch", path, old_text, new_text, False))


def vault_batch_replace(updates: list[dict]) -> str:
    """Replace exact strings across files; each update succeeds or fails on its own."""
    if not isinstance(updates, list):
        return dumps({"error": "updates must be a list"})
    if len(updates) > MAX_BATCH:
        return dumps({"error": f"at most {MAX_BATCH} updates per call, got {len(updates)}"})
    results = []
    for update in updates:
        if not isinstance(update, dict):
            results.append({"error": "each update must be an object"})
            continue
        path = update.get("path", "")
        old = update.get("old_str", update.get("old_string", ""))
        new = update.get("new_str", update.get("new_string", ""))
        results.append(_replace("vault_batch_replace", path, old, new, bool(update.get("replace_all", False))))
    return dumps({"results": results})


def _skip(entry: Path) -> bool:
    return entry.name.startswith(".") or entry.name in host_config.EXCLUDED_DIRS or entry.is_symlink()


def vault_tree(path: str = "", depth: int = 3) -> str:
    """Nested JSON tree of the vault's directories and files, as the fork returned it."""
    try:
        vault_root = host_config.VAULT_PATH.resolve()
        start = resolve_vault_path(path) if path else vault_root
        if not start.is_dir():
            return dumps({"error": f"Not a directory: {path}"})
        depth = max(0, min(int(depth), MAX_TREE_DEPTH))

        def count(directory: Path) -> tuple[int, int]:
            files = dirs = 0
            try:
                for child in directory.iterdir():
                    if _skip(child):
                        continue
                    if child.is_file():
                        files += 1
                    elif child.is_dir():
                        dirs += 1
            except OSError:
                pass
            return files, dirs

        def build(directory: Path, level: int) -> dict:
            node = {"name": directory.name, "files": [], "dirs": []}
            try:
                entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                return node
            for entry in entries:
                if _skip(entry):
                    continue
                if entry.is_file():
                    node["files"].append(entry.name)
                elif entry.is_dir():
                    if level < depth:
                        node["dirs"].append(build(entry, level + 1))
                    else:
                        files, dirs = count(entry)
                        node["dirs"].append({"name": entry.name, "file_count": files, "dir_count": dirs})
            return node

        tree = build(start, 0)
        tree["path"] = path or "/"
        return dumps(tree)
    except ValueError as exc:
        return dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.error("vault_tree error: %s", exc)
        return dumps({"error": str(exc)})
