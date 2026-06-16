# MaintenanceExtension

Vault housekeeping, added through the host's `serve(extensions=...)` seam (#57); no host fork,
no dependencies (stdlib only).

Three tools:

- `vault_scan_encoding(path_prefix="", max_results=100)` — scan markdown files under the vault
  (or under `path_prefix`) and report those that are **not valid UTF-8**, with the byte
  position and reason. Read-only.
- `vault_repair_encoding(path_prefix="", max_files=50, source_encoding="cp1252", dry_run=False)`
  — repair non-UTF-8 markdown by re-decoding the bytes from `source_encoding` and rewriting
  them as UTF-8 via the host's atomic write. `dry_run=True` previews without writing.
- `vault_delete_directory(path, only_if_empty=True)` — soft-delete a directory by moving it
  into the vault's trash folder (timestamp suffix on name collision). Refuses a non-empty
  directory unless `only_if_empty=False`. Reversible (move, not destroy).

## Scope and safety

- Uses only upstream-public host APIs: `resolve_vault_path` (every target is vault-confined),
  `write_file_atomic` (the repair rewrite is atomic), and the host config's `VAULT_PATH`.
- The scan/repair walk skips symlinks and any path component starting with `.` (so `.git`,
  `.obsidian`, the trash folder, and other hidden dirs are never touched).
- Encoding repair changes file *bytes*; run `vault_scan_encoding` first, then
  `vault_repair_encoding` with `dry_run=True`, and only then a real repair. `cp1252` is the
  common legacy source for Western-European text mis-saved as non-UTF-8; set
  `source_encoding` to match your origin.
- Directory delete is a soft-delete to the trash folder, not `rm`. Recover by moving the
  folder back out of `<VAULT_PATH>/.trash/`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_MAINTENANCE_SCAN_MAX_RESULTS` | `100` | Default cap on issues returned by `vault_scan_encoding`. |
| `VAULT_MAINTENANCE_REPAIR_MAX_FILES` | `50` | Default cap on files touched by one `vault_repair_encoding` call. |
| `VAULT_MAINTENANCE_REPAIR_SOURCE_ENCODING` | `cp1252` | Default source encoding assumed when repairing. |
| `VAULT_MAINTENANCE_TRASH_DIR` | `.trash` | Vault-root folder soft-deleted directories are moved into. |

`VAULT_PATH` comes from the host server config.
