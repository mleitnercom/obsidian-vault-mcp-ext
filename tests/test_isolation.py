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


def test_imports_extension_imports_standalone():
    mod = importlib.import_module("obsidian_vault_mcp_ext.imports")
    assert mod.ImportExtension is not None


def test_imports_extension_pulls_no_heavy_deps_and_no_siblings():
    # The import extension is stdlib-only and must not drag in the semantic extra or a sibling.
    import subprocess
    import sys as _sys

    code = (
        "import sys\n"
        "import obsidian_vault_mcp_ext.imports.tools\n"
        "import obsidian_vault_mcp_ext.imports.extension\n"
        "import obsidian_vault_mcp_ext.imports._fetch\n"
        "for heavy in ('faiss', 'fastembed', 'sentence_transformers', 'numpy'):\n"
        "    assert heavy not in sys.modules, 'imports pulled in ' + heavy\n"
        "sibs = ('semantic', 'recurring', 'templates', 'audit')\n"
        "leaked = [m for m in sys.modules if m.startswith('obsidian_vault_mcp_ext.') and\n"
        "          any(m.startswith('obsidian_vault_mcp_ext.' + s) for s in sibs)]\n"
        "assert leaked == [], 'imports leaked sibling extension imports: ' + repr(leaked)\n"
    )
    result = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_maintenance_extension_imports_standalone():
    mod = importlib.import_module("obsidian_vault_mcp_ext.maintenance")
    assert mod.MaintenanceExtension is not None


def test_maintenance_extension_pulls_no_heavy_deps_and_no_siblings():
    # The maintenance extension is stdlib-only and must not drag in the semantic extra or a sibling.
    import subprocess
    import sys as _sys

    code = (
        "import sys\n"
        "import obsidian_vault_mcp_ext.maintenance.tools\n"
        "import obsidian_vault_mcp_ext.maintenance.extension\n"
        "for heavy in ('faiss', 'fastembed', 'sentence_transformers', 'numpy'):\n"
        "    assert heavy not in sys.modules, 'maintenance pulled in ' + heavy\n"
        "sibs = ('semantic', 'recurring', 'templates', 'imports', 'audit')\n"
        "leaked = [m for m in sys.modules if m.startswith('obsidian_vault_mcp_ext.') and\n"
        "          any(m.startswith('obsidian_vault_mcp_ext.' + s) for s in sibs)]\n"
        "assert leaked == [], 'maintenance leaked sibling extension imports: ' + repr(leaked)\n"
    )
    result = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_templates_does_not_import_sibling_extensions():
    # No extension may import another extension subpackage. Checked in a fresh
    # interpreter so the result is independent of what other tests already
    # imported into this process's sys.modules.
    import subprocess
    import sys as _sys

    code = (
        "import sys\n"
        "import obsidian_vault_mcp_ext.templates.tools\n"
        "import obsidian_vault_mcp_ext.templates.extension\n"
        "sibs = ('semantic', 'recurring', 'audit')\n"
        "leaked = [m for m in sys.modules if m.startswith('obsidian_vault_mcp_ext.') and\n"
        "          any(m.startswith('obsidian_vault_mcp_ext.' + s) for s in sibs)]\n"
        "assert leaked == [], 'templates leaked sibling extension imports: ' + repr(leaked)\n"
    )
    result = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
