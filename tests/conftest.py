"""Shared test setup.

Production registers every extension through ``serve(extensions=...)``, which calls each
``register_tools``; that is where the extensions declare their operation names to the
host's audit log. Many tests call the tool functions directly, so the same declaration
is made here once per session, through the same ``register_tools`` calls against a
throwaway FastMCP instance. test_audit_declarations.py checks that path itself.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def extensions_declared():
    try:
        from mcp.server.fastmcp import FastMCP

        from obsidian_vault_mcp_ext.__main__ import default_extensions
    except ImportError:  # host without the seam; those tests skip themselves
        yield
        return
    mcp = FastMCP("declarations")
    for extension in default_extensions():
        extension.register_tools(mcp)
    yield
