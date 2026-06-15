# Templates and Dataview

Back to [README](../README.md).

`TemplatesExtension` adds lightweight template rendering plus Dataview DQL query
support through the host's extension seam. It exposes four MCP tools:

| Tool | Annotation | Purpose |
|---|---|---|
| `vault_template_list` | read-only | List markdown templates under `VAULT_TEMPLATER_FOLDER`. |
| `vault_template_render` | read-only | Render a template with `{{token}}` substitution; returns content without writing. |
| `vault_template_apply` | write | Render a template and write it to a target path (atomic). |
| `vault_dataview_query` | read-only | Run a Dataview `TABLE` DQL query via the Obsidian Local REST API. |

No optional extra is required; the REST bridge is stdlib-only (`urllib`).

## Rendering: `{{token}}` substitution, not Templater

This is **not** a Templater engine. It performs simple `{{token}}` substitution
only. The token grammar is `{{ name }}` where `name` matches
`[A-Za-z_][A-Za-z0-9_.-]*`. Only `engine="simple"` is supported.

**Templater `<% %>` syntax is rejected.** If the template contains any of the
markers `<%`, `<%-`, `<%*`, `<%~`, `<%+`, rendering fails with error code
`template_render_unavailable` ("Templater syntax detected; this server supports
{{ }} substitution only.") rather than silently passing the script through.

### Tokens

Built-in tokens, always available:

| Token | Value |
|---|---|
| `{{date}}` | Today's local date, ISO (`YYYY-MM-DD`). |
| `{{datetime}}` | Current UTC datetime, ISO 8601. |
| `{{title}}` | `variables.title` if provided, else the target filename stem, else the template filename stem. |
| `{{target_path}}` | The `target_path_hint` (or `target_path` for apply), or empty. |

User variables come from the `variables` argument and can be referenced two ways:

- `{{variables.foo}}` — explicit namespace; the name after `variables.` must exist
  in `variables` or rendering fails with `template_render_failed` ("Missing
  template variable: foo").
- `{{foo}}` — bare name; resolved against built-ins first, then `variables`. An
  unknown bare token also fails with `template_render_failed`.

A `None` variable value renders as an empty string. There is no partial-render
mode: any unresolved token is an error.

## Listing and rendering

```python
vault_template_list(folder=None, recursive=True)
```

Lists `*.md` files under `folder` (default `VAULT_TEMPLATER_FOLDER`). Returns each
template's vault-relative `path`, `name` (stem), `relative_to_template_folder`,
and `size`. Returns `template_folder_missing` when the folder is unset or absent.

```python
vault_template_render(template_path, target_path_hint=None, variables=None, engine="simple")
```

`template_path` is resolved leniently: it is tried as given, prefixed with
`VAULT_TEMPLATER_FOLDER`, and with a `.md` suffix added — so `"daily"`,
`"daily.md"`, and `"Templates/daily.md"` all resolve when the folder is set.
Returns the rendered `content`, the resolved `template_path`, the
`target_path_hint`, the `engine`, and the byte `size`. `target_path_hint` does not
need to exist; it only feeds `{{title}}` / `{{target_path}}`.

```python
vault_template_apply(template_path, target_path, variables=None, overwrite=False, engine="simple")
```

Renders, then writes to `target_path` through the host's `write_file_atomic`
(`create_dirs=True`). Refuses to clobber an existing file unless `overwrite=True`
(`target_exists` otherwise). Returns `path`, `created` (bool), `size`, the
resolved `template_path`, the engine, and `rendered_size`. Path escapes are
rejected with `path_not_allowed` (via the host's `resolve_vault_path`).

### Example

Template `Templates/daily.md`:

```markdown
---
title: {{title}}
created: {{datetime}}
---

# {{date}} - {{variables.focus}}
```

```python
vault_template_apply(
    "daily",
    target_path="40_Reflexion/HEUTE/2026-07-15.md",
    variables={"focus": "Quarter close"},
)
```

writes a file titled from the target stem (`2026-07-15`), with the current UTC
`created`, and a heading `# 2026-07-15 - Quarter close`.

## Dataview queries

```python
vault_dataview_query(query, query_type="dql", timeout_seconds=None)
```

Runs a Dataview DQL query (only `query_type="dql"` is supported) by POSTing it to
the Obsidian Local REST API `/search/` endpoint with content type
`application/vnd.olrapi.dataview.dql+txt`. The JSON response is flattened into a
table: a `filename` column plus one column per `result` key, in first-seen order.
Returns `{"type": "table", "columns": [...], "rows": [...], "duration_ms": ...}`.

The default timeout is `VAULT_DATAVIEW_TIMEOUT` (overridable per call via
`timeout_seconds`).

### Obsidian Local REST API bridge and fail-soft

The Dataview tool is a bridge to the [Obsidian Local REST
API](https://github.com/coddingtonbear/obsidian-local-rest-api) plugin. It is
**fail-soft**: when `VAULT_OBSIDIAN_REST_URL` is unset, the request raises
`capability_unavailable` and the tool returns that as a clean error — the
template tools keep working from vault files regardless.

Error mapping the tool surfaces:

| Condition | `error_code` |
|---|---|
| `VAULT_OBSIDIAN_REST_URL` unset | `capability_unavailable` |
| API key rejected (HTTP 401) | `rest_auth_failed` |
| Endpoint/command not found (404) | `command_unknown` |
| Bad request (400) / malformed DQL | `dataview_query_failed` |
| `TABLE WITHOUT ID` used | `dataview_query_failed` (not supported by Local REST API) |
| Dataview plugin missing/undefined | `dataview_unavailable` |
| Timeout | `rest_timeout` |
| Host unreachable | `plugin_unavailable` |
| TLS / other plugin error | `plugin_misconfigured` |

## Environment variables

Read at import time from this extension's namespace.

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `VAULT_TEMPLATER_FOLDER` | string | _(empty)_ | Vault-relative folder holding templates. Template tools return `template_folder_missing` when unset. |
| `VAULT_DATAVIEW_TIMEOUT` | int (seconds) | `15` | Default timeout for `vault_dataview_query`. |
| `VAULT_OBSIDIAN_REST_URL` | string | _(empty)_ | Base URL of the Obsidian Local REST API. When unset, Dataview fails soft with `capability_unavailable`. |
| `VAULT_OBSIDIAN_REST_API_KEY` | string | _(empty)_ | Bearer token sent as `Authorization` when set. |
| `VAULT_OBSIDIAN_REST_VERIFY_TLS` | bool | `false` | Verify TLS certificates. Default false suits the plugin's self-signed local HTTPS. |
| `VAULT_OBSIDIAN_REST_TIMEOUT` | int (seconds) | `15` | Default timeout for REST requests generally. |

Boolean parsing accepts `1/true/yes/on`. `VAULT_PATH` is taken dynamically from the
host config (not snapshotted), so monkeypatched test vaults take effect.
