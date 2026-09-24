"""OCR sidecars in the fork's format, and the OCR command's scrubbed environment.

Sidecars: the first read writes <file>.ocr.txt; a later read (new process, empty memory
cache) reuses it without running OCR; a changed file invalidates it. The decisive case is
a sidecar written the way the fork writes it, with the header computed independently here,
because that is what decides whether switching to this extension re-runs OCR over ~1,670
existing sidecars in production.

Environment: the server holds VAULT_MCP_TOKEN and OAuth secrets. The stub OCR command dumps
the environment it receives; the secret must be absent while the OCR tuning variables the
production wrapper reads must be present.
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import pytest

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp import write_events

from obsidian_vault_mcp_ext.ocr import OcrExtension
from obsidian_vault_mcp_ext.ocr import _config as ocr_config
from obsidian_vault_mcp_ext.ocr import extractor

pytest.importorskip("obsidian_vault_mcp.content_extractors")

MARKER = "STUB-OCR-TEXT"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", "")
    return v


@pytest.fixture
def seam():
    from obsidian_vault_mcp import content_extractors

    content_extractors._content_extractors.clear()
    extractor.clear_cache()
    yield content_extractors
    content_extractors._content_extractors.clear()
    extractor.clear_cache()


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """OCR stub: counts its runs, records its environment, prints a marker."""
    runs = tmp_path / "runs.log"
    envdump = tmp_path / "env.json"
    script = tmp_path / "fake_ocr.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(runs)!r}, 'a', encoding='utf-8').write(sys.argv[-1] + '\\n')\n"
        f"open({str(envdump)!r}, 'w', encoding='utf-8').write(json.dumps(dict(os.environ)))\n"
        f"print({MARKER!r})\n",
        encoding="utf-8",
    )
    python = sys.executable.replace("\\", "/")
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_SIDECAR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_IMAGE_CMD", f'"{python}" "{script.as_posix()}" {{path}}')

    class Stub:
        def runs(self):
            return runs.read_text(encoding="utf-8").splitlines() if runs.exists() else []

        def env(self):
            return json.loads(envdump.read_text(encoding="utf-8"))

    return Stub()


def read(name: str) -> dict:
    from obsidian_vault_mcp import server

    result = asyncio.run(server.mcp.call_tool("vault_read", {"path": name}))
    if isinstance(result, tuple):
        result = result[0]
    return json.loads("".join(getattr(block, "text", "") for block in result))


def fork_style_header(path) -> str:
    """The fork's sidecar source line, computed here and not with the extension's code."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    mtime_iso = mtime.isoformat().replace("+00:00", "Z")
    digest = hashlib.sha256(path.read_bytes()[: 64 * 1024]).hexdigest()[:16]
    return f"# Source: {mtime_iso}:{digest}"


# --- sidecars ---------------------------------------------------------------------------

def test_first_read_writes_a_sidecar_in_the_fork_format(vault, seam, stub):
    (vault / "bild.png").write_bytes(PNG)
    OcrExtension().register()

    assert read("bild.png")["content"] == MARKER

    side = vault / "bild.png.ocr.txt"
    lines = side.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# OCR generated ")
    assert lines[1] == fork_style_header(vault / "bild.png")
    assert lines[3] == MARKER
    assert stub.runs() and len(stub.runs()) == 1


def test_a_later_process_reuses_the_sidecar_without_running_ocr(vault, seam, stub):
    (vault / "bild.png").write_bytes(PNG)
    OcrExtension().register()
    read("bild.png")
    extractor.clear_cache()  # what a restart does to the memory cache

    assert read("bild.png")["content"] == MARKER
    assert len(stub.runs()) == 1, "OCR ran again although a valid sidecar existed"


def test_a_sidecar_the_fork_wrote_is_reused(vault, seam, stub):
    """Production has ~1,670 of these. If this fails, switching re-runs OCR over all."""
    image = vault / "alt.png"
    image.write_bytes(PNG)
    (vault / "alt.png.ocr.txt").write_text(
        f"# OCR generated 2026-09-02T00:15:00Z\n{fork_style_header(image)}\n\nText vom Fork\n",
        encoding="utf-8",
    )
    OcrExtension().register()

    assert read("alt.png")["content"] == "Text vom Fork"
    assert stub.runs() == []


def test_a_changed_file_invalidates_its_sidecar(vault, seam, stub):
    image = vault / "bild.png"
    image.write_bytes(PNG)
    OcrExtension().register()
    read("bild.png")
    extractor.clear_cache()

    image.write_bytes(PNG + b"geaendert")
    os.utime(image, (image.stat().st_atime, image.stat().st_mtime + 5))
    read("bild.png")

    assert len(stub.runs()) == 2, "a stale sidecar was served"


def test_a_symlinked_sidecar_is_not_trusted(vault, seam, stub, tmp_path):
    image = vault / "bild.png"
    image.write_bytes(PNG)
    outside = tmp_path / "fremd.txt"
    outside.write_text(f"# OCR generated x\n{fork_style_header(image)}\n\nFremder Text\n", encoding="utf-8")
    try:
        os.symlink(outside, vault / "bild.png.ocr.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    OcrExtension().register()

    assert read("bild.png")["content"] == MARKER


def test_the_sidecar_write_is_announced(vault, seam, stub):
    write_events._write_listeners.clear()
    events = []
    write_events.register_write_listener(lambda op, paths: events.append((op, paths)))
    try:
        (vault / "bild.png").write_bytes(PNG)
        OcrExtension().register()
        read("bild.png")
    finally:
        write_events._write_listeners.clear()

    assert events == [("created", ["bild.png.ocr.txt"])]


def test_sidecars_can_be_switched_off(vault, seam, stub, monkeypatch):
    monkeypatch.setattr(ocr_config, "OCR_SIDECAR_ENABLED", False)
    (vault / "bild.png").write_bytes(PNG)
    OcrExtension().register()

    assert read("bild.png")["content"] == MARKER
    assert not (vault / "bild.png.ocr.txt").exists()


# --- scrubbed environment -----------------------------------------------------------------

def test_the_ocr_command_never_sees_server_secrets(vault, seam, stub, monkeypatch):
    monkeypatch.setenv("VAULT_MCP_TOKEN", "bearer-secret-123")
    monkeypatch.setenv("VAULT_OAUTH_CLIENT_SECRET", "oauth-secret-456")
    monkeypatch.setenv("VAULT_UPLOAD_URL_SECRET", "upload-secret-789")
    monkeypatch.setenv("VAULT_PDF_OCR_MAX_PAGES", "12")
    monkeypatch.setenv("VAULT_PDF_OCR_DPI", "200")
    (vault / "bild.png").write_bytes(PNG)
    OcrExtension().register()

    read("bild.png")
    env = stub.env()

    for secret in ("VAULT_MCP_TOKEN", "VAULT_OAUTH_CLIENT_SECRET", "VAULT_UPLOAD_URL_SECRET"):
        assert secret not in env, f"{secret} reached the OCR command"
    assert env.get("VAULT_PDF_OCR_MAX_PAGES") == "12" and env.get("VAULT_PDF_OCR_DPI") == "200"
    assert env.get("VAULT_PDF_PATH") == str((vault / "bild.png").resolve())
