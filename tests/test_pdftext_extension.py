"""PdfTextExtension: a PDF's text layer through the host's vault_read.

Every PDF used here is a real one built at test time, not a stub: one with a text layer,
one without (a scan stand-in), one encrypted with a user password, one with an owner
password only (opens with an empty password), and one whose header is null bytes, like a
damaged file found in production.

The first test is the negative control: without the extension the host cannot read the
PDF at all.
"""

import asyncio
import io
import json
import sys

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("obsidian_vault_mcp.content_extractors")

from pypdf import PdfReader, PdfWriter

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp import content_extractors

from obsidian_vault_mcp_ext.ocr import OcrExtension
from obsidian_vault_mcp_ext.ocr import _config as ocr_config
from obsidian_vault_mcp_ext.ocr import extractor as ocr_extractor
from obsidian_vault_mcp_ext.pdftext import PdfTextExtension

CANARY = "Vertragslaufzeit 36 Monate"
OCR_MARKER = "STUB-OCR-TEXT"


def pdf_with_text(text: str) -> bytes:
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
    xref = sum(len(part) for part in parts)
    parts.append(b"xref\n0 6\n0000000000 65535 f \n")
    parts.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets)
    parts.append(b"trailer\n<< /Size 6 /Root 1 0 R >>\n")
    parts.append(f"startxref\n{xref}\n%%EOF\n".encode("ascii"))
    return b"".join(parts)


def pdf_without_text() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def encrypted(source: bytes, user_password: str, owner_password: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(source)))
    writer.encrypt(user_password=user_password, owner_password=owner_password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    monkeypatch.setattr(host_config, "VAULT_AUDIT_LOG_PATH", "")
    return v


@pytest.fixture(autouse=True)
def clean_registry():
    content_extractors._content_extractors.clear()
    ocr_extractor.clear_cache()
    yield
    content_extractors._content_extractors.clear()
    ocr_extractor.clear_cache()


@pytest.fixture
def ocr_stub(tmp_path, monkeypatch):
    runs = tmp_path / "ocr-runs.log"
    script = tmp_path / "fake_ocr.py"
    script.write_text(
        "import sys\n"
        f"open({str(runs)!r}, 'a', encoding='utf-8').write(sys.argv[-1] + '\\n')\n"
        f"print({OCR_MARKER!r})\n",
        encoding="utf-8",
    )
    python = sys.executable.replace("\\", "/")
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_SIDECAR_ENABLED", False)
    monkeypatch.setattr(ocr_config, "OCR_PDF_CMD", f'"{python}" "{script.as_posix()}" {{path}}')
    return lambda: runs.read_text(encoding="utf-8").splitlines() if runs.exists() else []


def call(name, arguments):
    from obsidian_vault_mcp import server

    result = asyncio.run(server.mcp.call_tool(name, arguments))
    if isinstance(result, tuple):
        result = result[0]
    return json.loads("".join(getattr(block, "text", "") for block in result))


def test_without_the_extension_the_host_cannot_read_a_pdf(vault):
    (vault / "vertrag.pdf").write_bytes(pdf_with_text(CANARY))

    assert "error" in call("vault_read", {"path": "vertrag.pdf"})


def test_a_text_pdf_reads_as_its_text(vault):
    (vault / "vertrag.pdf").write_bytes(pdf_with_text(CANARY))
    PdfTextExtension().register()

    result = call("vault_read", {"path": "vertrag.pdf"})

    assert CANARY in result["content"], result
    assert result["metadata"]["extracted"] is True


def test_a_text_pdf_never_reaches_ocr(vault, ocr_stub):
    (vault / "vertrag.pdf").write_bytes(pdf_with_text(CANARY))
    PdfTextExtension().register()
    OcrExtension().register()

    assert CANARY in call("vault_read", {"path": "vertrag.pdf"})["content"]
    assert ocr_stub() == []


def test_a_scan_falls_through_to_ocr(vault, ocr_stub):
    (vault / "scan.pdf").write_bytes(pdf_without_text())
    PdfTextExtension().register()
    OcrExtension().register()

    assert call("vault_read", {"path": "scan.pdf"})["content"] == OCR_MARKER
    assert len(ocr_stub()) == 1


def test_a_pdf_locked_with_a_user_password_is_declined(vault):
    (vault / "geheim.pdf").write_bytes(encrypted(pdf_with_text(CANARY), "pw", "owner"))
    PdfTextExtension().register()

    result = call("vault_read", {"path": "geheim.pdf"})

    assert "error" in result and CANARY not in json.dumps(result), result


def test_a_pdf_with_only_an_owner_password_is_read(vault):
    (vault / "offen.pdf").write_bytes(encrypted(pdf_with_text(CANARY), "", "owner"))
    PdfTextExtension().register()

    assert CANARY in call("vault_read", {"path": "offen.pdf"})["content"]


def test_a_damaged_pdf_is_declined_not_a_crash(vault):
    # Null bytes alone are valid UTF-8 and would be served as text by the host without
    # asking any extractor; the trailing bytes make it a binary the extractor must handle.
    (vault / "kaputt.pdf").write_bytes(b"\x00" * 64 + b"\xff\xfe not a pdf %%EOF\n")
    PdfTextExtension().register()

    result = call("vault_read", {"path": "kaputt.pdf"})

    assert "error" in result, result


def test_write_back_tools_still_refuse_a_text_pdf(vault):
    """The host keeps extracted text out of every tool that writes back."""
    original = pdf_with_text(CANARY)
    (vault / "vertrag.pdf").write_bytes(original)
    PdfTextExtension().register()
    assert CANARY in call("vault_read", {"path": "vertrag.pdf"})["content"]

    edit = call("vault_edit", {"path": "vertrag.pdf", "edits": [{"old_text": CANARY, "new_text": "X"}]})
    append = call("vault_append", {"path": "vertrag.pdf", "content": "angehaengt"})

    assert "error" in json.dumps(edit) and "error" in json.dumps(append)
    assert (vault / "vertrag.pdf").read_bytes() == original


def test_other_binaries_are_left_to_other_extractors(vault):
    (vault / "bild.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    PdfTextExtension().register()

    assert "error" in call("vault_read", {"path": "bild.png"})
