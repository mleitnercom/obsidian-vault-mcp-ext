"""Semantic search MCP tools (self-contained subpackage).

Ported from the fork's tools/semantic_search.py. Adaptations:

- ``from ..vault import vault_json_dumps`` -> the host's public serializer
  ``from obsidian_vault_mcp.serialization import dumps as vault_json_dumps``.
- The shared engine is injected by SemanticExtension via ``set_engine`` (same
  injection seam the fork used at server startup), so importing this module pulls
  in none of the heavy [semantic] deps.

All tools return ``dumps({...})``; the error shape is ``{"error": ...}`` and every
path fails soft (deps/index absent -> clean error, never a crash).
"""

import logging

from obsidian_vault_mcp.serialization import dumps as vault_json_dumps

logger = logging.getLogger(__name__)

_engine = None


def set_engine(engine) -> None:
    """Inject the shared semantic engine (called by the extension lifecycle)."""
    global _engine
    _engine = engine


def vault_semantic_search(
    query: str,
    path_prefix: str | None = None,
    filter_tags: list[str] | None = None,
    search_mode: str = "hybrid",
    max_results: int = 10,
    min_score: float = 0.0,
) -> str:
    """Run hybrid semantic + keyword search across vault markdown notes."""
    if _engine is None:
        return vault_json_dumps({"error": "Semantic search engine is unavailable"})
    try:
        return vault_json_dumps(
            _engine.search(
                query=query,
                path_prefix=path_prefix,
                filter_tags=filter_tags,
                search_mode=search_mode,
                max_results=max_results,
                min_score=min_score,
            )
        )
    except Exception as exc:  # fail soft; never surface a raw traceback to the client
        logger.warning("vault_semantic_search failed: %s", exc)
        return vault_json_dumps({"error": f"Semantic search failed: {exc}"})


def vault_reindex(full: bool = True) -> str:
    """Rebuild the semantic-search cache from the current vault contents."""
    if _engine is None:
        return vault_json_dumps({"error": "Semantic search engine is unavailable"})
    try:
        return vault_json_dumps(_engine.reindex(full=full))
    except Exception as exc:  # fail soft (e.g. deps not installed)
        logger.warning("vault_reindex failed: %s", exc)
        return vault_json_dumps({"error": f"Semantic reindex failed: {exc}"})
