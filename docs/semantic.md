# Semantic Search

Back to [README](../README.md).

`SemanticExtension` adds hybrid semantic + keyword search over the vault's
markdown notes through the host's extension seam. It exposes two MCP tools:

| Tool | Annotation | Purpose |
|---|---|---|
| `vault_semantic_search` | read-only | Hybrid semantic + BM25 keyword retrieval for a natural-language query. |
| `vault_reindex` | write | Rebuild the search cache (full or incremental) from current vault contents. |

The heavy machinery (FAISS vector index, embeddings, BM25) lives behind an
optional dependency extra and is **lazy-imported**, so the package loads and the
tools register even when the extra is not installed — they just fail soft.

## The `[semantic]` extra

The embedding stack is **not** a hard dependency. Install it explicitly:

```bash
pip install "obsidian-vault-mcp-ext[semantic]"
```

The extra pulls in `faiss-cpu`, `numpy` (pinned `<2.0`), `fastembed` and
`rank-bm25`. None of these are imported at module load — `engine.py` imports them
inside methods only when a search or reindex actually runs. Importing the
extension without the extra is safe (a test asserts that `faiss`, `fastembed`,
`sentence_transformers` and `numpy` stay out of `sys.modules` after import).

Fail-soft behavior without the extra (or before an index is built): both tools
return `{"error": "..."}` rather than crashing. The full reindex + search round
trip is verified by the test suite on Python 3.12 with the extra installed
(`tests/test_semantic_extension.py::test_full_reindex_and_search`, gated to run
only when the deps are present).

> The embedding-backend code in `engine.py` can also fall back to
> `sentence-transformers` and references a `[semantic-sentence]` extra in its
> error messages, but **that extra is not declared in `pyproject.toml`**. The
> shipped, tested path is `fastembed` via `[semantic]`. Treat the
> sentence-transformers backend as undocumented/unsupported unless you add the
> dependency yourself.

## Enabling search

Semantic search is **off by default**. Two things must be true to get results:

1. `VAULT_SEMANTIC_SEARCH_ENABLED=1` (otherwise the tool returns
   "Semantic search is disabled").
2. A built cache on disk — or `VAULT_SEMANTIC_BUILD_ON_DEMAND=1` so the engine
   builds one on first use. Without either, search returns
   "Semantic cache is not initialized. Run vault_reindex(full=true) ...".

Typical first-time setup:

```bash
export VAULT_SEMANTIC_SEARCH_ENABLED=1
# build the index once
python -c "from obsidian_vault_mcp_ext.semantic import tools; ..."   # or call vault_reindex(full=True) via MCP
```

## Environment variables

All read at import time from the `VAULT_SEMANTIC_*` namespace.

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `VAULT_SEMANTIC_SEARCH_ENABLED` | bool | `false` | Master switch. Tools fail soft until set. |
| `VAULT_SEMANTIC_EMBED_BACKEND` | `auto` \| `fastembed` \| `sentence` | `fastembed` | Embedding backend selection. `fastembed` is the supported/tested path. |
| `VAULT_SEMANTIC_EMBED_MODEL` | string | `BAAI/bge-small-en-v1.5` | Embedding model name passed to the backend. |
| `VAULT_SEMANTIC_BUILD_ON_DEMAND` | bool | `false` | If true, build a full index on first use when no cache exists. If false, a missing cache stays unavailable until `vault_reindex` runs. |
| `VAULT_SEMANTIC_CHUNK_SIZE` | int | `900` | Target characters per chunk. |
| `VAULT_SEMANTIC_CHUNK_OVERLAP` | int | `150` | Character overlap between adjacent chunks. |
| `VAULT_SEMANTIC_EMBED_BATCH_SIZE` | int | `64` | Chunks embedded per batch during index build. |
| `VAULT_SEMANTIC_MAX_RESULTS` | int | `20` | Hard cap on results; a tool call's `max_results` is clamped to this. |
| `VAULT_SEMANTIC_UPDATE_DEBOUNCE_SECONDS` | int | `4` | Debounce window for incremental updates fed by the host change listener. |
| `VAULT_SEMANTIC_CACHE_PATH` | string | _(derived)_ | Explicit cache directory. When empty, the cache lives at `<VAULT_PATH>/.obsidian-vault-mcp`. |

