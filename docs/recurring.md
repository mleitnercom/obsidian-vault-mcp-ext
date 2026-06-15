# Recurring Task Materialization (Task-OS)

Back to [README](../README.md).

`RecurringExtension` turns `type: recurring-template` notes in the vault into
concrete task instances. A template declares **WHEN** a new instance becomes due
(an absolute calendar anchor or a relative interval), **WHAT** the instance looks
like (inherited frontmatter, tags, priority, due-offset, title, body) and
**WHERE** it lands. The single MCP tool `recurring_materialize` does the work.

The extension is **client-agnostic**: scopes, project slugs, status values and
folder layouts are all plain strings configured per template. It does not
validate them against any particular task-OS schema.

The extension ships with **no extra dependencies** (the `frontmatter` library it
uses is already a host dependency).

## Why

A vault full of markdown task notes accumulates recurring work: a governance
report every quarter end, a review every 28th, a prep task 3 days before a fixed
deadline, a follow-up 7 days after the last one was done. Materializing those by
hand is error-prone and easy to forget. This tool generates the instances and is
**strictly idempotent**: running it twice for the same template and period
produces nothing new.

## How idempotency actually works (port note)

The upstream fork queried the host's in-memory frontmatter index for existing
`(recurrence_template, recurrence_period)` pairs. **This package does not.**
Upstream does not expose that query, so the port replaced it with an **on-disk
scan of the instance folder**:

1. For each active template, the tool resolves its instance folder and reads
   every markdown file under it (recursively), parsing frontmatter with the
   `frontmatter` library.
2. It builds an index of already-materialized `(recurrence_template,
   recurrence_period)` pairs from those files. If a candidate period is already
   in the index, the instance is `skipped` with reason `already_exists`.
3. The same scan supplies the **relative-mode "last done" lookup** (the most
   recent instance whose `status` equals `VAULT_RECURRING_DONE_STATUS`).

Consequences of the disk-based mechanism:

- Idempotency survives a wiped `last_run`, manually created instance files and
  prior dry runs, because it reads the actual files on disk, not a mutable index.
- The scan is per instance folder and **cached within a single run**, so several
  templates that target the same folder share one scan. Newly created instances
  are also reflected back into the cache mid-run, so `catchup=all` cannot create
  the same `(template, period)` twice.
- Excluded directories (`.obsidian`, `.trash`, `.git`, `.DS_Store`) and symlinks
  are skipped while walking.
- Path safety: folders resolve through the host's `resolve_vault_path`, which
  fails closed on any path escaping the vault root.

## Configuration

All knobs are read from `VAULT_RECURRING_*` environment variables at import time.

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `VAULT_RECURRING_ENABLED` | bool | `true` | Master switch. When false the tool returns `{"error_code": "recurring_disabled"}`. |
| `VAULT_RECURRING_TEMPLATES_FOLDER` | string | _(empty)_ | Vault-relative folder scanned for template notes. **Required**; the tool returns `recurring_folder_unset` when empty. |
| `VAULT_RECURRING_DONE_STATUS` | string | `done` | The `status` frontmatter value that marks an instance "completed" for relative-mode last-done lookup. |
| `VAULT_RECURRING_CATCHUP_MODE` | `next` \| `all` | `next` | Behavior when several absolute periods are pending. `next` keeps only the most recent; `all` materializes one instance per missed period. |
| `VAULT_RECURRING_INTERVAL` | int (seconds) | `0` | Read from the environment but **not used in this port** (see note below). |

Boolean parsing accepts `1/true/yes/on` (case-insensitive). `VAULT_PATH` is taken
dynamically from the host server config (not snapshotted), so a monkeypatched
vault in tests takes effect.

