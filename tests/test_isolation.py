"""Extensions must be independent: importable on their own, no cross-extension imports,
and no heavy/optional dependency pulled in just by importing a feature that doesn't use it.
"""

import importlib
import sys


def test_templates_imports_standalone():
    # The templates extension loads on its own (no other extension required).
    mod = importlib.import_module("obsidian_vault_mcp_ext.templates")
    assert mod.TemplatesExtension is not None


def test_templates_pulls_no_embedding_deps():
    # Importing the templates feature must not drag in the semantic extra's heavy deps.
    importlib.import_module("obsidian_vault_mcp_ext.templates")
    for heavy in ("faiss", "fastembed", "sentence_transformers"):
        assert heavy not in sys.modules, f"templates import pulled in {heavy}"


def test_templates_does_not_import_sibling_extensions():
    # No extension may import another extension subpackage.
    import obsidian_vault_mcp_ext.templates.tools as tools  # noqa: F401
    import obsidian_vault_mcp_ext.templates.extension as ext  # noqa: F401
    leaked = [m for m in sys.modules if m.startswith("obsidian_vault_mcp_ext.") and
              any(m.startswith(f"obsidian_vault_mcp_ext.{sib}") for sib in ("semantic", "recurring", "audit"))]
    assert leaked == [], f"templates leaked sibling extension imports: {leaked}"