Boolean parsing accepts `1/true/yes/on`. `VAULT_PATH` and the cache path are
resolved dynamically from the host config (not snapshotted), so a monkeypatched
vault in tests gets an isolated cache under the temp vault.

## Cache location and contents

By default the cache is `<VAULT_PATH>/.obsidian-vault-mcp/`, overridable with
`VAULT_SEMANTIC_CACHE_PATH`. It holds four files:

- `faiss.index` — the FAISS inner-product vector index.
- `chunks.json` — chunk records (path, title, section, tags, text, tokens, hash).
- `manifest.json` — `rel_path -> SHA-256` of source content, for change detection.
- `path_index.json` — `rel_path -> [chunk ids]`, so a file's chunks can be
  removed/replaced incrementally.

All four must be present for the engine to load an existing cache; otherwise it
either builds on demand (if enabled) or reports the cache uninitialized. A corrupt
or unreadable cache triggers an automatic full rebuild.

## Indexing model

`vault_reindex(full=True)` walks every `*.md` under `VAULT_PATH`, skipping
non-indexable paths (excluded dirs `.obsidian`, `.trash`, `.git`, `.DS_Store`,
`.obsidian-vault-mcp`, and anything that does not resolve safely inside the vault
via the host's `resolve_vault_path`). Each file is split: frontmatter is parsed
off, the body is divided on top-level `#` headings, and long sections are split
into overlapping character windows (`CHUNK_SIZE` / `CHUNK_OVERLAP`). Each chunk
carries the note title, section heading, tags and a tokenized form for BM25.

`vault_reindex(full=False)` does an incremental refresh: it loads the existing
cache, detects changed/new/deleted files by comparing content hashes against the
manifest (or uses an explicit path list), removes stale chunks, re-chunks updated
files, then rebuilds the BM25 and FAISS structures and persists. If no cache
exists yet, an incremental call is promoted to a full rebuild.

### Incremental reindex via the host index listener

If the host frontmatter index exposes `add_change_listener`, the extension
attaches one in `after_indexes_start`. The host calls back with
`(abs_path, exists)`; the extension maps that to a vault-relative path and a
`modify`/`delete` action and queues it on the engine. Queued changes are
**debounced** by `VAULT_SEMANTIC_UPDATE_DEBOUNCE_SECONDS`; when the timer fires
the engine runs an incremental reindex over just the queued paths. The listener
is best-effort: it never loads heavy deps on its own (the engine stays unbuilt
until a real search/reindex), and any listener exception is swallowed so it can
never crash the host's index loop.

## Searching

```python
vault_semantic_search(
    query,
    path_prefix=None,      # restrict to chunks whose path starts with this prefix
    filter_tags=None,      # require all listed tags (case-insensitive) on the chunk
    search_mode="hybrid",  # "hybrid" | "semantic" | "keyword"
    max_results=10,        # clamped to VAULT_SEMANTIC_MAX_RESULTS
    min_score=0.0,         # drop results below this combined score
)
```

Scoring:

- **semantic** — cosine/inner-product similarity from the FAISS index over
  normalized embeddings.
- **keyword** — BM25 over whitespace-tokenized query terms, normalized to the
  top score in the result set.
- **hybrid** (default) — `0.75 * semantic + 0.25 * keyword`.

Each result returns `path`, `title`, `section`, `tags`, the combined `score` plus
the component `semantic_score` / `keyword_score`, and a 280-char `excerpt`. The
payload also includes `candidate_counts` (semantic/keyword/merged) and a
`truncated` flag.

Result shape:

```json
{
  "mode": "hybrid",
  "results": [
    {"path": "...", "title": "...", "section": "...", "tags": ["..."],
     "score": 0.8123, "semantic_score": 0.91, "keyword_score": 0.42,
     "excerpt": "..."}
  ],
  "total": 5,
  "truncated": true,
  "candidate_counts": {"semantic": 40, "keyword": 12, "merged": 47}
}
```

Every error path is soft: a missing engine, missing deps, disabled search, or an
unbuilt index all return `{"error": "..."}` rather than raising.
