"""Tests for ImportExtension on the host's extension seam.

The security core (``_fetch``) is tested directly, including the two SSRF bypasses a naive
guard leaves open: DNS rebinding / TOCTOU (closed by single-resolution IP pinning) and
redirect-to-internal (closed by per-hop re-validation). No network is touched: DNS and the
connection layer are substituted.
"""

import ipaddress
import json
import socket

import pytest
from mcp.server.fastmcp import FastMCP

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext import ImportExtension
from obsidian_vault_mcp_ext.imports import _config, _fetch, tools


# --- fakes for the connection layer ---------------------------------------------------

class _FakeHeaders:
    def __init__(self, mapping):
        self._m = {k.lower(): v for k, v in mapping.items()}

    def get(self, key, default=None):
        return self._m.get(key.lower(), default)


class _FakeResponse:
    def __init__(self, status, headers, body=b""):
        self.status = status
        self.headers = _FakeHeaders(headers)
        self._body = body
        self._read = False

    def read(self, n=-1):
        if self._read:
            return b""
        self._read = True
        return self._body


class _FakeConn:
    def __init__(self, response):
        self._response = response

    def putrequest(self, *a, **k):
        pass

    def putheader(self, *a, **k):
        pass

    def endheaders(self, *a, **k):
        pass

    def getresponse(self):
        return self._response

    def close(self):
        pass


