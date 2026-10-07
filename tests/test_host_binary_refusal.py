"""The ext tools that write text leave binary files alone (host behaviour since jimprosser#105).

The host decides by extension: read_file refuses a binary type as text and
write_file_atomic will not create or replace one. The compat tools reach the host's
vault_edit and the templates write through write_file_atomic, so an all-ASCII PDF (which
decodes as UTF-8) must come through them unchanged. On a host before #105 the same calls
changed the PDF and wrote a text "PDF" (probe of 28.09.2026); there these tests skip.
"""

import asyncio
import json
import os

import pytest

from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext.compat import CompatExtension
from obsidian_vault_mcp_ext.templates import TemplatesExtension
from obsidian_vault_mcp_ext.templates import _config as templates_config

REQUIRE = os.environ.get("VAULT_TEST_REQUIRE_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}

STREAM = b"BT\n/F1 24 Tf\n72 100 Td\n(Invoice total 1200 EUR) Tj\nET"
PDF = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(STREAM)).encode() + b" >>\nstream\n" + STREAM
       + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")


@pytest.fixture(autouse=True)
def host_with_105():
    from obsidian_vault_mcp import vault

    if not hasattr(vault, "BinaryFileTypeError"):
        if REQUIRE:
            pytest.fail("host before jimprosser#105 under VAULT_TEST_REQUIRE_TOOLS=1")
        pytest.skip("host before jimprosser#105: binary types are decided by content there")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "_templates").mkdir(parents=True)
    (vault / "invoice.pdf").write_bytes(PDF)
    PDF.decode("utf-8")  # the premise: valid UTF-8, so only the extension can protect it
    (vault / "_templates" / "t.md").write_text("Hallo {{name}}\n", encoding="utf-8")
    monkeypatch.setattr(host_config, "VAULT_PATH", vault)
    monkeypatch.setattr(templates_config, "VAULT_TEMPLATER_FOLDER", "_templates")
    monkeypatch.setattr(templates_config, "VAULT_OBSIDIAN_REST_URL", "")
    return vault


@pytest.fixture(scope="module")
def mcp():
    server = FastMCP("binary-refusal")
    CompatExtension().register_tools(server)
    TemplatesExtension().register_tools(server)
    return server


def call(mcp, name: str, arguments: dict) -> dict:
    result = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(result, tuple):
        result = result[0]
    return json.loads("".join(getattr(block, "text", "") for block in result))


@pytest.mark.parametrize("tool,arguments", [
    ("vault_str_replace", {"path": "invoice.pdf", "old_string": "1200", "new_string": "12000"}),
    ("vault_patch", {"path": "invoice.pdf", "old_text": "1200", "new_text": "12000"}),
    ("vault_batch_replace", {"updates": [{"path": "invoice.pdf", "old_string": "1200", "new_string": "12000"}]}),
])
def test_compat_edits_leave_a_pdf_unchanged(vault, mcp, tool, arguments):
    result = call(mcp, tool, arguments)

    assert (vault / "invoice.pdf").read_bytes() == PDF, f"{tool} changed the PDF: {result}"
    assert "binary file" in json.dumps(result), result


@pytest.mark.parametrize("target,overwrite", [("rendered.pdf", False), ("invoice.pdf", True)])
def test_a_template_cannot_create_or_replace_a_pdf(vault, mcp, target, overwrite):
    result = call(mcp, "vault_template_apply", {"template_path": "_templates/t.md", "target_path": target,
                                           "variables": {"name": "X"}, "overwrite": overwrite})

    assert result.get("error_code") == "path_not_allowed", result
    assert "binary file type" in result.get("error", ""), result
    assert (vault / "invoice.pdf").read_bytes() == PDF
    assert not (vault / "rendered.pdf").exists()


def test_notes_still_work_through_the_same_tools(vault, mcp):
    """Guard: the refusal is about the extension, not about these tools."""
    (vault / "n.md").write_text("Betrag 1200\n", encoding="utf-8")

    assert call(mcp, "vault_str_replace", {"path": "n.md", "old_string": "1200", "new_string": "12000"}).get("replaced") is True
    assert call(mcp, "vault_template_apply", {"template_path": "_templates/t.md", "target_path": "out.md",
                                         "variables": {"name": "X"}}).get("created") is True
    assert (vault / "n.md").read_text(encoding="utf-8") == "Betrag 12000\n"
