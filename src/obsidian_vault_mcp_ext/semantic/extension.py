"""SemanticExtension: FAISS + BM25 hybrid semantic search as a seam extension.

The heavy embedding deps (faiss / numpy / fastembed) are NEVER imported at module
load. The engine is constructed lazily and only touches those deps when a search or
reindex actually runs, so importing this extension without the [semantic] extra
installed is safe (fail-soft: tools return a clean error).
"""

import logging
from pathlib import Path

from obsidian_vault_mcp.extensions import Extension

from . import tools

logger = logging.getLogger(__name__)

_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_REINDEX = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class SemanticExtension(Extension):
    """Hybrid semantic + keyword search over vault markdown, added through the
    server's extension seam without forking the host.

    Fail-soft: with the ``[semantic]`` extra (faiss/numpy/fastembed) not installed,
    or the index not yet built, the tools return ``{"error": ...}`` instead of
    crashing. The engine is built lazily on first use; if the host exposes a
    frontmatter change listener, incremental reindex is wired opportunistically.
    """

    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        """Construct and cache the engine on first use (no heavy imports until then)."""
        if self._engine is None:
            from .engine import SemanticSearchEngine

            self._engine = SemanticSearchEngine()
            tools.set_engine(self._engine)
        return self._engine

    def register_tools(self, mcp) -> None:
        # Build the engine eagerly enough to inject it into the tools module, but the
        # constructor itself imports no heavy deps (those load lazily on search/reindex).
        self._get_engine()
        mcp.tool(
            name="vault_semantic_search",
            description=(
                "Hybrid semantic + keyword search across vault markdown notes. "
                "Requires the [semantic] extra and a built index; returns an error "
                "payload (fail-soft) when dependencies or the index are absent."
            ),
            annotations=_RO,
        )(tools.vault_semantic_search)
        mcp.tool(
            name="vault_reindex",
            description=(
                "Rebuild the semantic-search cache (FAISS + BM25) from the current "
                "vault contents. Pass full=false for an incremental refresh. Requires "
                "the [semantic] extra; returns an error payload when it is unavailable."
            ),
            annotations=_REINDEX,
        )(tools.vault_reindex)

    def after_indexes_start(self, frontmatter_index) -> None:
        """Attach an incremental reindex listener when the host exposes one.

        The host's listener signature is ``callback(abs_path: str, exists: bool)``;
        this adapts it to the engine's ``handle_vault_change(rel_path, action)`` by
        making the path vault-relative and mapping ``exists`` to modify/delete.

        Optional and best-effort: the engine is not built (and no heavy deps loaded)
        unless and until a search/reindex actually runs. The listener only queues
        debounced updates, which the engine ignores while disabled/unavailable.
        """
        add_listener = getattr(frontmatter_index, "add_change_listener", None)
        if not callable(add_listener):
            return

        def _on_change(abs_path: str, exists: bool) -> None:
            try:
                from . import _config as config

                rel_path = Path(abs_path).resolve().relative_to(Path(config.VAULT_PATH).resolve()).as_posix()
                action = "modify" if exists else "delete"
                engine = self._get_engine()
                engine.handle_vault_change(rel_path, action)
            except Exception as exc:  # never let a listener crash the index loop
                logger.debug("semantic change listener ignored %s (exists=%s): %s", abs_path, exists, exc)

        try:
            add_listener(_on_change)
        except Exception as exc:
            logger.debug("could not attach semantic change listener: %s", exc)
