"""Every write an extension makes shows up where the host's own writes do.

The host's mutation tools append an audit record and fire a write event. Tools registered
by an extension did neither: a template apply, a recurring instance, an import, an
encoding repair or a directory soft-delete changed the vault while the audit log and every
write listener saw nothing, although the host README promises one record per mutation.
Surfaced by upstream issue #89, where another extension author hit the same gap.

Each test performs the real write through the extension tool, then asserts on the event a
registered listener received and on the record in a real audit log file. Against the
previous revision every one of them sees no event and no record.
"""

import json

import pytest

pytest.importorskip("obsidian_vault_mcp.write_events")
pytest.importorskip("obsidian_vault_mcp.audit")

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp import write_events

from obsidian_vault_mcp_ext import _mutations
from obsidian_vault_mcp_ext.imports import _config as import_config
from obsidian_vault_mcp_ext.imports import tools as imports
from obsidian_vault_mcp_ext.maintenance import tools as maintenance
from obsidian_vault_mcp_ext.recurring import _config as recurring_config
from obsidian_vault_mcp_ext.recurring import tools as recurring
from obsidian_vault_mcp_ext.templates import _config as templates_config
from obsidian_vault_mcp_ext.templates import tools as templates

TEMPLATE = (
    "---\n"
    "type: recurring-template\n"
    "id: monatsbericht\n"
    "title: Monatsbericht\n"
    "recurrence_anchor_mode: absolute\n"
    "recurrence_anchor: fixed-07-31\n"
    "created: 2026-01-01\n"
    "target_folder: tasks\n"
    "due_offset_days: 0\n"
    "---\n"
    "## Next Action (Template)\n"
    "Bericht schreiben.\n"
)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    return v


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    path = tmp_path / "audit" / "audit.jsonl"
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", str(path))

    def records():
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    return records


@pytest.fixture
def events():
    write_events._write_listeners.clear()
    captured = []
    write_events.register_write_listener(lambda op, paths: captured.append((op, paths)))
    yield captured
    write_events._write_listeners.clear()


def _one(records, operation):
    matching = [r for r in records() if r["operation"] == operation]
    assert len(matching) == 1, records()
    return matching[0]


def test_template_apply(vault, audit_log, events, monkeypatch):
    (vault / "templates").mkdir()
    (vault / "templates" / "note.md").write_text("# {{title}}\n", encoding="utf-8")
    monkeypatch.setattr(templates_config, "VAULT_TEMPLATER_FOLDER", "templates")
    monkeypatch.setattr(templates_config, "VAULT_OBSIDIAN_REST_URL", "")

    result = json.loads(templates.vault_template_apply("note", target_path="neu.md"))

    assert result.get("created") is True, result
    assert events == [("created", ["neu.md"])]
    record = _one(audit_log, "vault_template_apply")
    assert record["target_path"] == "neu.md" and record["operation_status"] == "success"
    assert record["size_after"] == (vault / "neu.md").stat().st_size


def test_recurring_instance(vault, audit_log, events, monkeypatch):
    (vault / "templates").mkdir()
    (vault / "tasks").mkdir()
    (vault / "templates" / "bericht.md").write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_ENABLED", True)
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_TEMPLATES_FOLDER", "templates")
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_CATCHUP_MODE", "next")
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_DONE_STATUS", "done")

    result = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
    instance = result["created"][0]["path"]

    assert ("created", [instance]) in events, events
    records = [r for r in audit_log() if r["operation"] == "recurring_materialize" and r["target_path"] == instance]
    assert records and records[0]["operation_status"] == "success", audit_log()


def test_encoding_repair(vault, audit_log, events):
    (vault / "alt.md").write_bytes("Geschäftsführer\n".encode("cp1252"))

    result = json.loads(maintenance.vault_repair_encoding())

    assert [r["path"] for r in result["repaired"]] == ["alt.md"], result
    assert events == [("updated", ["alt.md"])]
    record = _one(audit_log, "vault_repair_encoding")
    assert record["checksum_before"] != record["checksum_after"]


def test_directory_soft_delete(vault, audit_log, events):
    (vault / "leer").mkdir()

    result = json.loads(maintenance.vault_delete_directory("leer"))

    assert result.get("deleted") is True, result
    assert events == [("deleted", ["leer"])]
    assert _one(audit_log, "vault_delete_directory")["operation_status"] == "success"


def test_file_import(vault, tmp_path, audit_log, events, monkeypatch):
    source_root = tmp_path / "quelle"
    source_root.mkdir()
    (source_root / "bild.png").write_bytes(b"\x89PNG\r\n\x1a\nbytes")
    monkeypatch.setattr(import_config, "allowed_file_roots", lambda: [str(source_root)])

    result = json.loads(imports.vault_import_file("bilder/bild.png", str(source_root / "bild.png"), "image/png"))

    assert result.get("created") is True, result
    assert events == [("created", ["bilder/bild.png"])]
    assert _one(audit_log, "vault_import_file")["size_after"] == len(b"\x89PNG\r\n\x1a\nbytes")


def test_a_failed_write_is_recorded_as_an_error_and_fires_nothing(vault, audit_log, events, monkeypatch):
    (vault / "templates").mkdir()
    (vault / "templates" / "note.md").write_text("# {{title}}\n", encoding="utf-8")
    monkeypatch.setattr(templates_config, "VAULT_TEMPLATER_FOLDER", "templates")
    monkeypatch.setattr(templates_config, "VAULT_OBSIDIAN_REST_URL", "")

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(templates, "write_file_atomic", broken)

    result = json.loads(templates.vault_template_apply("note", target_path="neu.md"))

    assert "error" in json.dumps(result), result
    assert events == []
    record = _one(audit_log, "vault_template_apply")
    assert record["operation_status"] == "error" and "disk full" in record["error"]


def test_with_auditing_off_events_still_fire_and_nothing_is_hashed(vault, events, monkeypatch):
    """Snapshots read the whole file; with auditing off nothing records them."""
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", "")
    monkeypatch.setattr(_mutations, "_snapshot_path", lambda path: pytest.fail("hashed a file with auditing off"))
    (vault / "alt.md").write_bytes("Geschäftsführer\n".encode("cp1252"))

    json.loads(maintenance.vault_repair_encoding())

    assert events == [("updated", ["alt.md"])]
