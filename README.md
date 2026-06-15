# obsidian-vault-mcp-ext

Personal and optional-dependency extensions for
[`jimprosser/obsidian-web-mcp`](https://github.com/jimprosser/obsidian-web-mcp), loaded
through its `serve(extensions=...)` seam (#57) **without forking the host**.

Each feature is its own `Extension` subclass so an operator loads only what they want and
installs only the dependencies that feature needs (heavy embedding deps are an optional
extra, lazy-imported). Compose them at your own entry point:

```python
from obsidian_vault_mcp.server import serve
from obsidian_vault_mcp_ext import TemplatesExtension

serve([TemplatesExtension()])
```

## Installation

The host server ([`jimprosser/obsidian-web-mcp`](https://github.com/jimprosser/obsidian-web-mcp))
provides the `serve(extensions=...)` seam this package plugs into. Neither package is on PyPI
yet, so install both from git — **the host first** (its dependency must already be present when
pip resolves this package):

```bash
# 1. Host server (must include the extension seam, i.e. recent main):
pip install "obsidian-web-mcp @ git+https://github.com/jimprosser/obsidian-web-mcp@main"

# 2. This package — base (Templates + Recurring + Import, no heavy deps):
pip install "obsidian-vault-mcp-ext @ git+https://github.com/mleitnercom/obsidian-vault-mcp-ext@main"

# ...or with semantic search (also pulls faiss-cpu / fastembed / numpy<2 / rank-bm25):
pip install "obsidian-vault-mcp-ext[semantic] @ git+https://github.com/mleitnercom/obsidian-vault-mcp-ext@main"
```

Python 3.12+. The `[semantic]` extra needs a Python with faiss/numpy<2 wheels (3.12 or 3.13).
Verified on Python 3.12 (host `0.2.0`).

## Running

First configure the **host** per its README (at least `VAULT_PATH` and `VAULT_MCP_TOKEN`, plus
its OAuth settings for remote use), then set the extension knobs you need
(see [Configuration](#configuration)). Then start the server with the extensions loaded.

Bundled entry point — serves all three (semantic fails soft without its extra):

```bash
vault-mcp-ext
```

To load only a subset, use your own one-line entry point:

```python
# run_vault.py
from obsidian_vault_mcp.server import serve
from obsidian_vault_mcp_ext import TemplatesExtension, RecurringExtension

serve([TemplatesExtension(), RecurringExtension()])   # e.g. without semantic
```
```bash
python run_vault.py
```

## Extensions

All four are shipped and tested (the semantic full reindex + search round trip is
verified on Python 3.12 with the `[semantic]` extra installed).

- **TemplatesExtension** — `{{token}}` rendering (not full Templater; `<% %>` is rejected)
  and Dataview `TABLE` DQL via the Obsidian Local REST API. Tools: `vault_template_list`,
  `vault_template_render`, `vault_template_apply`, `vault_dataview_query`. Fail-soft: the
  Dataview tool returns a capability error when `VAULT_OBSIDIAN_REST_URL` is unset; the
  template tools work from vault files regardless. No optional extra. See
  [docs/templates.md](docs/templates.md).

- **SemanticExtension** — hybrid embedding + BM25 search (`vault_semantic_search`,
  `vault_reindex`) with a persistent FAISS cache. Heavy deps (`faiss-cpu`, `fastembed`,
  `numpy`, `rank-bm25`) are an optional extra and lazy-imported, so the package loads
  without them and the search tools fail soft until
  `pip install obsidian-vault-mcp-ext[semantic]`. Reindexes incrementally via the host's
  index change listener. See [docs/semantic.md](docs/semantic.md).

- **RecurringExtension** — materializes `recurring-template` notes into concrete task
  instances (`recurring_materialize`); strictly idempotent via an on-disk scan of the
  instance folder. The deepest of the three (the Task-OS engine: calendar anchors, relative
  intervals, bootstrap and catch-up semantics). No extra dependencies. See
  [docs/recurring.md](docs/recurring.md).

- **ImportExtension** — bring binary files into the vault by URL (`vault_import_url`) or from a
  local allowlisted path (`vault_import_file`). URL import is **SSRF-hardened**: it resolves the
  host once and pins the connection to the validated IP (no DNS-rebinding window), re-validates
  every redirect hop, denies non-public targets by default, restricts ports, and caps size.
  Secure-by-default: URL import is off until `VAULT_IMPORT_URL_ENABLED`, file import off until
  `VAULT_IMPORT_FILE_ALLOWED_ROOTS`. No extra dependencies (stdlib only). See
  [docs/import.md](docs/import.md).

Planned: `AuditExtension` (once a write-listener seam lands upstream, jimprosser#58).

## Configuration

Every knob is an environment variable, namespaced per extension and read at import time.
Booleans accept `1/true/yes/on`. `VAULT_PATH` comes from the host server config.

### Templates

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_TEMPLATER_FOLDER` | _(empty)_ | Vault-relative folder holding templates. |
| `VAULT_DATAVIEW_TIMEOUT` | `15` | Default timeout (s) for `vault_dataview_query`. |
| `VAULT_OBSIDIAN_REST_URL` | _(empty)_ | Obsidian Local REST API base URL; Dataview fails soft when unset. |
| `VAULT_OBSIDIAN_REST_API_KEY` | _(empty)_ | Bearer token for the REST API. |
| `VAULT_OBSIDIAN_REST_VERIFY_TLS` | `false` | Verify TLS certs (default suits self-signed local HTTPS). |
| `VAULT_OBSIDIAN_REST_TIMEOUT` | `15` | Default timeout (s) for REST requests. |

### Semantic

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_SEMANTIC_SEARCH_ENABLED` | `false` | Master switch; tools fail soft until set. |
| `VAULT_SEMANTIC_EMBED_BACKEND` | `fastembed` | `auto` \| `fastembed` \| `sentence` (`fastembed` is the supported path). |
| `VAULT_SEMANTIC_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model name. |
| `VAULT_SEMANTIC_BUILD_ON_DEMAND` | `false` | Build a full index on first use when no cache exists. |
| `VAULT_SEMANTIC_CHUNK_SIZE` | `900` | Target characters per chunk. |
| `VAULT_SEMANTIC_CHUNK_OVERLAP` | `150` | Character overlap between chunks. |
| `VAULT_SEMANTIC_EMBED_BATCH_SIZE` | `64` | Chunks embedded per batch. |
| `VAULT_SEMANTIC_MAX_RESULTS` | `20` | Hard cap on results (clamps a call's `max_results`). |
| `VAULT_SEMANTIC_UPDATE_DEBOUNCE_SECONDS` | `4` | Debounce for change-listener incremental updates. |
| `VAULT_SEMANTIC_CACHE_PATH` | `<VAULT_PATH>/.obsidian-vault-mcp` | Explicit cache directory override. |

### Recurring

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_RECURRING_ENABLED` | `true` | Master switch; tool returns `recurring_disabled` when false. |
| `VAULT_RECURRING_TEMPLATES_FOLDER` | _(empty)_ | Vault-relative folder of template notes. Required. |
| `VAULT_RECURRING_DONE_STATUS` | `done` | `status` value marking an instance completed (relative mode). |
| `VAULT_RECURRING_CATCHUP_MODE` | `next` | `next` (most recent pending period) or `all` (one per missed period). |
| `VAULT_RECURRING_INTERVAL` | `0` | Parsed but unused in this port (no internal scheduler; drive via the CLI). |

### Import

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_IMPORT_URL_ENABLED` | `false` | Master switch for `vault_import_url`; fails soft until set. |
| `VAULT_IMPORT_URL_ALLOW_PRIVATE` | `false` | Allow non-public (private/loopback/link-local) URL targets. Opt-in only. |
| `VAULT_IMPORT_URL_TIMEOUT` | `30` | Per-connection timeout (s). |
| `VAULT_IMPORT_URL_MAX_REDIRECTS` | `5` | Max redirect hops; each hop is re-validated and re-pinned. |
| `VAULT_IMPORT_URL_ALLOWED_PORTS` | `80,443` | Comma-separated allowed ports for URL targets. |
| `VAULT_IMPORT_MAX_BYTES` | `10485760` | Hard size cap (bytes) for URL and file import. |
| `VAULT_IMPORT_ALLOWED_MEDIA_TYPES_JSON` | _(images + PDF)_ | JSON `{media_type: [".ext"]}` overriding the default allowlist. |
| `VAULT_IMPORT_FILE_ALLOWED_ROOTS` | _(empty)_ | OS-pathsep list of roots `vault_import_file` may read from. Empty disables it. |

## Development

```
pip install -e ../obsidian-web-mcp   # the host (provides the seam)
pip install -e .[dev] --no-deps
pytest
```