> **No internal scheduler in this port.** The fork ran an in-process asyncio
> scheduler loop tied to the server lifespan when `VAULT_RECURRING_INTERVAL > 0`.
> This extension intentionally leaves that out of scope: materialization is a
> tool, driven on demand (MCP call) or by an external timer calling the CLI.
> `VAULT_RECURRING_INTERVAL` is still parsed but has no effect; drive cadence with
> a systemd timer / cron / Task Scheduler instead (see [CLI](#cli)).

## Template schema

A template is any markdown file inside `VAULT_RECURRING_TEMPLATES_FOLDER` with
`type: recurring-template`. It is processed only while **active** (`active` is
truthy; defaults to true when absent — string values `1/true/yes/on` count as
true). Inactive or non-template files are reported under `skipped` with reason
`not_recurring_template_or_inactive`.

```yaml
---
id: q-governance                  # required; used in instance ids + idempotency keys
type: recurring-template
active: true
recurrence_anchor_mode: absolute  # 'absolute' or 'relative' (required)
recurrence_anchor: quarter_end_plus_3d   # required when mode = absolute
# recurrence_interval: 7d         # required when mode = relative ('Nd' / 'Nm')
created: 2026-07-01               # implicit bootstrap baseline (absolute mode)
due_offset_days: 0                # added to the trigger date -> instance 'due'
priority_initial: 2               # written into the instance as 'priority'
target_folder: 15_Tasks/pbs       # where instances go; defaults to template's folder
instance_title: "Q-Governance {period}"   # optional; {template_id} {period} {trigger}
frontmatter_to_inherit:           # canonical: a map copied verbatim onto the instance
  scope: pbs
  project: governance
tags_to_inherit:                  # appended after the implicit 'recurring-instance' tag
  - quarterly
  - reporting
body_template: |                  # optional instance body; {template_id} {period} {trigger} {due}
  Quarterly governance review for {period}. Due {due}.
# last_run: managed by the tool — do not hand-edit except to reseed the baseline
---

Optional template body (ignored unless you use body_template for the instance).
```

### Required fields and where they are validated

| Field | Required | Failure if missing/invalid |
|---|---|---|
| `id` (or `template_id`) | yes | `errors`: "template missing 'id' or 'template_id'" |
| `type: recurring-template` | yes | silently `skipped` (not treated as a template) |
| `recurrence_anchor_mode` | yes | `errors`: must be `absolute` or `relative` |
| `recurrence_anchor` | absolute only | `errors`: "template missing 'recurrence_anchor'" |
| `recurrence_interval` | relative only | `errors`: "template missing 'recurrence_interval'" |

`id` and `template_id` are interchangeable (`id` wins). The id is slugified into
the instance filename (`[^A-Za-z0-9_-]` collapses to `-`).

### Schema aliases (legacy compatibility)

Two keys accept a legacy form. The canonical form is preferred; the alias still
works but emits a deprecation note surfaced in the tool's `warnings` field.

| Canonical | Legacy alias | Behavior on conflict |
|---|---|---|
| `target_folder: 15_Tasks/pbs` | `instance_folder: 15_Tasks/pbs` | If both are set, `target_folder` wins + a warning is emitted. |
| `frontmatter_to_inherit:` as a `{key: value}` map | `frontmatter_to_inherit:` as a list of key names (values read from the template's top-level frontmatter) | Dict form is canonical; list form emits a deprecation warning. |

The dict form is DRY: inherited values live once in the map rather than being
duplicated as top-level template frontmatter. If `frontmatter_to_inherit` is
configured but resolves to no fields (e.g. every listed key in the legacy form is
a typo, or the dict is empty), the tool emits a warning rather than silently
inheriting nothing. A non-dict/non-list value is ignored with a warning.

## Anchors and intervals

### Absolute anchors

Absolute mode uses a calendar anchor expression. Each anchor yields a
`trigger_date` (the date the instance becomes due, before `due_offset_days`) and
a deterministic `period_key` used for idempotency and the filename.

| Anchor | Trigger date | Period key |
|---|---|---|
| `month_end` | last calendar day of the month | `YYYY-MM` |
| `month_start` | first calendar day of the month | `YYYY-MM` |
| `quarter_end_plus_Nd` | last day of the calendar quarter, plus `N` days | `qN-YYYY` |
| `fixed-MM-DD` | that month/day each year | `fixed-MM-DD-YYYY` |
| `T-N-before-MM-DD` | `N` days before that month/day each year | `fixed-MM-DD-YYYY` (the anchor date, not the trigger, is the key) |

Examples:

| Expression | On `as_of` 2026-07-15 the most recent trigger is | Period key |
|---|---|---|
| `month_end` | 2026-06-30 | `2026-06` |
| `month_start` | 2026-07-01 | `2026-07` |
| `quarter_end_plus_3d` | 2026-07-03 (Q2 ends 2026-06-30, +3d) | `q2-2026` |
| `fixed-10-28` | 2025-10-28 | `fixed-10-28-2025` |
| `T-5-before-12-31` | 2025-12-26 | `fixed-12-31-2025` |

Quarter boundaries: Q1 = Jan–Mar, Q2 = Apr–Jun, Q3 = Jul–Sep, Q4 = Oct–Dec.
`fixed-02-29` / `T-N-before-02-29` anchors silently skip non-leap years (the
date does not exist, so no trigger fires that year). Month/day values are
validated; an out-of-range `MM`/`DD` raises `AnchorError` and surfaces under
`errors`. The descending anchor walk is capped at 4000 internal steps so a
misconfiguration cannot hang the process.

### Relative intervals

`recurrence_interval` accepts `Nd` (days) or `Nm` (months), `N >= 1`:

- `7d` → 7 days after the last-done date.
- `2m` → 2 months after, **calendar-aware with month-end clamping**: Jan 31 + 1m
  → Feb 28 (or Feb 29 in a leap year), because February has no 31st. The
  day-of-month is clamped to the target month's length.

The `period_key` for relative mode is the ISO trigger date (e.g. `2026-07-22`).

The last-done date is resolved in this order:

1. The most recent existing instance for this template whose `status` equals
   `VAULT_RECURRING_DONE_STATUS` (from the on-disk scan). The instance's date is
   read from the first present of: `closed`, `done_at`, `completed`, `due`,
   `updated`, `created`.
2. The template's `last_run`.
3. If neither exists → bootstrap (see below).

## Bootstrap semantics

Behavior on the very first run of a freshly installed template (no `last_run`, no
done instances) differs by mode — deliberately, because the two modes encode
different intents.

### Absolute mode: `created` is the implicit baseline

Without `last_run`, the tool uses the template's `created` frontmatter date as an
implicit baseline, minus one day (so a trigger landing exactly on `created`
qualifies). Then:

- Trigger **before** the baseline → not materialized (no retroactive backfill).
- Trigger **between baseline and `as_of`** (inclusive) → fires. This is the
  bootstrap moment: the first matching anchor produces the first instance and
  `last_run` is set accordingly.
- Trigger **after `as_of`** → `skipped: not_due` (future).

If the template has **neither `last_run` nor `created`**, `since` stays `None`,
`compute_pending_periods` returns nothing, and the template is reported
`skipped: not_due` — it never fires on its own. For UI-created notes that omit
`created`, set one by hand once.

Why: a freshly installed `quarter_end_plus_1d` anchored at `created: 2026-07-01`
should fire on 2026-07-01 without operator intervention rather than silently
skipping a whole quarter.

Operator note: `last_run` is written **only by the tool**, on real (non-dry-run)
firings. To shift the bootstrap baseline, hand-edit `created`, not `last_run`.

### Relative mode: bootstrap with today

Without any baseline (no done instance, no `last_run`), a relative template
**fires once immediately** with `trigger_date = today` and `period_key =
today.isoformat()`. The cadence starts from that first instance: the next trigger
is `today + recurrence_interval`. A relative template is a self-driven cadence
("every 7 days starting now"); skipping the bootstrap would mean it never fires
without a manually seeded baseline.

## Catch-up behavior (absolute mode)

`VAULT_RECURRING_CATCHUP_MODE` controls what happens when several absolute periods
are pending between the baseline and `as_of`:

- `next` (default) → materialize only the **most recent** pending period. Long
  downtime followed by a single run produces one instance per template, not a
  flood.
- `all` → materialize **one instance per missed period**, for a full audit trail.

On a fresh install (no `last_run`, baseline derived from `created`) the tool
creates at most one instance regardless of mode. Catch-up only applies once a
real `last_run` exists. Relative mode always produces a single candidate per run.

## What ends up in an instance

For `id: q-governance`, period `q2-2026`, `as_of` 2026-07-04, `due_offset_days:
0`, the instance is named `recurring-q-governance-q2-2026.md` and contains:

```yaml
---
id: recurring-q-governance-q2-2026
title: "Q-Governance q2-2026"      # only when instance_title is set
recurrence_template: q-governance
recurrence_period: q2-2026
source: recurring-q-governance-q2-2026
created: 2026-07-04                 # the run date (today / as_of)
due: 2026-07-03                     # trigger_date + due_offset_days
priority: 2                         # from priority_initial (omitted if unset)
scope: pbs                          # inherited via frontmatter_to_inherit
project: governance
tags: [recurring-instance, quarterly, reporting]
---

Quarterly governance review for q2-2026. Due 2026-07-03.
```

Fixed fields the tool always writes: `id`, `recurrence_template`,
`recurrence_period`, `source`, `created`, `due`, and a `tags` list that always
begins with `recurring-instance`. `priority` is added only when `priority_initial`
is set. `title` and the body appear only when `instance_title` / `body_template`
are set. `instance_title` interpolates `{template_id}`, `{period}`, `{trigger}`;
`body_template` additionally exposes `{due}`. Interpolation uses Python
`str.format`, so literal braces in a template body must be doubled (`{{`, `}}`).

The instance directory is resolved as: `target_folder` (canonical) →
`instance_folder` (legacy alias) → the template's own parent directory (sibling
fallback when neither is set).

Writes go through the host's `write_file_atomic` (with `create_dirs=True`) plus a
read-back verification; a mismatch raises and is reported under `errors` for that
period. After a successful non-dry-run firing the template's `last_run` is updated
to the last processed trigger date.

## The tool: `recurring_materialize`

```python
recurring_materialize(dry_run=False, template_id=None, as_of=None)
```

- `dry_run=True` — compute what would be created, write nothing, do not touch
  `last_run`. Each would-be instance appears under `created` with `"dry_run":
  true`.
- `template_id` — restrict to a single template id. If it matches no active
  template, the response lists it under `errors` ("template id not found").
- `as_of` — ISO date (`YYYY-MM-DD`) overriding "today" for anchor resolution
  (backfills, tests). An invalid value returns `error_code: invalid_as_of`.

Returns a JSON string:

```json
{
  "checked": 1,
  "created": [
    {"template_id": "q-governance", "period": "q2-2026",
     "path": "15_Tasks/pbs/recurring-q-governance-q2-2026.md",
     "trigger_date": "2026-07-03", "size": 412}
  ],
  "skipped": [
    {"template_id": "weekly-review", "period": "2026-07-22",
     "reason": "already_exists",
     "existing_path": "15_Tasks/pbs/recurring-weekly-review-2026-07-22.md"}
  ],
  "errors": [],
  "warnings": [],
  "dry_run": false,
  "as_of": "2026-07-15",
  "catchup_mode": "next"
}
```

`skipped` reasons you may see: `not_recurring_template_or_inactive`, `not_due`,
`not_yet_due` (relative, with `next_trigger`), `already_exists`,
`no_pending_periods`. Capability errors (disabled / folder unset / invalid
`as_of`) return a flat `{"error", "error_code"}` payload instead of the
aggregate shape.

## CLI

A standalone CLI entry point `cli_main` lives in `recurring/cli.py` for
timer-driven setups (systemd timer, cron, Windows Task Scheduler). **No
console-script is wired** — that is the operator's choice. Unlike the fork's CLI
there is **no `--no-index` flag and no frontmatter-index bootstrap**: idempotency
and relative last-done lookups are entirely disk-based.

```
vault-recurring run [--dry-run] [--template-id ID] [--as-of YYYY-MM-DD]
```

It runs materialization once against the configured vault, writes the JSON result
to stdout, logs to stderr, and returns exit code 0. To expose it as a command,
add a console-script in your own entry point pointing at
`obsidian_vault_mcp_ext.recurring.cli:cli_main`, or call it from Python:

```python
from obsidian_vault_mcp_ext.recurring.cli import cli_main
raise SystemExit(cli_main(["run"]))
```

Example systemd timer service fragment:

```ini
[Service]
Type=oneshot
EnvironmentFile=/etc/obsidian-mcp-ext.env   # exports VAULT_PATH, VAULT_RECURRING_*
ExecStart=/opt/venv/bin/python -m obsidian_vault_mcp_ext.recurring.cli run
```

## Worked examples

### 1. Quarterly governance task (absolute anchor, catch-up)

Template `90_Recurring/q-governance.md`:

```yaml
---
id: q-governance
type: recurring-template
active: true
recurrence_anchor_mode: absolute
recurrence_anchor: quarter_end_plus_3d
created: 2026-07-01
due_offset_days: 0
priority_initial: 2
target_folder: 15_Tasks/pbs
instance_title: "Q-Governance {period}"
frontmatter_to_inherit:
  scope: pbs
  project: governance
tags_to_inherit: [quarterly, reporting]
body_template: |
  Quarterly governance review for {period}. Due {due}.
---
```

Env: `VAULT_RECURRING_TEMPLATES_FOLDER=90_Recurring`,
`VAULT_RECURRING_CATCHUP_MODE=next`.

First run on 2026-07-15 (`recurring_materialize()`):

- Baseline = `created` − 1 day = 2026-06-30. Q2 trigger is 2026-07-03 (Q2 ends
  06-30, +3d). 06-30 < 07-03 <= 07-15 → fires.
- Creates `15_Tasks/pbs/recurring-q-governance-q2-2026.md`, sets `last_run =
  2026-07-03`. `created: 1, checked: 1`.

Second run the same day → period already on disk → `skipped: already_exists`,
`created: 0` (idempotent).

Run on 2026-10-05 after a quiet Q3: Q3 trigger is 2026-10-03. With
`catchup_mode=next` exactly the Q3 instance is created. With `catchup_mode=all`,
any missed quarter between `last_run` and `as_of` would each get an instance.

### 2. Relative weekly task (interval, bootstrap then cadence)

Template `90_Recurring/weekly-review.md`:

```yaml
---
id: weekly-review
type: recurring-template
active: true
recurrence_anchor_mode: relative
recurrence_interval: 7d
priority_initial: 3
target_folder: 15_Tasks/ceo
instance_title: "Weekly Review {period}"
frontmatter_to_inherit:
  scope: ceo
tags_to_inherit: [weekly]
---
```

Env adds `VAULT_RECURRING_DONE_STATUS=done`.

First run on 2026-07-15: no done instance, no `last_run` → bootstrap fires once
with `trigger_date = 2026-07-15`, period `2026-07-15`. Creates
`recurring-weekly-review-2026-07-15.md`, `last_run = 2026-07-15`.

Run on 2026-07-20 (no instance marked done yet): last-done falls back to
`last_run` 2026-07-15; next trigger 2026-07-22 > 2026-07-20 →
`skipped: not_yet_due` with `next_trigger: 2026-07-22`.

You complete the 2026-07-15 instance by setting `status: done` in it. Run on
2026-07-23: last-done is now the done instance's date, next trigger fires, a new
`recurring-weekly-review-2026-07-22.md` is created — the cadence advances from
actual completion, not from the calendar.

## Safety and scope

- All writes use the host's atomic write + read-back verification path, the same
  as `vault_write`.
- Folder/path resolution fails closed via `resolve_vault_path`; excluded
  directories and symlinks are skipped.
- Anchor parsing is bounded (4000-step cap) and malformed expressions are
  reported as `errors`, never raised to the client.
- **Out of scope** (by design): the in-process scheduler, post-write hooks, the
  frontmatter-index idempotency path, "snooze / skip this period / rematerialize"
  semantics, timezone handling (server-local date), and multi-vault support.
