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

- **SemanticExtension** — embedding + BM25 search (`vault_semantic_search`, `vault_reindex`)
  with a persistent FAISS cache. Heavy deps (`faiss-cpu`, `fastembed`, `numpy`, `rank-bm25`)
  are an optional extra and lazy-imported, so the package loads without them and the search
  tools fail soft until `pip install obsidian-vault-mcp-ext[semantic]`. Reindexes
  incrementally via the host's index change listener.
- **RecurringExtension** — materializes `recurring-template` notes into concrete task
  instances (`recurring_materialize`); strictly idempotent via an on-disk scan of the
  instance folder. No extra dependencies.

Planned: `AuditExtension` (once a write-listener seam lands upstream, jimprosser#58).

## Development

```
pip install -e ../obsidian-web-mcp   # the host (provides the seam)
pip install -e .[dev] --no-deps
pytest
```
