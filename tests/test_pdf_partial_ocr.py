"""OCR for the pages of a PDF that have no text layer, when others do (fork v0.14.0/v0.15.1).

A contract scan with an e-signature trail appended has text on the trail pages only, so
PdfTextExtension returned just those and the contract stayed unreadable. With
VAULT_OCR_PDF_PARTIAL on, the pages without text go to the OCR command and come back
labelled by page number.

Reads go through the host's registered vault_read with both extractors registered, as in
production. The OCR commands are real child processes: one follows the label contract,
others break it the ways a real command can, and the last tests run the production
wrapper (docs/deploy/pdf-ocr-wrapper.sh) with pdftoppm and tesseract where they exist.
"""

import asyncio
import io
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

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

from test_pdftext_extension import pdf_with_text

REQUIRE = os.environ.get("VAULT_TEST_REQUIRE_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}
WRAPPER = Path(__file__).resolve().parents[1] / "docs" / "deploy" / "pdf-ocr-wrapper.sh"


def mixed_pdf(text_pages: dict[int, str], total: int) -> bytes:
    writer = PdfWriter()
    for number in range(1, total + 1):
        if number in text_pages:
            writer.append(PdfReader(io.BytesIO(pdf_with_text(text_pages[number]))))
        else:
            writer.add_blank_page(width=400, height=200)
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
def registered(monkeypatch):
    content_extractors._content_extractors.clear()
    ocr_extractor.clear_cache()
    monkeypatch.setattr(ocr_config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr_config, "OCR_SIDECAR_ENABLED", True)
    PdfTextExtension().register()
    OcrExtension().register()
    yield
    content_extractors._content_extractors.clear()
    ocr_extractor.clear_cache()


FOLLOWS = (
    "pages = [p for p in os.environ.get('VAULT_PDF_OCR_PAGES', '').split(',') if p]\n"
    "if not pages:\n"
    "    sys.stdout.write('OCR page ALL\\n')\n"
    "for p in pages:\n"
    "    sys.stdout.write(f'\\fPAGE {p}\\nOCR page {p}\\n')\n"
)


@pytest.fixture
def ocr(tmp_path, monkeypatch):
    def configure(body: str = FOLLOWS, partial: bool = True):
        script = tmp_path / "ocr_stub.py"
        script.write_text("import os, sys\n" + body, encoding="utf-8")
        python = sys.executable.replace("\\", "/")
        monkeypatch.setattr(ocr_config, "OCR_PDF_CMD", f'"{python}" "{script.as_posix()}" {{path}}')
        monkeypatch.setattr(ocr_config, "OCR_PDF_PARTIAL", partial)

    return configure


def read(path: str) -> str:
    from obsidian_vault_mcp import server

    result = asyncio.run(server.mcp.call_tool("vault_read", {"path": path}))
    if isinstance(result, tuple):
        result = result[0]
    return json.loads("".join(getattr(block, "text", "") for block in result))["content"]


def test_off_by_default_a_mixed_pdf_reads_its_text_layer(vault, ocr):
    ocr(body="raise SystemExit('OCR ran')\n", partial=False)
    (vault / "vertrag.pdf").write_bytes(mixed_pdf({3: "Audit trail"}, total=3))

    assert read("vertrag.pdf") == "Audit trail"
    assert not (vault / "vertrag.pdf.ocr.txt").exists()


def test_only_the_pages_without_text_are_ocrd_and_merged_in_order(vault, ocr):
    ocr()
    (vault / "vertrag.pdf").write_bytes(mixed_pdf({1: "Deckblatt", 4: "Audit trail"}, total=4))

    assert read("vertrag.pdf").split("\n\n") == ["Deckblatt", "OCR page 2", "OCR page 3", "Audit trail"]
    assert (vault / "vertrag.pdf.ocr.txt").exists()


def test_the_second_read_runs_no_ocr(vault, ocr):
    ocr()
    (vault / "vertrag.pdf").write_bytes(mixed_pdf({1: "Deckblatt"}, total=2))
    first = read("vertrag.pdf")
    assert first.split("\n\n") == ["Deckblatt", "OCR page 2"]  # else there is nothing to cache
    ocr_extractor.clear_cache()  # the sidecar, not memory, must carry it

    ocr(body="raise SystemExit('OCR ran a second time')\n")
    assert read("vertrag.pdf") == first


def test_one_unlabelled_stream_is_not_put_on_the_missing_page(vault, ocr):
    """What a wrapper that ignores the page list prints (tesseract 5.3 has no separator)."""
    ocr(body="sys.stdout.write('Deckblatt\\nVertragstext\\nAudit trail\\n')\n")
    (vault / "vertrag.pdf").write_bytes(mixed_pdf({1: "Deckblatt", 3: "Audit trail"}, total=3))

    assert read("vertrag.pdf") == "Deckblatt\n\nAudit trail"
    assert not (vault / "vertrag.pdf.ocr.txt").exists()


@pytest.mark.parametrize("output", [
    "\\fPAGE 1\\nnot requested\\n",
    "\\fPAGE 2\\nfirst\\n\\fPAGE 2\\nagain\\n",
    "\\fPAGE 2\\nok\\n\\fno label\\n",
])
def test_a_block_that_breaks_the_labels_rejects_the_output(vault, ocr, output):
    ocr(body=f"sys.stdout.write('{output}')\n")
    (vault / "vertrag.pdf").write_bytes(mixed_pdf({1: "Deckblatt"}, total=3))

    assert read("vertrag.pdf") == "Deckblatt"


def test_blank_pages_are_a_result_and_are_cached(vault, ocr):
    ocr(body="sys.stdout.write('\\fPAGE 2\\n\\fPAGE 3\\n')\n")
    (vault / "folien.pdf").write_bytes(mixed_pdf({1: "Titel"}, total=3))

    assert read("folien.pdf") == "Titel"
    assert (vault / "folien.pdf.ocr.txt").exists()


def test_a_failed_page_is_answered_but_not_cached(vault, ocr, tmp_path):
    runs = tmp_path / "runs.txt"
    ocr(body=(
        f"open({str(runs)!r}, 'a').write('x')\n"
        "sys.stdout.write('\\fPAGE 2 FAILED\\n\\fPAGE 3\\nOCR page 3\\n')\n"
    ))
    (vault / "vertrag.pdf").write_bytes(mixed_pdf({1: "Deckblatt"}, total=3))

    first = read("vertrag.pdf")
    read("vertrag.pdf")

    assert first.split("\n\n") == ["Deckblatt", "OCR page 3"]
    assert not (vault / "vertrag.pdf.ocr.txt").exists()
    assert runs.read_text() == "xx"


def test_a_failing_command_leaves_the_text_layer(vault, ocr):
    ocr(body="raise SystemExit(3)\n")
    (vault / "vertrag.pdf").write_bytes(mixed_pdf({1: "Deckblatt"}, total=2))

    assert read("vertrag.pdf") == "Deckblatt"


def test_an_inherited_page_list_does_not_reach_a_whole_document_run(vault, ocr, monkeypatch):
    ocr()
    monkeypatch.setenv("VAULT_PDF_OCR_PAGES", "1")
    (vault / "scan.pdf").write_bytes(mixed_pdf({}, total=2))

    assert read("scan.pdf") == "OCR page ALL"


# --- the production wrapper, with real rendering and OCR ----------------------------------

def _png_to_image_pdf_page(png: bytes) -> bytes:
    """One page whose only content is the PNG as an image: a scan, no text layer."""
    pos, idat, width, space, colors = 8, b"", 0, "/DeviceGray", 1
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", data[:10])
            assert depth == 8 and color in (0, 2)
            space, colors = ("/DeviceGray", 1) if color == 0 else ("/DeviceRGB", 3)
        elif kind == b"IDAT":
            idat += data
        pos += 12 + length
    image = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace {space} "
        f"/BitsPerComponent 8 /Filter /FlateDecode /DecodeParms << /Predictor 15 /Colors {colors} "
        f"/BitsPerComponent 8 /Columns {width} >> /Length {len(idat)} >>\nstream\n"
    ).encode() + idat + b"\nendstream"
    draw = b"q 400 0 0 200 0 0 cm /Im1 Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>",
        image,
        b"<< /Length " + str(len(draw)).encode() + b" >>\nstream\n" + draw + b"\nendstream",
    ]
    parts, offsets = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"], []
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(map(len, parts)))
        parts.append(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = sum(map(len, parts))
    parts.append(b"xref\n0 6\n0000000000 65535 f \n")
    parts += [f"{o:010d} 00000 n \n".encode() for o in offsets]
    parts.append(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return b"".join(parts)


@pytest.fixture
def wrapper(monkeypatch):
    missing = [tool for tool in ("pdftoppm", "pdfinfo", "tesseract", "bash") if shutil.which(tool) is None]
    if missing:
        if REQUIRE:
            pytest.fail(f"{missing} missing under VAULT_TEST_REQUIRE_TOOLS=1")
        pytest.skip(f"needs {missing}; unproven here, proven on the server run")
    monkeypatch.setenv("VAULT_PDF_OCR_MAX_PAGES", "12")
    monkeypatch.setenv("VAULT_PDF_OCR_LANGUAGES", "eng")
    monkeypatch.setattr(ocr_config, "OCR_PDF_CMD", f"bash {WRAPPER.as_posix()} {{path}}")
    monkeypatch.setattr(ocr_config, "OCR_PDF_PARTIAL", True)


def _image_page(tmp_path, pdf_bytes: bytes, name: str):
    source = tmp_path / f"{name}.pdf"
    source.write_bytes(pdf_bytes)
    subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", "-singlefile", str(source), str(tmp_path / name)], check=True)
    return PdfReader(io.BytesIO(_png_to_image_pdf_page((tmp_path / f"{name}.png").read_bytes())))


def test_the_production_wrapper_reads_only_the_scanned_page(vault, tmp_path, wrapper):
    scanned = _image_page(tmp_path, pdf_with_text("Laufzeit 36"), "scan")
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(pdf_with_text("Deckblatt"))))
    writer.append(scanned)
    writer.append(PdfReader(io.BytesIO(pdf_with_text("Audit trail"))))
    out = io.BytesIO()
    writer.write(out)
    (vault / "vertrag.pdf").write_bytes(out.getvalue())

    parts = read("vertrag.pdf").split("\n\n")

    assert len(parts) == 3 and parts[0] == "Deckblatt" and parts[2] == "Audit trail", parts
    assert "Laufzeit 36" in parts[1], parts


def test_the_production_wrapper_reports_a_white_page_as_blank(vault, tmp_path, wrapper):
    blank = PdfWriter()
    blank.add_blank_page(width=400, height=200)
    out = io.BytesIO()
    blank.write(out)
    white = _image_page(tmp_path, out.getvalue(), "white")
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(pdf_with_text("Titel"))))
    writer.append(white)
    out = io.BytesIO()
    writer.write(out)
    (vault / "folien.pdf").write_bytes(out.getvalue())

    assert read("folien.pdf") == "Titel"
    assert (vault / "folien.pdf.ocr.txt").exists()
