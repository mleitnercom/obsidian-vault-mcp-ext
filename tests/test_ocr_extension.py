"""OcrExtension: the real consumer of the host's content-extractor seam.

What has to hold, in the order it is proven:

1. Through the host's registered read tools, a screenshot or scanned PDF comes back as
   OCR text. The real-OCR test renders an actual page with poppler and reads it with
   tesseract; a hand-made image can make OCR abort and turn every later assertion into
   a pass for the wrong reason.
2. With that proven, the host's write-back tools still refuse the file and leave its
   bytes as they were.
3. The command runs without a shell: a hostile file name stays one argument.

Tests that need the seam, tesseract or pdftoppm skip where those are missing, which a
green run must not hide. VAULT_TEST_REQUIRE_TOOLS=1 turns each such skip into a failure;
the server run sets it.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys

import pytest

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext.ocr import OcrExtension
from obsidian_vault_mcp_ext.ocr import _config as ocr_config
from obsidian_vault_mcp_ext.ocr import extractor

REQUIRE = os.environ.get("VAULT_TEST_REQUIRE_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}
CANARY = "Vertragslaufzeit 36 Monate"
MARKER = "STUB-OCR-TEXT"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))


def _missing(what: str):
    if REQUIRE:
        pytest.fail(f"{what} is missing under VAULT_TEST_REQUIRE_TOOLS=1; this run proves nothing about OCR")
    pytest.skip(f"{what} missing; unproven here, proven on the server run")


def _content_extractors():
    try:
        from obsidian_vault_mcp import content_extractors
    except ImportError:
        _missing("the host's content-extractor seam")
    return content_extractors


def call_tool(name: str, arguments: dict) -> dict:
    """Through the host's FastMCP registration, as a client reaches the tool."""
    from obsidian_vault_mcp import server

    result = asyncio.run(server.mcp.call_tool(name, arguments))
    if isinstance(result, tuple):
        result = result[0]
    return json.loads("".join(getattr(block, "text", "") for block in result))


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    return v


@pytest.fixture
def seam():
    module = _content_extractors()
    module._content_extractors.clear()
    extractor.clear_cache()
    yield module
    module._content_extractors.clear()
    extractor.clear_cache()


@pytest.fixture
def stub_ocr(tmp_path, monkeypatch):
    """An OCR command that records its argv and prints a marker. Portable, no tesseract."""
    log = tmp_path / "argv.log"
    script = tmp_path / "fake_ocr.py"
    script.write_text(
        "import json, sys\n"
        f"open({str(log)!r}, 'a', encoding='utf-8').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print({MARKER!r})\n",
        encoding="utf-8",
    )
    python = sys.executable.replace("\\", "/")
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_IMAGE_CMD", f'"{python}" "{script.as_posix()}" {{path}}')
    monkeypatch.setattr(ocr_config, "OCR_PDF_CMD", f'"{python}" "{script.as_posix()}" {{path}}')

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return calls


# --- registration -----------------------------------------------------------------------

def test_disabled_registers_nothing(seam, monkeypatch):
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", False)

    assert OcrExtension().register() is False
    assert seam._content_extractors == []


def test_enabled_registers_once(seam, stub_ocr):
    ext = OcrExtension()
    ext.before_indexes_start(None)
    ext.before_indexes_start(None)

    assert seam._content_extractors == [extractor.extract]


def test_host_without_the_seam_stays_off(monkeypatch):
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setitem(sys.modules, "obsidian_vault_mcp.content_extractors", None)

    assert OcrExtension().register() is False


# --- through the host's tools, with a stub command --------------------------------------

