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

## Extensions

- **TemplatesExtension** — Templater-style `{{token}}` rendering (not full Templater) and
  Dataview `TABLE` DQL via the Obsidian Local REST API. Fail-soft: the Dataview tool
  returns a capability error when `VAULT_OBSIDIAN_REST_URL` is unset; the template tools
  work from vault files regardless. Env: `VAULT_TEMPLATER_FOLDER`, `VAULT_OBSIDIAN_REST_URL`,
  `VAULT_OBSIDIAN_REST_API_KEY`, `VAULT_DATAVIEW_TIMEOUT`.

Planned: `RecurringExtension` (task materialization), `SemanticExtension` (`[semantic]`
extra: faiss/fastembed, lazy-imported), `AuditExtension` (once the write-listener seam lands).

## Development

```
pip install -e ../obsidian-web-mcp   # the host (provides the seam)
pip install -e .[dev] --no-deps
pytest
```
