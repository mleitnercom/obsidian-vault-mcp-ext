"""Tests for MaintenanceExtension on the host's extension seam.

Covers tool registration plus the three behaviours ported from the fork onto
upstream-public APIs: encoding scan, encoding repair (dry-run and real), and directory
soft-delete. VAULT_PATH is monkeypatched on the host config so everything stays in tmp_path.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext import MaintenanceExtension
from obsidian_vault_mcp_ext.maintenance import tools


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir(parents=True)
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    return v


def test_register_tools(vault):
    mcp = FastMCP("test")
    MaintenanceExtension().register_tools(mcp)
    for name in ("vault_scan_encoding", "vault_repair_encoding", "vault_delete_directory"):
        assert mcp._tool_manager.get_tool(name) is not None


def test_scan_finds_invalid_and_ignores_valid_utf8(vault):
    # b"Caf\xe9" is valid cp1252 ("Café") but invalid UTF-8.
    (vault / "broken.md").write_bytes(b"Caf\xe9")
    (vault / "ok.md").write_text("Café", encoding="utf-8")

    res = json.loads(tools.vault_scan_encoding())
    assert "error" not in res, res
    paths = [issue["path"] for issue in res["issues"]]
    assert "broken.md" in paths
    assert "ok.md" not in paths
    assert res["count"] == 1


def test_scan_skips_hidden_dirs(vault):
    hidden = vault / ".obsidian"
    hidden.mkdir()
    (hidden / "broken.md").write_bytes(b"Caf\xe9")

    res = json.loads(tools.vault_scan_encoding())
    assert res["issues"] == []


def test_repair_dry_run_reports_but_does_not_change(vault):
    f = vault / "broken.md"
    f.write_bytes(b"Caf\xe9")

    res = json.loads(tools.vault_repair_encoding(dry_run=True))
    assert "error" not in res, res
    assert res["dry_run"] is True
    assert [r["path"] for r in res["repaired"]] == ["broken.md"]
    assert res["repaired"][0]["changed"] is False
    # File is untouched and still invalid UTF-8.
    assert f.read_bytes() == b"Caf\xe9"


def test_repair_real_makes_file_valid_utf8(vault):
    f = vault / "broken.md"
    f.write_bytes(b"Caf\xe9")

    res = json.loads(tools.vault_repair_encoding())
    assert "error" not in res, res
    assert res["repaired_count"] == 1
    assert res["repaired"][0]["changed"] is True
    # File is now valid UTF-8 and round-trips to the expected text.
    assert f.read_text(encoding="utf-8") == "Café"

    # A follow-up scan finds nothing.
    scan = json.loads(tools.vault_scan_encoding())
    assert scan["count"] == 0


def test_delete_directory_soft_deletes_empty_dir(vault):
    d = vault / "stale"
    d.mkdir()

    res = json.loads(tools.vault_delete_directory("stale"))
    assert "error" not in res, res
    assert res["deleted"] is True
    assert res["trashed_to"] == ".trash/stale"
    assert not d.exists()
    assert (vault / ".trash" / "stale").is_dir()


def test_delete_directory_refuses_non_empty_when_only_if_empty(vault):
    d = vault / "notes"
    d.mkdir()
    (d / "keep.md").write_text("hi", encoding="utf-8")

    res = json.loads(tools.vault_delete_directory("notes", only_if_empty=True))
    assert "error" in res
    assert "non-empty" in res["error"]
    assert d.exists()


def test_delete_directory_deletes_non_empty_when_allowed(vault):
    d = vault / "notes"
    d.mkdir()
    (d / "keep.md").write_text("hi", encoding="utf-8")

    res = json.loads(tools.vault_delete_directory("notes", only_if_empty=False))
    assert "error" not in res, res
    assert res["deleted"] is True
    assert not d.exists()
    assert (vault / ".trash" / "notes" / "keep.md").read_text(encoding="utf-8") == "hi"
