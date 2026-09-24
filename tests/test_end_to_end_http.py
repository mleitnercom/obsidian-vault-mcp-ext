"""The package as it ships: ``python -m obsidian_vault_mcp_ext`` in its own process, the
default extension list loaded, and a client with the bearer token calling tools over MCP
streamable HTTP.

This is the path production would take after a switch from the fork, so it checks the
things only a real process shows: that every extension registers next to the host's tools
without a name clash, that the fork-era tool names answer, that the create-note policy is
read from the environment, and that a PDF with a text layer is readable through the host's
vault_read.

The child's environment is built from scratch, never inherited, so a developer's own
settings cannot reach into the test.
"""

import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager

import pytest

pytest.importorskip("obsidian_vault_mcp.content_extractors")

TOKEN = "ext-e2e-token"
_PASSTHROUGH = ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LD_LIBRARY_PATH")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def live_package(tmp_path, vault, extra_env):
    port = _free_port()
    home = tmp_path / "home"
    home.mkdir()
    env = {name: os.environ[name] for name in _PASSTHROUGH if name in os.environ}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "PYTHONUNBUFFERED": "1",
        "VAULT_PATH": str(vault), "VAULT_MCP_TOKEN": TOKEN, "VAULT_MCP_HOST": "127.0.0.1",
        "VAULT_MCP_PORT": str(port), "VAULT_MCP_PATH": "/", "VAULT_MCP_PUBLIC_URL": f"http://127.0.0.1:{port}",
        **extra_env,
    })
    log_path = tmp_path / "server.log"
    with open(log_path, "wb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "obsidian_vault_mcp_ext"], env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 90
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited: {log_path.read_text(errors='replace')[-3000:]}")
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.time() > deadline:
                raise RuntimeError(f"server did not come up: {log_path.read_text(errors='replace')[-3000:]}")
            time.sleep(0.3)
        yield base, log_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)


def session(base, calls):
    """Run several tool calls (and a tools/list) in one MCP session."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        async with streamablehttp_client(f"{base}/", headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                out = []
                for name, args in calls:
                    res = await s.call_tool(name, args)
                    text = "".join(getattr(b, "text", "") for b in res.content)
                    out.append(json.loads(text) if text.strip().startswith("{") else {"error": text})
                return names, out

    return asyncio.run(run())


def text_pdf(text: str) -> bytes:
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
        offsets.append(sum(len(p) for p in parts))
        parts.append(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = sum(len(p) for p in parts)
    parts.append(b"xref\n0 6\n0000000000 65535 f \n")
    parts.extend(f"{o:010d} 00000 n \n".encode("ascii") for o in offsets)
    parts.append(b"trailer\n<< /Size 6 /Root 1 0 R >>\n")
    parts.append(f"startxref\n{xref}\n%%EOF\n".encode("ascii"))
    return b"".join(parts)


def test_the_package_serves_its_tools_over_http(tmp_path):
    pytest.importorskip("pypdf")
    vault = tmp_path / "vault"
    (vault / "tasks").mkdir(parents=True)
    (vault / "note.md").write_bytes("Der Zaehlerstand hier. Der Zaehlerstand dort.\n".encode("utf-8"))
    (vault / "vertrag.pdf").write_bytes(text_pdf("Vertragslaufzeit 36 Monate"))
    note = "---\nid: 2026-09-neu\ntitle: Neu\n---\n\n# Neu\n"
    env = {
        "VAULT_CREATE_NOTE_PATH_PATTERN": r"tasks/\d{4}-\d{2}-[a-z0-9-]+\.md",
        "VAULT_CREATE_NOTE_ID_FIELD": "id",
        "VAULT_AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
    }

    with live_package(tmp_path, vault, env) as (base, _log):
        names, (replace, tree, created, again, pdf) = session(base, [
            ("vault_str_replace", {"path": "note.md", "old_string": "Zaehlerstand", "new_string": "Stand", "replace_all": True}),
            ("vault_tree", {"depth": 1}),
            ("vault_create_note", {"path": "tasks/2026-09-neu.md", "content": note}),
            ("vault_create_note", {"path": "tasks/2026-09-neu.md", "content": note}),
            ("vault_read", {"path": "vertrag.pdf"}),
        ])

    expected = {
        "vault_read", "vault_edit",                       # host
        "vault_str_replace", "vault_patch", "vault_batch_replace", "vault_tree",   # compat
        "vault_create_note", "recurring_materialize", "vault_template_list",
        "vault_semantic_search", "vault_scan_encoding", "vault_import_url",
    }
    assert expected <= set(names), expected - set(names)
    assert len(names) == len(set(names)), "a tool name is registered twice"

    assert replace["occurrences_found"] == 2, replace
    assert "Zaehlerstand" not in (vault / "note.md").read_text(encoding="utf-8")
    assert tree["files"] == ["note.md", "vertrag.pdf"] and [d["name"] for d in tree["dirs"]] == ["tasks"], tree
    assert created.get("created") is True, created
    assert again.get("error_code") == "note_exists", again
    assert "36 Monate" in pdf.get("content", ""), pdf
    assert pdf["metadata"]["extracted"] is True

    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    operations = [r["operation"] for r in records]
    assert "vault_str_replace" in operations and "vault_create_note" in operations, operations
