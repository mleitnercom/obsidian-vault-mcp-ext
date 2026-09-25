"""Make extension tools visible in the host's audit log and to its write listeners.

The host (obsidian-web-mcp >= 0.4.0) audits an operation only when it knows the name:
its built-ins, plus whatever an extension declares with ``register_audit_operation``
(upstream #93). This module is the one place the extensions do that.

- ``declare({name: kind})`` from each extension's ``register_tools``. ``kind`` is
  ``"read"`` or ``"mutation"``, as for a built-in. The host refuses a name that collides
  with a built-in, so a clash shows at startup rather than as a misleading log line.
- ``audited(name, func)`` wraps a tool whose whole call is one operation: the read tools,
  and ``vault_reindex``. It goes through the host's ``run_audited``, so a read is recorded
  only with ``VAULT_AUDIT_LOG_INCLUDE_READS`` on, exactly like ``vault_read``.
- ``mutation(name, path)`` records one write per file with before/after snapshots and
  fires the host's write event. The tools that write several files (recurring
  instances, an encoding repair across the vault) use it per file; one record per tool
  call would hide which file changed.

Usage::

    with mutation("vault_template_apply", target_path) as m:
        is_new, _ = write_file_atomic(target_path, text)
        m.created = is_new

A clean exit records success and fires "created" or "updated" (or ``m.event`` when set,
e.g. "deleted"). An exception records an error, fires nothing, and propagates.
"""

import functools
import logging
from contextlib import contextmanager

from obsidian_vault_mcp.audit import (
    build_audit_record,
    register_audit_operation,
    run_audited,
    should_audit_operation,
    snapshot_path,
    write_audit_record,
)
from obsidian_vault_mcp.write_events import fire_write

logger = logging.getLogger(__name__)

# Arguments that name what a tool touches; passed to run_audited as record context.
_CONTEXT_ARGS = ("path", "source", "paths", "folder", "path_prefix", "template_path", "target_path")


def declare(operations: dict[str, str]) -> None:
    """Register each extension operation with the host's audit log."""
    for name, kind in operations.items():
        register_audit_operation(name, kind)


def audited(operation: str, func):
    """Wrap a tool so its whole call is one audited operation.

    ``functools.wraps`` keeps the signature visible, so the tool's MCP schema is built
    from ``func`` exactly as before.
    """

    @functools.wraps(func)
    def tool(*args, **kwargs):
        context = {key: kwargs[key] for key in _CONTEXT_ARGS if key in kwargs}
        return run_audited(operation, lambda: func(*args, **kwargs), **context)

    return tool


class _Mutation:
    def __init__(self) -> None:
        self.created: bool | None = None
        self.event: str | None = None
        self.paths: list[str] | None = None


@contextmanager
def mutation(operation: str, path: str, *, announce: bool = True):
    """Audit and announce one extension write to ``path`` (vault-relative).

    ``announce=False`` records the audit entry only, for a write that goes through a host
    tool implementation which fires its own write event (the compat tools wrap
    ``vault_edit``); announcing it again would tell every listener twice.
    """
    # The host decides: a declared mutation is audited whenever the log is on. An
    # undeclared name is not, the same rule the host applies to everything.
    auditing = should_audit_operation(operation)
    # Only hashed when a record will be written: snapshot_path reads the whole file.
    before = snapshot_path(path) if auditing else None
    state = _Mutation()
    try:
        yield state
    except Exception as exc:
        if auditing:
            write_audit_record(
                build_audit_record(
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
        after = None if event == "deleted" else snapshot_path(path)
        write_audit_record(
            build_audit_record(
                operation=operation,
                target_path=path,
                before=before,
                after=after,
                operation_status="success",
            )
        )
    if announce:
        fire_write(event, state.paths or [path])