def _install_dns(monkeypatch, mapping, counter=None):
    def fake_getaddrinfo(host, port, *a, **k):
        if counter is not None:
            counter.append(host)
        if host not in mapping:
            raise socket.gaierror(f"no fake DNS entry for {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (mapping[host], port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _install_conns(monkeypatch, responses, recorder=None):
    state = {"i": 0}

    def fake_open(scheme, host, pinned_ip, port, timeout):
        if recorder is not None:
            recorder.append({"scheme": scheme, "host": host, "pinned_ip": pinned_ip, "port": port})
        resp = responses[state["i"]]
        state["i"] += 1
        return _FakeConn(resp)

    monkeypatch.setattr(_fetch, "_open_connection", fake_open)


# --- _ip_is_public --------------------------------------------------------------------

@pytest.mark.parametrize("addr", ["8.8.8.8", "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_ips_pass(addr):
    assert _fetch._ip_is_public(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize(
    "addr",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fe80::1", "::ffff:169.254.169.254", "224.0.0.1"],
)
def test_non_public_ips_rejected(addr):
    assert _fetch._ip_is_public(ipaddress.ip_address(addr)) is False


# --- _validate_url --------------------------------------------------------------------

@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://h/", "://nohost"])
def test_validate_url_rejects_bad_scheme(url):
    with pytest.raises(_fetch.ImportSecurityError):
        _fetch._validate_url(url, {80, 443})


def test_validate_url_rejects_disallowed_port():
    with pytest.raises(_fetch.ImportSecurityError):
        _fetch._validate_url("http://example.com:8080/x", {80, 443})


# --- SSRF bypass #1: DNS rebinding / TOCTOU is closed by single-resolution pinning -----

def test_rebinding_closed_resolves_once_and_pins(monkeypatch):
    calls, conns = [], []
    _install_dns(monkeypatch, {"good.example": "93.184.216.34"}, counter=calls)
    _install_conns(monkeypatch, [_FakeResponse(200, {"Content-Type": "image/png"}, b"PNG")], recorder=conns)

    ctype, data = _fetch.fetch_url(
        "http://good.example/a.png",
        allow_private=False, allowed_ports={80, 443}, max_bytes=1_000_000, max_redirects=5, timeout=5,
    )
    assert (ctype, data) == ("image/png", b"PNG")
    # Resolved exactly once, and the connection used that exact validated IP (no re-resolve gap).
    assert calls == ["good.example"]
    assert conns[0]["pinned_ip"] == "93.184.216.34"


def test_private_resolution_rejected(monkeypatch):
    _install_dns(monkeypatch, {"evil.example": "169.254.169.254"})
    with pytest.raises(_fetch.ImportSecurityError):
        _fetch.fetch_url(
            "http://evil.example/x.png",
            allow_private=False, allowed_ports={80, 443}, max_bytes=1_000_000, max_redirects=5, timeout=5,
        )


# --- SSRF bypass #2: redirect to an internal target is re-validated and rejected --------

def test_redirect_to_metadata_rejected(monkeypatch):
    _install_dns(monkeypatch, {"good.example": "93.184.216.34", "169.254.169.254": "169.254.169.254"})
    _install_conns(monkeypatch, [_FakeResponse(302, {"Location": "http://169.254.169.254/latest/meta-data/"})])
    with pytest.raises(_fetch.ImportSecurityError):
        _fetch.fetch_url(
            "http://good.example/a.png",
            allow_private=False, allowed_ports={80, 443}, max_bytes=1_000_000, max_redirects=5, timeout=5,
        )


def test_redirect_budget_enforced(monkeypatch):
    _install_dns(monkeypatch, {"good.example": "93.184.216.34"})
    # Always redirect back to a public host: should exhaust the hop budget, not loop forever.
    responses = [_FakeResponse(302, {"Location": "http://good.example/again"}) for _ in range(10)]
    _install_conns(monkeypatch, responses)
    with pytest.raises(_fetch.ImportSecurityError):
        _fetch.fetch_url(
            "http://good.example/a.png",
            allow_private=False, allowed_ports={80, 443}, max_bytes=1_000_000, max_redirects=2, timeout=5,
        )


def test_size_cap_enforced(monkeypatch):
    _install_dns(monkeypatch, {"good.example": "93.184.216.34"})
    _install_conns(monkeypatch, [_FakeResponse(200, {"Content-Type": "image/png"}, b"x" * 50)])
    with pytest.raises(_fetch.ImportFetchError):
        _fetch.fetch_url(
            "http://good.example/a.png",
            allow_private=False, allowed_ports={80, 443}, max_bytes=10, max_redirects=5, timeout=5,
        )


# --- tool-level: registration, disabled default, end-to-end with a stubbed fetch -------

@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir(parents=True)
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    return v


def test_register_tools(vault):
    mcp = FastMCP("test")
    ImportExtension().register_tools(mcp)
    for name in ("vault_import_url", "vault_import_file"):
        assert mcp._tool_manager.get_tool(name) is not None


def test_url_import_disabled_by_default(vault, monkeypatch):
    monkeypatch.setattr(_config, "URL_IMPORT_ENABLED", False)
    res = json.loads(tools.vault_import_url("a.png", "http://h/x.png", "image/png"))
    assert "disabled" in res["error"]


def test_url_import_writes_file(vault, monkeypatch):
    monkeypatch.setattr(_config, "URL_IMPORT_ENABLED", True)
    monkeypatch.setattr(tools, "fetch_url", lambda url, **k: ("image/png", b"PNGBYTES"))
    res = json.loads(tools.vault_import_url("sub/a.png", "http://h/x.png", "image/png"))
    assert "error" not in res, res
    assert res["created"] is True and res["size"] == 8
    assert (vault / "sub" / "a.png").read_bytes() == b"PNGBYTES"


def test_url_import_media_type_mismatch(vault, monkeypatch):
    monkeypatch.setattr(_config, "URL_IMPORT_ENABLED", True)
    monkeypatch.setattr(tools, "fetch_url", lambda url, **k: ("application/pdf", b"%PDF"))
    res = json.loads(tools.vault_import_url("a.png", "http://h/x.png", "image/png"))
    assert "does not match" in res["error"]
    assert not (vault / "a.png").exists()


def test_url_import_rejects_unsupported_media_type(vault, monkeypatch):
    monkeypatch.setattr(_config, "URL_IMPORT_ENABLED", True)
    res = json.loads(tools.vault_import_url("a.exe", "http://h/x", "application/x-msdownload"))
    assert "Unsupported media_type" in res["error"]


def test_file_import_disabled_without_roots(vault, monkeypatch):
    monkeypatch.setattr(_config, "allowed_file_roots", lambda: [])
    res = json.loads(tools.vault_import_file("a.png", "/tmp/x.png", "image/png"))
    assert "disabled" in res["error"]


def test_file_import_writes_and_blocks_outside_root(vault, tmp_path, monkeypatch):
    src_root = tmp_path / "incoming"
    src_root.mkdir()
    src = src_root / "pic.png"
    src.write_bytes(b"LOCALPNG")
    monkeypatch.setattr(_config, "allowed_file_roots", lambda: [str(src_root)])

    res = json.loads(tools.vault_import_file("a.png", str(src), "image/png"))
    assert "error" not in res, res
    assert (vault / "a.png").read_bytes() == b"LOCALPNG"

    outside = tmp_path / "secret.png"
    outside.write_bytes(b"NOPE")
    res2 = json.loads(tools.vault_import_file("b.png", str(outside), "image/png"))
    assert "outside" in res2["error"]
