"""CompatExtension: the fork's tool names, served by the host's vault_edit and a tree walk.

Called through a FastMCP registration, the way a client reaches them. The protections are
inherited, not re-implemented, so the tests prove they arrive: the default refuses a
repeated match, a binary is refused and left untouched, a hardlink is refused, and one
edit produces exactly one write event and one audit record.
"""

import asyncio
import json
import os

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp import write_events

from obsidian_vault_mcp_ext.compat import CompatExtension

NOTE = "Der Zaehlerstand steht hier. Der Zaehlerstand steht dort.\n"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", "")
    (v / "note.md").write_bytes(NOTE.encode("utf-8"))
    return v


@pytest.fixture
def mcp():
    server = FastMCP("compat-test")
    CompatExtension().register_tools(server)
    return server


@pytest.fixture
def events():
    write_events._write_listeners.clear()
    captured = []
    write_events.register_write_listener(lambda op, paths: captured.append((op, paths)))
    yield captured
    write_events._write_listeners.clear()


def call(mcp, name, arguments):
    result = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(result, tuple):
        result = result[0]
    return json.loads("".join(getattr(block, "text", "") for block in result))


def test_all_four_tools_are_registered(mcp):
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert {"vault_str_replace", "vault_patch", "vault_batch_replace", "vault_tree"} <= names


# --- vault_str_replace ------------------------------------------------------------------

def test_str_replace_refuses_a_repeated_match_by_default(vault, mcp):
    """Negative control for replace_all: the term is there twice."""
    result = call(mcp, "vault_str_replace", {"path": "note.md", "old_string": "Zaehlerstand", "new_string": "Zählerstand"})

    assert "error" in result, result
    assert (vault / "note.md").read_bytes() == NOTE.encode("utf-8")


def test_str_replace_all_replaces_every_occurrence_in_the_fork_shape(vault, mcp):
    result = call(
        mcp,
        "vault_str_replace",
        {"path": "note.md", "old_string": "Zaehlerstand", "new_string": "Stand", "replace_all": True},
    )

    assert result["replaced"] is True and result["changed"] is True, result
    assert result["occurrences_found"] == 2 and result["replace_all"] is True
    # Two occurrences, each seven bytes shorter.
    assert result["size_delta"] == result["size_after"] - result["size_before"] == -14
    body = (vault / "note.md").read_text(encoding="utf-8")
    assert body.count("Stand") == 2 and "Zaehlerstand" not in body


def test_str_replace_near_miss_comes_through(vault, mcp):
    result = call(mcp, "vault_str_replace", {"path": "note.md", "old_string": "Zahlerstand steht hier", "new_string": "x"})

    assert "error" in result and result.get("near_miss", {}).get("line_number") == 1, result


# --- vault_patch --------------------------------------------------------------------------

def test_patch_replaces_one_unique_occurrence(vault, mcp):
    result = call(mcp, "vault_patch", {"path": "note.md", "old_text": "steht hier", "new_text": "stand hier"})

    assert result["changed"] is True, result
    assert "stand hier" in (vault / "note.md").read_text(encoding="utf-8")


def test_patch_refuses_a_repeated_match(vault, mcp):
    result = call(mcp, "vault_patch", {"path": "note.md", "old_text": "Zaehlerstand", "new_text": "x"})

    assert "error" in result, result


# --- vault_batch_replace ------------------------------------------------------------------

def test_batch_replace_handles_each_file_on_its_own(vault, mcp):
    (vault / "b.md").write_bytes(b"alpha beta\n")
    result = call(
        mcp,
        "vault_batch_replace",
        {"updates": [
            {"path": "note.md", "old_str": "Zaehlerstand", "new_str": "Z", "replace_all": True},
            {"path": "b.md", "old_str": "fehlt", "new_str": "x"},
            {"path": "b.md", "old_str": "alpha", "new_str": "gamma"},
        ]},
    )

    first, missing, third = result["results"]
    assert first["occurrences_found"] == 2
    assert "error" in missing
    assert third["changed"] is True
    assert (vault / "b.md").read_bytes() == b"gamma beta\n"


def test_batch_replace_caps_the_batch(vault, mcp):
    result = call(mcp, "vault_batch_replace", {"updates": [{"path": "note.md", "old_str": "a", "new_str": "b"}] * 21})

    assert "error" in result and "20" in result["error"]


# --- inherited protections ----------------------------------------------------------------

def test_a_binary_is_refused_and_left_untouched(vault, mcp):
    pdf = b"%PDF-1.4\n\xff\xfe\x00 scan\n%%EOF\n"
    (vault / "scan.pdf").write_bytes(pdf)

    result = call(mcp, "vault_str_replace", {"path": "scan.pdf", "old_string": "scan", "new_string": "x", "replace_all": True})

    assert "error" in result, result
    assert (vault / "scan.pdf").read_bytes() == pdf


def test_a_hardlink_is_refused(vault, mcp, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"geheim steht hier\n")
    try:
        os.link(outside, vault / "linked.md")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")

    result = call(mcp, "vault_patch", {"path": "linked.md", "old_text": "geheim", "new_text": "offen"})

    assert "error" in result, result
    assert outside.read_bytes() == b"geheim steht hier\n"


def test_one_edit_is_one_event_and_one_audit_record(vault, mcp, events, tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", str(log))

    call(mcp, "vault_patch", {"path": "note.md", "old_text": "steht hier", "new_text": "stand hier"})

    assert events == [("updated", ["note.md"])], "the host fires it; the wrapper must not fire it again"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["operation"] for r in records] == ["vault_patch"], records
    assert records[0]["checksum_before"] != records[0]["checksum_after"]


def test_a_refused_edit_is_audited_as_an_error(vault, mcp, tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", str(log))

    call(mcp, "vault_patch", {"path": "note.md", "old_text": "gibt es nicht", "new_text": "x"})

    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1 and records[0]["operation_status"] == "error", records


# --- vault_tree -------------------------------------------------------------------------

def test_tree_nests_and_hides_what_the_vault_hides(vault, mcp, tmp_path):
    (vault / "a" / "b" / "c").mkdir(parents=True)
    (vault / "a" / "eins.md").write_bytes(b"x")
    (vault / "a" / "b" / "zwei.md").write_bytes(b"x")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_bytes(b"{}")
    (vault / ".trash").mkdir()
    outside = tmp_path / "draussen"
    outside.mkdir()
    try:
        os.symlink(outside, vault / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pass

    tree = call(mcp, "vault_tree", {"depth": 1})

    assert tree["path"] == "/"
    assert tree["files"] == ["note.md"]
    names = [d["name"] for d in tree["dirs"]]
    assert names == ["a"], names
    a = tree["dirs"][0]
    assert a["files"] == ["eins.md"]
    assert a["dirs"] == [{"name": "b", "file_count": 1, "dir_count": 1}]


def test_tree_refuses_a_path_outside_the_vault(vault, mcp):
    result = call(mcp, "vault_tree", {"path": "../"})

    assert "error" in result, result
