"""Tests for RecurringExtension on the host's extension seam.

The vault is a temp dir; obsidian_vault_mcp.config.VAULT_PATH is monkeypatched to it
(the ext _config proxies VAULT_PATH from the host dynamically). The recurring _config
knobs are set via monkeypatch.setattr, mirroring the templates extension tests.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext.recurring import RecurringExtension, _config, tools as recurring


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "templates").mkdir(parents=True)
    (v / "tasks").mkdir(parents=True)
    # An absolute, fixed-date template with a `created` baseline so it fires.
    (v / "templates" / "vat.md").write_text(
        "---\n"
        "type: recurring-template\n"
        "id: vat-return\n"
        "recurrence_anchor_mode: absolute\n"
        "recurrence_anchor: fixed-07-31\n"
        "created: 2026-01-01\n"
        "target_folder: tasks\n"
        "due_offset_days: 0\n"
        "priority_initial: A\n"
        "frontmatter_to_inherit:\n"
        "  scope: pbs\n"
        "  area: finance\n"
        "tags_to_inherit:\n"
        "  - vat\n"
        "---\n"
        "Body of the template.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    monkeypatch.setattr(_config, "VAULT_RECURRING_ENABLED", True)
    monkeypatch.setattr(_config, "VAULT_RECURRING_TEMPLATES_FOLDER", "templates")
    monkeypatch.setattr(_config, "VAULT_RECURRING_CATCHUP_MODE", "next")
    monkeypatch.setattr(_config, "VAULT_RECURRING_DONE_STATUS", "done")
    return v


def test_register_tools_registers_recurring_materialize(vault):
    mcp = FastMCP("test")
    RecurringExtension().register_tools(mcp)
    assert mcp._tool_manager.get_tool("recurring_materialize") is not None


def test_materialize_creates_instance_with_inherited_frontmatter(vault):
    res = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
    assert res["errors"] == [], res
    assert len(res["created"]) == 1, res
    item = res["created"][0]
    assert item["template_id"] == "vat-return"
    assert item["period"] == "fixed-07-31-2026"

    instance_path = vault / item["path"]
    assert instance_path.is_file()

    import frontmatter

    post = frontmatter.loads(instance_path.read_text(encoding="utf-8"))
    fm = post.metadata
    # Idempotency identity fields.
    assert fm["recurrence_template"] == "vat-return"
    assert fm["recurrence_period"] == "fixed-07-31-2026"
    # Anchor feature: fixed-07-31 -> trigger 2026-07-31, due_offset 0.
    assert fm["due"] == "2026-07-31"
    # Inherited frontmatter (dict form) is copied verbatim.
    assert fm["scope"] == "pbs"
    assert fm["area"] == "finance"
    # priority_initial -> priority.
    assert fm["priority"] == "A"
    # Marker tag + inherited tag.
    assert "recurring-instance" in fm["tags"]
    assert "vat" in fm["tags"]


def test_materialize_is_idempotent(vault):
    first = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
    assert len(first["created"]) == 1, first

    second = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
    # Strict idempotency: a second run creates nothing new.
    assert second["created"] == [], second

    # Exactly one instance file on disk.
    instances = list((vault / "tasks").glob("*.md"))
    assert len(instances) == 1


def test_disk_lookup_detects_preexisting_instance(vault, monkeypatch):
    # The disk-based idempotency lookup must detect an instance written by a
    # prior process (no last_run advanced yet), independent of the index.
    # catchup='all' so the absolute anchor is re-derived without a last_run gate.
    monkeypatch.setattr(_config, "VAULT_RECURRING_CATCHUP_MODE", "all")
    (vault / "tasks" / "recurring-vat-return-fixed-07-31-2026.md").write_text(
        "---\n"
        "recurrence_template: vat-return\n"
        "recurrence_period: fixed-07-31-2026\n"
        "---\n"
        "pre-existing\n",
        encoding="utf-8",
    )
    res = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
    assert res["created"] == [], res
    already = [s for s in res["skipped"] if s.get("reason") == "already_exists"]
    assert len(already) == 1, res
    assert already[0]["existing_path"] == "tasks/recurring-vat-return-fixed-07-31-2026.md"


def test_relative_mode_bootstrap_and_idempotency(vault, monkeypatch):
    (vault / "templates" / "weekly.md").write_text(
        "---\n"
        "type: recurring-template\n"
        "id: weekly-review\n"
        "recurrence_anchor_mode: relative\n"
        "recurrence_interval: 7d\n"
        "target_folder: tasks\n"
        "---\n"
        "Weekly.\n",
        encoding="utf-8",
    )
    first = json.loads(
        recurring.recurring_materialize(template_id="weekly-review", as_of="2026-08-01")
    )
    assert len(first["created"]) == 1, first
    assert first["created"][0]["period"] == "2026-08-01"

    # Second run: no done instance, last_run now set -> next trigger is in the
    # future, so nothing new is created.
    second = json.loads(
        recurring.recurring_materialize(template_id="weekly-review", as_of="2026-08-01")
    )
    assert second["created"] == [], second


def test_disabled_returns_capability_error(vault, monkeypatch):
    monkeypatch.setattr(_config, "VAULT_RECURRING_ENABLED", False)
    res = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
    assert res["error_code"] == "recurring_disabled"


def test_unset_folder_returns_capability_error(vault, monkeypatch):
    monkeypatch.setattr(_config, "VAULT_RECURRING_TEMPLATES_FOLDER", "")
    res = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
    assert res["error_code"] == "recurring_folder_unset"
