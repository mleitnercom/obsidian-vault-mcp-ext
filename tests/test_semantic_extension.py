"""Tests for SemanticExtension on the host's extension seam.

Mirrors the templates test's fixtures: a temp vault with the host's
``obsidian_vault_mcp.config.VAULT_PATH`` monkeypatched to it.

The heavy embedding deps (faiss/numpy/fastembed/rank_bm25) are optional. Tests
(a)-(c) prove the extension imports and registers and fails soft WITHOUT them.
The real reindex+search test (d) is skipped unless they are installed.
"""

import importlib
import json
import sys

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext.semantic import SemanticExtension
from obsidian_vault_mcp_ext.semantic import _config, tools as semantic_tools


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir(parents=True)
    (v / "alpha.md").write_text(
        "---\ntitle: Alpha\ntags: [project]\n---\n# Heading\n\nThe quick brown fox jumps over the lazy dog.\n",
        encoding="utf-8",
    )
    (v / "beta.md").write_text(
        "---\ntitle: Beta\n---\n# Notes\n\nMachine learning embeddings power semantic retrieval.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    monkeypatch.setattr(_config, "SEMANTIC_SEARCH_ENABLED", True)
    monkeypatch.setattr(_config, "SEMANTIC_BUILD_ON_DEMAND", True)
    # Reset any engine injected by a prior test so each test starts clean.
    semantic_tools.set_engine(None)
    return v


# (a) Imports WITHOUT faiss installed (proves lazy import).
def test_semantic_imports_without_faiss():
    mod = importlib.import_module("obsidian_vault_mcp_ext.semantic")
    assert mod.SemanticExtension is not None
    for heavy in ("faiss", "fastembed", "sentence_transformers", "numpy"):
        assert heavy not in sys.modules, f"semantic import pulled in {heavy}"


# (b) register_tools registers the tools on a FastMCP instance.
def test_register_tools_registers_search_and_reindex(vault):
    mcp = FastMCP("test")
    SemanticExtension().register_tools(mcp)
    for name in ("vault_semantic_search", "vault_reindex"):
        assert mcp._tool_manager.get_tool(name) is not None


# (c) The search tool fails soft (clean error/empty) when deps/index are absent.
def test_search_fails_soft_without_deps(vault):
    SemanticExtension().register_tools(mcp := FastMCP("test"))  # noqa: F841 -- wires engine into tools
    res = json.loads(semantic_tools.vault_semantic_search("anything"))
    # Either a clean capability error or an empty result set -- never a crash.
    assert "error" in res or res.get("results") == []


def test_reindex_fails_soft_without_deps(vault):
    SemanticExtension().register_tools(FastMCP("test"))
    res = json.loads(semantic_tools.vault_reindex(full=True))
    assert "error" in res


def test_search_without_engine_reports_unavailable():
    # No extension/engine wired at all.
    semantic_tools.set_engine(None)
    res = json.loads(semantic_tools.vault_semantic_search("q"))
    assert res["error"] == "Semantic search engine is unavailable"


# (d) Full reindex + search over a tiny vault, only when the heavy deps are present.
def _deps_available() -> bool:
    for mod in ("faiss", "fastembed", "rank_bm25", "numpy"):
        try:
            importlib.import_module(mod)
        except Exception:
            return False
    return True


@pytest.mark.skipif(not _deps_available(), reason="semantic extra (faiss/fastembed/rank_bm25/numpy) not installed")
def test_full_reindex_and_search(vault):
    ext = SemanticExtension()
    ext.register_tools(FastMCP("test"))
    reindex = json.loads(semantic_tools.vault_reindex(full=True))
    assert "error" not in reindex, reindex
    assert reindex["indexed_files"] == 2
    assert reindex["indexed_chunks"] >= 2

    res = json.loads(semantic_tools.vault_semantic_search("semantic retrieval embeddings", max_results=5))
    assert "error" not in res, res
    assert res["total"] >= 1
    assert any(r["path"] == "beta.md" for r in res["results"])
