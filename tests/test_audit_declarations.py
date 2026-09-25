"""Every extension tool is declared to the host's audit log, with the right kind.

Since obsidian-web-mcp 0.4.0 (#93) the host audits an operation only when it knows the
name. Before this package used that API, the extension read tools never reached the log,
not even with VAULT_AUDIT_LOG_INCLUDE_READS on, and the write records bypassed the host's
own decision about what to audit.

The kinds are checked against each tool's own readOnlyHint, so a new tool that is
registered but not declared, or declared with the wrong kind, fails here.
"""

import asyncio
import json

import pytest

from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp.audit import operation_kind, register_audit_operation

from obsidian_vault_mcp_ext import _mutations
from obsidian_vault_mcp_ext.__main__ import default_extensions


@pytest.fixture
def mcp():
    server = FastMCP("declarations-under-test")
    for extension in default_extensions():
        extension.register_tools(server)
    return server


def test_every_tool_is_declared_with_the_kind_its_annotation_says(mcp):
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 17, [t.name for t in tools]
    wrong = {}
    for tool in tools:
        expected = "read" if tool.annotations and tool.annotations.readOnlyHint else "mutation"
        if operation_kind(tool.name) != expected:
            wrong[tool.name] = (operation_kind(tool.name), expected)
    assert wrong == {}, wrong


def test_ocr_sidecar_writes_are_declared():
    assert operation_kind("ocr_sidecar") == "mutation"


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "a").mkdir(parents=True)
    (vault / "a" / "n.md").write_text("x", encoding="utf-8")
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(host_config, "VAULT_PATH", vault)
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", str(path))

    def records():
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    return records


@pytest.mark.parametrize("include_reads,expected", [(True, 1), (False, 0)])
def test_a_read_tool_is_logged_only_with_reads_included(mcp, audit_log, monkeypatch, include_reads, expected):
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_INCLUDE_READS", include_reads)

    result = asyncio.run(mcp.call_tool("vault_tree", {"path": "a", "depth": 1}))

    assert "n.md" in json.dumps([getattr(b, "text", "") for b in (result[0] if isinstance(result, tuple) else result)])
    assert len([r for r in audit_log() if r["operation"] == "vault_tree"]) == expected, audit_log()


def test_an_undeclared_write_leaves_no_record(audit_log):
    """The host rule, applied to the per-file records too: unknown names are not audited."""
    with _mutations.mutation("ext_never_declared", "a/n.md"):
        pass

    assert audit_log() == []


def test_a_name_that_shadows_a_built_in_is_refused():
    with pytest.raises(ValueError):
        register_audit_operation("vault_write", "mutation")
