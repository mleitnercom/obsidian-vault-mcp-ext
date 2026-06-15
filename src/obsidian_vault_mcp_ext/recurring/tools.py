"""Recurring task materialization, ported onto upstream-public APIs only.

This module turns ``type: recurring-template`` notes in the vault into concrete
task instances. Templates declare WHEN a new instance becomes due (an absolute
anchor expression or a relative interval), WHAT it should look like (inherited
frontmatter, tags, priority, due-offset) and WHERE to put it.

The tool is strictly idempotent: a second invocation for the same template and
period yields no new files.

Port adaptations vs the fork's tools/recurring.py
-------------------------------------------------
- ``from .. import config`` -> ``from . import _config as config``.
- ``vault_json_dumps`` -> ``from obsidian_vault_mcp.serialization import dumps``.
- ``frontmatter_io`` (fork ruamel module) -> the ``frontmatter`` library via
  ``helpers.parse_frontmatter`` / ``helpers.dump_frontmatter``.
- ``is_vault_path_allowed`` (fork-only) -> dropped; ``resolve_vault_path`` already
  fails closed on path escapes, and ``EXCLUDED_DIRS`` are skipped while walking.
- ``hooks.fire_post_write`` -> dropped (host-core internal; not part of the tool
  contract).
- KEY ADAPTATION -- idempotency lookup: the fork queried the host's in-memory
  frontmatter index for ``(recurrence_template, recurrence_period)``. Upstream does
  not expose that query, so we enumerate the configured instance folder on disk,
  read each instance file's frontmatter with the ``frontmatter`` library, and build
  the set of already-materialized ``(template, period)`` pairs that way. The same
  on-disk scan also supplies the relative-mode "last done" lookup.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from obsidian_vault_mcp.serialization import dumps as vault_json_dumps
from obsidian_vault_mcp.vault import read_file, resolve_vault_path, write_file_atomic

from . import _config as config
from .helpers import (
    AnchorError,
    TriggeredPeriod,
    coerce_date,
    compute_pending_periods,
    compute_relative_period,
    dump_frontmatter,
    format_iso,
    parse_frontmatter,
)

logger = logging.getLogger(__name__)

_EXCLUDED_DIR_NAMES = {".obsidian", ".trash", ".git", ".DS_Store"}


def _today() -> date:
    """Server-local current date. Indirected so tests can patch."""
    return datetime.now().date()


def _vault_root() -> Path:
    return config.VAULT_PATH.resolve()


# --------------------------------------------------------------------------
# Template enumeration
# --------------------------------------------------------------------------


def _list_markdown_paths(folder: str) -> list[str]:
    """List markdown files inside *folder*, relative to the vault root (POSIX)."""
    folder_clean = folder.strip().strip("/\\")
    if not folder_clean:
        return []
    try:
        base_dir = resolve_vault_path(folder_clean)
    except ValueError:
        return []
    if not base_dir.is_dir():
        return []
    root = _vault_root()
    paths: list[str] = []
    for md_path in sorted(base_dir.rglob("*.md")):
        if md_path.is_symlink() or not md_path.is_file():
            continue
        if any(part in _EXCLUDED_DIR_NAMES for part in md_path.parts):
            continue
        try:
            rel = md_path.relative_to(root).as_posix()
        except ValueError:
            continue
        paths.append(rel)
    return paths


def _read_template(rel_path: str) -> tuple[dict[str, Any], str] | None:
    """Parse a template/instance file and return (metadata, body), or None."""
    try:
        content, _meta = read_file(rel_path)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Could not read note %s: %s", rel_path, exc)
        return None
    try:
        metadata, body = parse_frontmatter(content)
    except Exception as exc:
        logger.warning("Could not parse frontmatter in %s: %s", rel_path, exc)
        return None
    return metadata, body


def _is_active_recurring_template(meta: dict[str, Any]) -> bool:
    if not meta:
        return False
    if meta.get("type") != "recurring-template":
        return False
    active = meta.get("active", True)
    if isinstance(active, str):
        return active.strip().lower() in {"1", "true", "yes", "on"}
    return bool(active)


# --------------------------------------------------------------------------
# Instance path / content construction
# --------------------------------------------------------------------------


def _instance_dir_for(
    template_meta: dict[str, Any],
    template_path: str,
    warnings: list[str] | None = None,
) -> str:
    """Return the vault-relative POSIX directory where instances should be written.

    Resolution: ``target_folder`` (canonical) > ``instance_folder`` (legacy alias)
    > the template's own parent directory (sibling fallback).
    """
    target = template_meta.get("target_folder")
    alias = template_meta.get("instance_folder")

    canonical = target if isinstance(target, str) and target.strip() else None
    legacy = alias if isinstance(alias, str) and alias.strip() else None

    if canonical is not None and legacy is not None:
        if warnings is not None:
            warnings.append(
                "both 'target_folder' and 'instance_folder' set; "
                "'target_folder' takes precedence"
            )
        return canonical.strip().strip("/\\")
    if canonical is not None:
        return canonical.strip().strip("/\\")
    if legacy is not None:
        if warnings is not None:
            warnings.append("'instance_folder' is a legacy alias; prefer 'target_folder'")
        return legacy.strip().strip("/\\")

    parent = PurePosixPath(template_path).parent.as_posix()
    return parent if parent and parent != "." else ""


def _build_instance_filename(template_id: str, period_key: str) -> str:
    """Build the slug used as the instance filename."""
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(template_id)).strip("-")
    safe_period = re.sub(r"[^A-Za-z0-9_-]+", "-", str(period_key)).strip("-")
    if not safe_id:
        safe_id = "tpl"
    if not safe_period:
        safe_period = "period"
    return f"recurring-{safe_id}-{safe_period}.md"


def _instance_relpath(
    template_meta: dict[str, Any],
    template_path: str,
    filename: str,
    warnings: list[str] | None = None,
) -> str:
    folder = _instance_dir_for(template_meta, template_path, warnings)
    if folder:
        return f"{folder}/{filename}"
    return filename


def _resolve_inheritance(
    template_meta: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    """Build the inherited frontmatter map per the canonical-with-alias schema.

    Canonical: ``frontmatter_to_inherit`` is a dict copied verbatim onto the
    instance. Legacy alias: a list of key names looked up in the template
    frontmatter (emits a deprecation warning).
    """
    raw = template_meta.get("frontmatter_to_inherit")
    if raw is None:
        return {}

    inherited: dict[str, Any] = {}

    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(key, str) or not key.strip():
                warnings.append("'frontmatter_to_inherit' contains a non-string key; ignored")
                continue
            inherited[key] = value
        if not inherited:
            warnings.append(
                "'frontmatter_to_inherit' is configured as a dict but resolved to no fields"
            )
        return inherited

    if isinstance(raw, list):
        warnings.append(
            "'frontmatter_to_inherit' as a list of keys is a legacy form; "
            "prefer the dict {key: value} form"
        )
        for key in raw:
            if not isinstance(key, str) or not key.strip():
                continue
            if key in template_meta:
                inherited[key] = template_meta[key]
        if not inherited:
            warnings.append(
                "'frontmatter_to_inherit' (list form) is configured but no listed "
                "key was present in the template frontmatter; nothing inherited"
            )
        return inherited

    warnings.append(
        "'frontmatter_to_inherit' must be a dict (canonical) or list (legacy); "
        f"got {type(raw).__name__}; ignored"
    )
    return inherited


def _build_instance_content(
    *,
    template_id: str,
    template_meta: dict[str, Any],
    period_key: str,
    trigger_date: date,
    warnings: list[str] | None = None,
) -> str:
    """Render the instance markdown (frontmatter + optional body)."""
    sink: list[str] = warnings if warnings is not None else []
    due_offset = int(template_meta.get("due_offset_days", 0) or 0)
    priority_initial = template_meta.get("priority_initial")
    tags_to_inherit = template_meta.get("tags_to_inherit") or []
    title_template = template_meta.get("instance_title")

    metadata: dict[str, Any] = {}
    metadata["id"] = f"recurring-{template_id}-{period_key}"
    if title_template:
        metadata["title"] = str(title_template).format(
            template_id=template_id,
            period=period_key,
            trigger=format_iso(trigger_date),
        )
    metadata["recurrence_template"] = str(template_id)
    metadata["recurrence_period"] = str(period_key)
    metadata["source"] = f"recurring-{template_id}-{period_key}"
    metadata["created"] = format_iso(_today())
    metadata["due"] = format_iso(trigger_date + timedelta(days=due_offset))

    if priority_initial is not None:
        metadata["priority"] = priority_initial

    for key, value in _resolve_inheritance(template_meta, sink).items():
        metadata[key] = value

    tags: list[str] = ["recurring-instance"]
    if isinstance(tags_to_inherit, list):
        for tag in tags_to_inherit:
            if isinstance(tag, str) and tag and tag not in tags:
                tags.append(tag)
    metadata["tags"] = tags

    body = ""
    body_template = template_meta.get("body_template")
    if isinstance(body_template, str) and body_template:
        body = body_template.format(
            template_id=template_id,
            period=period_key,
            trigger=format_iso(trigger_date),
            due=metadata["due"],
        )
        if not body.endswith("\n"):
            body += "\n"
    return dump_frontmatter(metadata, body)


# --------------------------------------------------------------------------
# Disk-based idempotency / last-done lookup
#
# Replaces the fork's in-memory frontmatter-index query, which upstream does
# not expose. We enumerate the instance folder on disk, read each instance
# file's frontmatter, and index existing (template, period) pairs.
# --------------------------------------------------------------------------


def _scan_instances(folder: str) -> list[tuple[str, dict[str, Any]]]:
    """Read every markdown file under *folder* and return (rel_path, frontmatter)."""
    instances: list[tuple[str, dict[str, Any]]] = []
    for rel_path in _list_markdown_paths(folder):
        parsed = _read_template(rel_path)
        if parsed is None:
            continue
        meta, _body = parsed
        if not meta:
            continue
        instances.append((rel_path, meta))
    return instances


def _build_period_index(instances: list[tuple[str, dict[str, Any]]]) -> dict[tuple[str, str], str]:
    """Map ``(recurrence_template, recurrence_period)`` -> instance relative path."""
    index: dict[tuple[str, str], str] = {}
    for rel_path, meta in instances:
        tmpl = meta.get("recurrence_template")
        period = meta.get("recurrence_period")
        if tmpl is None or period is None:
            continue
        key = (str(tmpl), str(period))
        # First write wins; deterministic because _list_markdown_paths sorts.
        index.setdefault(key, rel_path)
    return index


def _last_done_for(
    template_id: str, instances: list[tuple[str, dict[str, Any]]]
) -> date | None:
    """Most recent done instance date for relative-mode templates (disk-based)."""
    done_status = config.VAULT_RECURRING_DONE_STATUS
    best: date | None = None
    for _rel_path, meta in instances:
        if str(meta.get("recurrence_template")) != str(template_id):
            continue
        status = meta.get("status")
        if status is None or str(status) != str(done_status):
            continue
        for candidate_key in ("closed", "done_at", "completed", "due", "updated", "created"):
            candidate = coerce_date(meta.get(candidate_key))
            if candidate is None:
                continue
            if best is None or candidate > best:
                best = candidate
            break
    return best


def _write_text_verified(rel_path: str, content: str) -> int:
    """Atomic write + read-back verification."""
    _, size = write_file_atomic(rel_path, content, create_dirs=True)
    written_back, _ = read_file(rel_path)
    if written_back != content:
        raise RuntimeError(f"Recurring instance write verification failed for {rel_path!r}")
    return size


def _update_template_last_run(
    template_path: str,
    template_meta: dict[str, Any],
    template_body: str,
    last_run: date,
) -> None:
    """Update the template's ``last_run`` frontmatter to ``last_run``."""
    new_meta = dict(template_meta)
    new_meta["last_run"] = format_iso(last_run)
    new_content = dump_frontmatter(new_meta, template_body)
    _write_text_verified(template_path, new_content)


