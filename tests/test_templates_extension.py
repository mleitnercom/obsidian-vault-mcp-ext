"""Tests for TemplatesExtension on the host's extension seam."""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext import TemplatesExtension, _config, templates


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "templates").mkdir(parents=True)
    (v / "templates" / "note.md").write_text(
        "# {{title}}\n\nCreated {{date}} for {{variables.who}}.\n", encoding="utf-8"
    )
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    monkeypatch.setattr(_config, "VAULT_TEMPLATER_FOLDER", "templates")
    monkeypatch.setattr(_config, "VAULT_OBSIDIAN_REST_URL", "")  # Dataview fail-soft
    return v


def test_register_tools_registers_all_four(vault):
    mcp = FastMCP("test")
    TemplatesExtension().register_tools(mcp)
    for name in ("vault_template_list", "vault_template_render", "vault_template_apply", "vault_dataview_query"):
        assert mcp._tool_manager.get_tool(name) is not None


def test_template_list(vault):
    res = json.loads(templates.vault_template_list())
    assert res["total"] == 1
    assert res["templates"][0]["path"] == "templates/note.md"


def test_template_render_substitutes_tokens(vault):
    res = json.loads(templates.vault_template_render("note", target_path_hint="out.md", variables={"who": "Michael"}))
    assert "error" not in res, res
    assert "for Michael" in res["content"]
    assert res["content"].startswith("# out")  # title from target_path_hint stem


def test_template_render_rejects_templater_syntax(vault):
    (vault / "templates" / "bad.md").write_text("<% tp.date.now() %>", encoding="utf-8")
    res = json.loads(templates.vault_template_render("bad"))
    assert res["error_code"] == "template_render_unavailable"


def test_template_apply_writes_file(vault):
    res = json.loads(templates.vault_template_apply("note", "notes/out.md", variables={"who": "X"}))
    assert "error" not in res, res
    assert res["created"] is True
    assert (vault / "notes" / "out.md").read_text(encoding="utf-8").startswith("# out")


def test_template_apply_refuses_existing_without_overwrite(vault):
    (vault / "exists.md").write_text("keep", encoding="utf-8")
    res = json.loads(templates.vault_template_apply("note", "exists.md", variables={"who": "X"}))
    assert res["error_code"] == "target_exists"
    assert (vault / "exists.md").read_text(encoding="utf-8") == "keep"


def test_dataview_fails_soft_without_rest_url(vault):
    res = json.loads(templates.vault_dataview_query("TABLE file.name"))
    assert res["error_code"] == "capability_unavailable"
