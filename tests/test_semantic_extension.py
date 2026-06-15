"""Tests for SemanticExtension on the host's extension seam.

Mirrors the templates test's fixtures: a temp vault with the host's
``obsidian_vault_mcp.config.VAULT_PATH`` monkeypatched to it.

The heavy embedding deps (faiss/numpy/fastembed/rank_bm25) are an optional extra. The
suite is correct in BOTH environments: the lazy-import / fail-soft tests run only when the
extra is ABSENT, and the real reindex+search test runs only when it is PRESENT.
"""

import importlib
import json
import sys

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext.semantic import SemanticExtension
from obsidian_vault_mcp_ext.semantic import _config, tools as semantic_tools


def _deps_available() -> bool:
    for mod in ("faiss", "fastembed", "rank_bm25", "numpy"):
        try:
            importlib.import_module(mod)
        except Exception:
            return False
    return True


_HAVE_DEPS = _deps_available()
_only_without_deps = pytest.mark.skipif(_HAVE_DEPS, reason="only meaningful without the semantic extra installed")
_only_with_deps = pytest.mark.skipif(not _HAVE_DEPS, reason="semantic extra (faiss/fastembed/rank_bm25/numpy) not installed")


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


# register_tools always works (no heavy deps needed to register).
def test_register_tools_registers_search_and_reindex(vault):
    mcp = FastMCP("test")
    SemanticExtension().register_tools(mcp)
    for name in ("vault_semantic_search", "vault_reindex"):
        assert mcp._tool_manager.get_tool(name) is not None


# With no engine wired at all, the tool reports unavailable (deps-independent).
def test_search_without_engine_reports_unavailable():
    semantic_tools.set_engine(None)
    res = json.loads(semantic_tools.vault_semantic_search("q"))
    assert res["error"] == "Semantic search engine is unavailable"


# --- Only meaningful WITHOUT the semantic extra ---

@_only_without_deps
def test_semantic_imports_without_faiss():
    mod = importlib.import_module("obsidian_vault_mcp_ext.semantic")
    assert mod.SemanticExtension is not None
    for heavy in ("faiss", "fastembed", "sentence_transformers", "numpy"):
        assert heavy not in sys.modules, f"semantic import pulled in {heavy}"


@_only_without_deps
def test_search_fails_soft_without_deps(vault):
    SemanticExtension().register_tools(FastMCP("test"))  # wires engine into tools
    res = json.loads(semantic_tools.vault_semantic_search("anything"))
    assert "error" in res or res.get("results") == []  # clean error/empty, never a crash


@_only_without_deps
def test_reindex_fails_soft_without_deps(vault):
    SemanticExtension().register_tools(FastMCP("test"))
    res = json.loads(semantic_tools.vault_reindex(full=True))
    assert "error" in res


# --- Only when the semantic extra IS installed: real reindex + search ---

@_only_with_deps
def test_full_reindex_and_search(vault):
    SemanticExtension().register_tools(FastMCP("test"))
    reindex = json.loads(semantic_tools.vault_reindex(full=True))
    assert "error" not in reindex, reindex
    assert reindex["indexed_files"] == 2
    assert reindex["indexed_chunks"] >= 2

    res = json.loads(semantic_tools.vault_semantic_search("semantic retrieval embeddings", max_results=5))
    assert "error" not in res, res
    assert res["total"] >= 1
    assert any(r["path"] == "beta.md" for r in res["results"])