# --------------------------------------------------------------------------
# Per-template processing
# --------------------------------------------------------------------------


def _process_template(
    *,
    template_path: str,
    template_meta: dict[str, Any],
    template_body: str,
    as_of: date,
    catchup: str,
    dry_run: bool,
    period_index: dict[tuple[str, str], str],
    instances: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Process one template; return its contribution to the aggregated result."""
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    template_id = template_meta.get("id") or template_meta.get("template_id")
    if not template_id:
        errors.append(
            {"path": template_path, "error": "template missing 'id' or 'template_id' frontmatter"}
        )
        return {"created": created, "skipped": skipped, "errors": errors, "warnings": warnings}
    template_id = str(template_id)

    anchor_mode = (template_meta.get("recurrence_anchor_mode") or "").strip().lower()
    if anchor_mode not in {"absolute", "relative"}:
        errors.append(
            {
                "path": template_path,
                "template_id": template_id,
                "error": (
                    "template missing or invalid 'recurrence_anchor_mode' "
                    "(must be 'absolute' or 'relative')"
                ),
            }
        )
        return {"created": created, "skipped": skipped, "errors": errors, "warnings": warnings}

    try:
        if anchor_mode == "absolute":
            anchor = (template_meta.get("recurrence_anchor") or "").strip()
            if not anchor:
                raise AnchorError("template missing 'recurrence_anchor' for absolute mode")
            since = coerce_date(template_meta.get("last_run"))
            implicit_baseline_used = False
            if since is None:
                # Bootstrap: use the template's `created` date as an implicit
                # baseline (minus one day so a trigger ON the baseline qualifies).
                implicit = coerce_date(template_meta.get("created"))
                if implicit is not None:
                    since = implicit - timedelta(days=1)
                    implicit_baseline_used = True
            periods = compute_pending_periods(anchor, as_of, since, catchup=catchup)
            if not periods and (since is None or implicit_baseline_used):
                skipped.append(
                    {"path": template_path, "template_id": template_id, "reason": "not_due"}
                )
                return {
                    "created": created,
                    "skipped": skipped,
                    "errors": errors,
                    "warnings": warnings,
                }
        else:
            interval_spec = (template_meta.get("recurrence_interval") or "").strip()
            if not interval_spec:
                raise AnchorError("template missing 'recurrence_interval' for relative mode")
            last_done = _last_done_for(template_id, instances) or coerce_date(
                template_meta.get("last_run")
            )
            if last_done is None:
                # Bootstrap: a freshly installed relative template fires once
                # with trigger=today so the cadence has a starting point.
                periods = [TriggeredPeriod(as_of, format_iso(as_of))]
            else:
                candidate = compute_relative_period(interval_spec, last_done)
                if candidate.trigger_date > as_of:
                    skipped.append(
                        {
                            "path": template_path,
                            "template_id": template_id,
                            "reason": "not_yet_due",
                            "next_trigger": format_iso(candidate.trigger_date),
                        }
                    )
                    return {
                        "created": created,
                        "skipped": skipped,
                        "errors": errors,
                        "warnings": warnings,
                    }
                periods = [candidate]
    except AnchorError as exc:
        errors.append({"path": template_path, "template_id": template_id, "error": str(exc)})
        return {"created": created, "skipped": skipped, "errors": errors, "warnings": warnings}

    if not periods:
        skipped.append(
            {"path": template_path, "template_id": template_id, "reason": "no_pending_periods"}
        )
        return {"created": created, "skipped": skipped, "errors": errors, "warnings": warnings}

    last_processed_trigger: date | None = None
    for period in periods:
        existing = period_index.get((template_id, period.period_key))
        if existing:
            skipped.append(
                {
                    "template_id": template_id,
                    "period": period.period_key,
                    "reason": "already_exists",
                    "existing_path": existing,
                }
            )
            continue

        filename = _build_instance_filename(template_id, period.period_key)
        warn_messages: list[str] = []
        rel_path = _instance_relpath(template_meta, template_path, filename, warn_messages)
        content = _build_instance_content(
            template_id=template_id,
            template_meta=template_meta,
            period_key=period.period_key,
            trigger_date=period.trigger_date,
            warnings=warn_messages,
        )
        for msg in warn_messages:
            warnings.append(
                {"template_id": template_id, "period": period.period_key, "warning": msg}
            )
        if dry_run:
            created.append(
                {
                    "template_id": template_id,
                    "period": period.period_key,
                    "path": rel_path,
                    "trigger_date": format_iso(period.trigger_date),
                    "dry_run": True,
                }
            )
            last_processed_trigger = period.trigger_date
            continue

        try:
            size = _write_text_verified(rel_path, content)
        except Exception as exc:
            errors.append(
                {
                    "template_id": template_id,
                    "period": period.period_key,
                    "path": rel_path,
                    "error": str(exc),
                }
            )
            continue
        # Keep the in-memory index current so a single run cannot create the
        # same (template, period) twice (e.g. catchup='all' duplicates).
        period_index[(template_id, period.period_key)] = rel_path
        created.append(
            {
                "template_id": template_id,
                "period": period.period_key,
                "path": rel_path,
                "trigger_date": format_iso(period.trigger_date),
                "size": size,
            }
        )
        last_processed_trigger = period.trigger_date

    if not dry_run and last_processed_trigger is not None:
        try:
            _update_template_last_run(
                template_path, template_meta, template_body, last_processed_trigger
            )
        except Exception as exc:
            errors.append(
                {
                    "path": template_path,
                    "template_id": template_id,
                    "error": f"could not update template last_run: {exc}",
                }
            )

    return {"created": created, "skipped": skipped, "errors": errors, "warnings": warnings}


# --------------------------------------------------------------------------
# Public tool entry point
# --------------------------------------------------------------------------


def recurring_materialize(
    dry_run: bool = False,
    template_id: str | None = None,
    as_of: str | None = None,
) -> str:
    """Materialize pending recurring-template instances.

    Parameters
    ----------
    dry_run:
        If True, compute what would be created but make no filesystem changes
        and do not touch template ``last_run``.
    template_id:
        Restrict processing to a single template id. If not found, the response
        lists it under ``errors``.
    as_of:
        ISO date (YYYY-MM-DD) overriding the "current date" used for anchor
        resolution. Useful for backfills and tests. Defaults to today.

    Returns a JSON string of
    ``{checked, created, skipped, errors, warnings, dry_run, as_of, catchup_mode}``.
    """
    if not config.VAULT_RECURRING_ENABLED:
        return vault_json_dumps(
            {
                "error": "recurring materialization is disabled (VAULT_RECURRING_ENABLED=false)",
                "error_code": "recurring_disabled",
            }
        )

    folder = config.VAULT_RECURRING_TEMPLATES_FOLDER
    if not folder:
        return vault_json_dumps(
            {
                "error": "recurring materialization requires VAULT_RECURRING_TEMPLATES_FOLDER",
                "error_code": "recurring_folder_unset",
            }
        )

    try:
        as_of_date = date.fromisoformat(as_of) if isinstance(as_of, str) and as_of else _today()
    except ValueError:
        return vault_json_dumps(
            {
                "error": f"invalid as_of value: {as_of!r} (expected YYYY-MM-DD)",
                "error_code": "invalid_as_of",
            }
        )

    catchup = config.VAULT_RECURRING_CATCHUP_MODE
    template_paths = _list_markdown_paths(folder)

    # Disk-based idempotency: scan the instance folder of every template once,
    # de-duplicated, and index existing (template, period) pairs. Templates
    # share the folder scan when their instance dirs coincide.
    instance_cache: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    def instances_for(instance_folder: str) -> list[tuple[str, dict[str, Any]]]:
        if instance_folder not in instance_cache:
            instance_cache[instance_folder] = _scan_instances(instance_folder)
        return instance_cache[instance_folder]

    aggregate_created: list[dict[str, Any]] = []
    aggregate_skipped: list[dict[str, Any]] = []
    aggregate_errors: list[dict[str, Any]] = []
    aggregate_warnings: list[dict[str, Any]] = []
    checked = 0
    matched_filter = False

    for path in template_paths:
        parsed = _read_template(path)
        if parsed is None:
            continue
        meta, body = parsed
        if not _is_active_recurring_template(meta):
            aggregate_skipped.append(
                {"path": path, "reason": "not_recurring_template_or_inactive"}
            )
            continue
        if template_id and str(meta.get("id") or meta.get("template_id")) != template_id:
            continue
        matched_filter = True
        checked += 1

        instance_folder = _instance_dir_for(meta, path)
        instances = instances_for(instance_folder)
        period_index = _build_period_index(instances)

        result = _process_template(
            template_path=path,
            template_meta=meta,
            template_body=body,
            as_of=as_of_date,
            catchup=catchup,
            dry_run=dry_run,
            period_index=period_index,
            instances=instances,
        )
        # Reflect newly-created instances into the shared cache so a later
        # template targeting the same folder sees them within this run.
        for item in result["created"]:
            if not dry_run and "size" in item:
                instances.append(
                    (
                        item["path"],
                        {
                            "recurrence_template": item["template_id"],
                            "recurrence_period": item["period"],
                        },
                    )
                )
        aggregate_created.extend(result["created"])
        aggregate_skipped.extend(result["skipped"])
        aggregate_errors.extend(result["errors"])
        aggregate_warnings.extend(result.get("warnings", []))

    if template_id and not matched_filter:
        aggregate_errors.append({"template_id": template_id, "error": "template id not found"})

    return vault_json_dumps(
        {
            "checked": checked,
            "created": aggregate_created,
            "skipped": aggregate_skipped,
            "errors": aggregate_errors,
            "warnings": aggregate_warnings,
            "dry_run": dry_run,
            "as_of": format_iso(as_of_date),
            "catchup_mode": catchup,
        }
    )