def test_vault_read_returns_ocr_text(vault, seam, stub_ocr):
    (vault / "screenshot.png").write_bytes(PNG_BYTES)
    OcrExtension().register()

    result = call_tool("vault_read", {"path": "screenshot.png"})

    assert result.get("content") == MARKER, result


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("vault_edit", {"path": "screenshot.png", "edits": [{"old_text": MARKER, "new_text": "X"}]}),
        ("vault_append", {"path": "screenshot.png", "content": "angehaengt"}),
        ("vault_batch_frontmatter_update", {"updates": [{"path": "screenshot.png", "fields": {"status": "done"}}]}),
        ("vault_write", {"path": "screenshot.png", "content": "---\nstatus: done\n---\n", "merge_frontmatter": True}),
    ],
)
def test_write_back_tools_leave_the_image_untouched(vault, seam, stub_ocr, name, arguments):
    target = vault / "screenshot.png"
    target.write_bytes(PNG_BYTES)
    OcrExtension().register()
    # The hazard is live: OCR text really is available for this file.
    assert call_tool("vault_read", {"path": "screenshot.png"}).get("content") == MARKER
    before = len(stub_ocr())

    result = call_tool(name, arguments)

    assert target.read_bytes() == PNG_BYTES, f"{name} replaced the image on disk"
    assert "error" in json.dumps(result), result
    assert len(stub_ocr()) == before, f"{name} ran OCR"


def test_file_name_stays_one_argument(vault, seam, stub_ocr):
    name = "a b; rm -rf x $(id) --help.png"
    (vault / name).write_bytes(PNG_BYTES)
    OcrExtension().register()

    assert call_tool("vault_read", {"path": name}).get("content") == MARKER
    assert stub_ocr() == [[str((vault / name).resolve())]]


def test_unchanged_file_is_not_ocrd_twice(vault, seam, stub_ocr):
    (vault / "screenshot.png").write_bytes(PNG_BYTES)
    OcrExtension().register()

    call_tool("vault_read", {"path": "screenshot.png"})
    call_tool("vault_batch_read", {"paths": ["screenshot.png"]})

    assert len(stub_ocr()) == 1


def test_failing_command_declines_and_the_read_errors(vault, seam, monkeypatch):
    python = sys.executable.replace("\\", "/")
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_IMAGE_CMD", f'"{python}" -c "import sys; sys.exit(3)" {{path}}')
    (vault / "screenshot.png").write_bytes(PNG_BYTES)
    OcrExtension().register()

    result = call_tool("vault_read", {"path": "screenshot.png"})

    assert "error" in result and "content" not in result


def test_unsupported_type_is_declined(vault, seam, stub_ocr):
    (vault / "archiv.zip").write_bytes(b"PK\x03\x04\xff\xfe")
    OcrExtension().register()

    assert "error" in call_tool("vault_read", {"path": "archiv.zip"})
    assert stub_ocr() == []


def test_template_without_path_placeholder_is_rejected():
    with pytest.raises(ValueError, match="path"):
        extractor.build_argv("tesseract - -l eng", None)


# --- real OCR -----------------------------------------------------------------------------

def _build_pdf(text: str) -> bytes:
    """A one-page PDF with real text, rendered by poppler below."""
    stream = f"BT\n/F1 24 Tf\n20 100 Td\n({text}) Tj\nET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    parts = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref_offset = sum(len(part) for part in parts)
    parts.append(b"xref\n0 6\n0000000000 65535 f \n")
    parts.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets)
    parts.append(b"trailer\n<< /Size 6 /Root 1 0 R >>\n")
    parts.append(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return b"".join(parts)


@pytest.fixture
def real_tools():
    for tool in ("tesseract", "pdftoppm"):
        if shutil.which(tool) is None:
            _missing(tool)


def test_real_ocr_reads_a_rendered_page_and_writes_never_touch_it(vault, tmp_path, seam, real_tools, monkeypatch):
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(_build_pdf(CANARY))
    subprocess.run(
        ["pdftoppm", "-r", "300", "-png", "-singlefile", str(pdf), str(tmp_path / "page")],
        check=True,
    )
    image = vault / "screenshot.png"
    image.write_bytes((tmp_path / "page.png").read_bytes())
    original = image.read_bytes()

    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_IMAGE_CMD", "tesseract {path} - -l eng")
    OcrExtension().register()

    text = call_tool("vault_read", {"path": "screenshot.png"}).get("content", "")
    assert "36" in text and "Monate" in text, f"real OCR did not read the page: {text!r}"

    result = call_tool("vault_append", {"path": "screenshot.png", "content": "angehaengt"})
    assert image.read_bytes() == original, "vault_append replaced a real screenshot"
    assert "error" in json.dumps(result)
