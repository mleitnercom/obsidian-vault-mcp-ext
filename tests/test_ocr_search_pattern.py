"""OcrExtension registers its sidecar pattern with the host's search seam (jimprosser#96).

OCR text is persisted as ``<file>.ocr.txt``. The host's ``vault_search`` looks at notes by
default, so without the registration that text is found only when the caller names the
pattern, and models do not. What must hold:

- with OCR and sidecars on, the default ``vault_search`` finds sidecar text;
- names stay on notes (the host's rule), an explicit pattern is used as given;
- OCR off, sidecars off, an unusable suffix or a host without the seam: nothing is
  registered and nothing raises, so the server still starts.

Through the host's registered ``vault_search``, and once through a real server process.
"""

import asyncio
import json
import os

import pytest

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext.ocr import OcrExtension
from obsidian_vault_mcp_ext.ocr import _config as ocr_config

from test_end_to_end_http import live_package, session  # tests/ is on sys.path (rootdir, no package)

REQUIRE = os.environ.get("VAULT_TEST_REQUIRE_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}
SIDECAR = (
    "<!-- obsidian-vault-mcp ocr v1 source=scan.pdf size=10 mtime_ns=1 -->\n"
    "Zaehlerstand Wasser 4711 laut Ablesung\n"
)


def _seam():
    from obsidian_vault_mcp import content_extractors

    if not hasattr(content_extractors, "register_search_pattern"):
        if REQUIRE:
            pytest.fail("host has no register_search_pattern (before jimprosser#96) under VAULT_TEST_REQUIRE_TOOLS=1")
        pytest.skip("host has no register_search_pattern (before jimprosser#96)")
    return content_extractors


@pytest.fixture
def seam(monkeypatch):
    content_extractors = _seam()
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_SIDECAR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_SIDECAR_SUFFIX", ".ocr.txt")
    content_extractors._search_patterns.clear()
    yield content_extractors
    content_extractors._search_patterns.clear()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Notiz ohne den Begriff\n", encoding="utf-8")
    (vault / "scan.pdf").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    (vault / "scan.pdf.ocr.txt").write_text(SIDECAR, encoding="utf-8")
    monkeypatch.setattr(host_config, "VAULT_PATH", vault)
    return vault


def search(**arguments) -> list[dict]:
    from obsidian_vault_mcp import server

    result = asyncio.run(server.mcp.call_tool("vault_search", arguments))
    if isinstance(result, tuple):
        result = result[0]
    payload = json.loads("".join(getattr(block, "text", "") for block in result))
    assert "error" not in payload, payload
    return payload["results"]


def test_control_without_the_registration_sidecar_text_is_not_found(seam, vault):
    assert search(query="Zaehlerstand") == []
    assert [h["path"] for h in search(query="Zaehlerstand", file_pattern="*.ocr.txt")] == ["scan.pdf.ocr.txt"]


def test_the_default_search_finds_sidecar_text(seam, vault):
    assert OcrExtension().register_search_pattern() is True

    hits = search(query="Zaehlerstand")

    assert [(h["path"], h["match_type"]) for h in hits] == [("scan.pdf.ocr.txt", "content")]


def test_names_stay_on_notes(seam, vault):
    """"pdf.ocr" is in the sidecar's name and nowhere in any text: only a name match finds it."""
    OcrExtension().register_search_pattern()

    assert search(query="pdf.ocr") == []
    assert [(h["path"], h["match_type"]) for h in search(query="pdf.ocr", file_pattern="*.ocr.txt")] == [
        ("scan.pdf.ocr.txt", "filename")]


@pytest.mark.parametrize("setting", ["OCR_ENABLED", "OCR_SIDECAR_ENABLED"])
def test_nothing_is_registered_when_ocr_or_sidecars_are_off(seam, monkeypatch, setting):
    monkeypatch.setattr(ocr_config, setting, False)

    assert OcrExtension().register_search_pattern() is False
    assert seam.default_search_patterns() == ["*.md"]


def test_an_unusable_suffix_is_skipped_without_raising(seam, monkeypatch, caplog):
    monkeypatch.setattr(ocr_config, "OCR_SIDECAR_SUFFIX", ".ocr txt")  # whitespace: the host refuses it

    assert OcrExtension().register_search_pattern() is False
    assert seam.default_search_patterns() == ["*.md"]
    assert "not registered" in caplog.text


def test_a_host_without_the_seam_is_skipped_without_raising(seam, monkeypatch):
    monkeypatch.delattr(seam, "register_search_pattern")

    assert OcrExtension().register_search_pattern() is False


def test_a_real_server_with_ocr_on_finds_sidecar_text(tmp_path):
    """The package's own entry point, OCR on, a client over HTTP."""
    _seam()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Notiz ohne den Begriff\n", encoding="utf-8")
    (vault / "scan.pdf.ocr.txt").write_text(SIDECAR, encoding="utf-8")

    with live_package(tmp_path, vault, {"VAULT_OCR_ENABLED": "1"}) as (base, _log):
        _names, (default, notes) = session(base, [
            ("vault_search", {"query": "Zaehlerstand"}),
            ("vault_search", {"query": "Zaehlerstand", "file_pattern": "*.md"}),
        ])

    assert [h["path"] for h in default["results"]] == ["scan.pdf.ocr.txt"], default
    assert notes["results"] == [], notes
