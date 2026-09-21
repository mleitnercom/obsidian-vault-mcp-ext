"""Make extension writes visible the way the host's own writes are.

The host's mutation tools run through its private audit wrapper and fire a write event.
Tools registered by an extension do neither on their own, so without this module a
template apply, a recurring instance, an import or an encoding repair would change the
vault while the audit log and every write listener (an index, a git committer, another
extension) saw nothing. The host README promises one audit record per mutation; this is
how the extensions keep that promise.

Everything here uses only public host API: ``write_events.fire_write`` and the audit
module's ``audit_enabled`` / ``snapshot_path`` / ``build_audit_record`` /
``write_audit_record``. On a host older than those (before #56/#62) the calls are
no-ops, so the extensions still run.

Usage::

    with mutation("vault_template_apply", target_path) as m:
        is_new, _ = write_file_atomic(target_path, text)
        m.created = is_new

A clean exit records success and fires "created" or "updated" (or ``m.event`` when set,
e.g. "deleted"). An exception records an error, fires nothing, and propagates.
"""

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

try:  # host >= #62
    from obsidian_vault_mcp.write_events import fire_write as _fire_write
except ImportError:  # pragma: no cover - older host
    _fire_write = None

try:  # host >= #56
    from obsidian_vault_mcp.audit import (
        audit_enabled as _audit_enabled,
        build_audit_record as _build_audit_record,
        snapshot_path as _snapshot_path,
        write_audit_record as _write_audit_record,
    )
except ImportError:  # pragma: no cover - older host
    _audit_enabled = None


class _Mutation:
    def __init__(self) -> None:
        self.created: bool | None = None
        self.event: str | None = None
        self.paths: list[str] | None = None


def _auditing() -> bool:
    return bool(_audit_enabled and _audit_enabled())


@contextmanager
def mutation(operation: str, path: str):
    """Audit and announce one extension write to ``path`` (vault-relative)."""
    auditing = _auditing()
    # Only hashed when a record will be written: snapshot_path reads the whole file.
    before = _snapshot_path(path) if auditing else None
    state = _Mutation()
    try:
        yield state
    except Exception as exc:
        if auditing:
            _write_audit_record(
                _build_audit_record(
                    operation=operation,
                    target_path=path,
                    before=before,
                    operation_status="error",
                    error=str(exc) or type(exc).__name__,
                )
            )
        raise
    event = state.event or ("created" if state.created else "updated")
    if auditing:
        after = None if event == "deleted" else _snapshot_path(path)
        _write_audit_record(
            _build_audit_record(
                operation=operation,
                target_path=path,
                before=before,
                after=after,
                operation_status="success",
            )
        )
    if _fire_write is not None:
        _fire_write(event, state.paths or [path])
