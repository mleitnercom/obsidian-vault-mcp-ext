# ImportExtension — Security and Design

Bring binary files (images, PDFs) into the vault by downloading an `http(s)` URL or by copying
a local allowlisted file. Loaded through the host's `serve(extensions=...)` seam (#57); no host
fork, no third-party dependency (stdlib only).

Tools:

- `vault_import_url(path, url, media_type, overwrite=False, create_dirs=True, expected_sha256=None)`
- `vault_import_file(path, source_path, media_type, overwrite=False, create_dirs=True, expected_sha256=None)`

This document is deliberately security-heavy: URL import is a Server-Side Request Forgery
(SSRF) surface, and the whole reason this extension is defensible is *how* it is constrained.
If you change `imports/_fetch.py`, read this first.

---

## 1. Threat model

**Asset under protection.** Not the vault contents (the caller is already authenticated and may
write to the vault). The asset is **the vault host and the internal network it sits on**: cloud
metadata endpoints (`169.254.169.254`), loopback admin services, RFC1918 hosts, and any other
resource the server can reach but an external client cannot.

**Trust boundary.** The server is reachable through a Cloudflare Tunnel and protected by OAuth
2.0 + bearer auth. URL import is dangerous *despite* authentication, because it converts an
authenticated *content* request into an **outbound network action originating inside the
perimeter**. An attacker who obtains (or is granted) a token, or any confused-deputy path that
reaches the tool, can otherwise aim the server's network position at internal targets.

**Attacker capabilities assumed.**
- Chooses `url` freely, and through redirects, every subsequent URL.
- Controls authoritative DNS for a domain they own (enables DNS rebinding).
- Can run an HTTP endpoint that returns arbitrary status codes, headers, and `Location`.
- Cannot break TLS or forge certificates for hosts they do not control.

**In scope (must be prevented):** reaching non-public IPs; reaching them via rebinding or
redirects; scanning internal ports; non-HTTP protocol smuggling; resource exhaustion (huge
bodies, redirect loops); writing arbitrary content types or to arbitrary vault paths.

**Out of scope / accepted (see §8):** an operator who deliberately sets
`VAULT_IMPORT_URL_ALLOW_PRIVATE=true`; the authenticated caller reading back what they imported
into their own vault (no privilege gain); a target that is public but malicious (the caller
chose to import it).

---

## 2. Attack vectors and controls

| # | Vector | Without hardening | Control in this extension |
|---|---|---|---|
| 1 | **DNS rebinding / TOCTOU** — DNS returns a public IP to the validator, a private IP to the connector | Reaches `169.254.169.254` / loopback while the check sees a public IP | Resolve **once**, **pin** the connection to the validated IP (§4) |
| 2 | **Redirect to internal** — public URL `30x`-redirects to `http://169.254.169.254/...` | Auto-followed; only the first URL was validated | No auto-follow; **every hop re-validated and re-pinned** (§5) |
| 3 | **Direct private target** — `url` is a literal private/loopback/link-local/metadata address | Reaches it | Public-IP-only check (`is_global`), opt-in override only (§6) |
| 4 | **IPv4-mapped IPv6 smuggling** — `::ffff:169.254.169.254` | Some checks only inspect the v6 form | Mapped address is unwrapped before the check (§6) |
| 5 | **Non-HTTP scheme** — `file://`, `gopher://`, `ftp://` | Local file read / protocol abuse | Scheme allowlist: `http`, `https` only |
| 6 | **Internal port scan** — `http://internal:6379/` etc. | Probes arbitrary services | Port allowlist (`80,443` by default) |
| 7 | **Resource exhaustion (body)** — multi-GB response | Memory/disk blow-up | Hard byte cap enforced on the actual read, not on a trusted `Content-Length` |
| 8 | **Resource exhaustion (redirects)** — infinite redirect loop | Hang | Hop budget (`VAULT_IMPORT_URL_MAX_REDIRECTS`) |
| 9 | **Content-type spoof** — server returns HTML for an `image/png` request | Wrong/hostile content written | `Content-Type` must match the requested `media_type`; extension must match the allowlist |
| 10 | **Target path traversal** — `path = "../../etc/x.png"` | Write outside the vault | `resolve_vault_path` confines the target to the vault |
| 11 | **Local-file traversal** (`vault_import_file`) — `source_path` outside intended dirs | Read arbitrary host files | Explicit root allowlist; resolved source must sit inside an allowlisted root; off until configured |

---

## 3. Why a naive guard is not enough

The tempting implementation — "parse the URL, `getaddrinfo` the host, reject private IPs, then
hand the original URL string to `urllib.urlopen`" — is the exact shape that fails. It leaves
**two** holes:

- The validator and `urlopen` each resolve the hostname **independently**. Between the two
  lookups the attacker's DNS can change the answer (vector 1).
- `urlopen` **auto-follows redirects**, and only the first URL was ever validated (vector 2).

Both are closed below. The controls are isolated behind small seams in `imports/_fetch.py`
(`_resolve_and_pin`, `_open_connection`, `_validate_url`) so they can be unit-tested directly.

---

## 4. IP pinning (closes rebinding / TOCTOU)

`fetch_url` resolves each hop's hostname **exactly once** via `_resolve_and_pin`, validates
**every** address the resolver returns (a single private record rejects the whole import), and
keeps the chosen IP. The connection is then opened **to that pinned IP** by a small
`HTTPConnection` / `HTTPSConnection` subclass whose `connect()` calls
`socket.create_connection((pinned_ip, port))` directly — the standard library never gets a
chance to re-resolve the name.

Correctness is preserved because:

- The HTTP `Host` header is still the original hostname (`http.client` derives it from
  `self.host`, which we set to the real name, not the IP).
- For HTTPS, TLS uses `server_hostname=<original host>`, so **SNI and certificate verification
  still validate against the real name** — pinning the socket does not weaken TLS.

There is no second resolution to exploit. The regression test
`test_rebinding_closed_resolves_once_and_pins` asserts the resolver is called once and the
connection target equals the validated IP.

---

## 5. Redirect handling (closes redirect-to-internal)

Auto-redirects are disabled. On a `301/302/303/307/308`, `fetch_url` reads the `Location`,
resolves it against the current URL, and loops — which means the **next iteration re-runs the
full validation**: scheme, hostname, port, single-resolution, and IP pinning. A redirect to
`http://169.254.169.254/...` is rejected at the resolve-and-pin step of the new hop. The number
of hops is bounded by `VAULT_IMPORT_URL_MAX_REDIRECTS`; exceeding it raises rather than loops.

Regression tests: `test_redirect_to_metadata_rejected` (302 to the metadata IP is refused) and
`test_redirect_budget_enforced` (a redirect loop terminates).

---

## 6. Public-IP-only classification

`_ip_is_public` is a **positive allowlist**: only globally-routable unicast addresses pass
(`ipaddress.is_global` and not multicast). This is stricter than enumerating bad ranges —
anything not provably public is denied, so future reserved ranges are handled without a code
change. IPv4-mapped IPv6 addresses are unwrapped first, so `::ffff:169.254.169.254` is judged on
its embedded IPv4 (link-local) and rejected. The only way to allow a non-public target is the
explicit `VAULT_IMPORT_URL_ALLOW_PRIVATE=true` opt-in.

Covered by `test_public_ips_pass` / `test_non_public_ips_rejected`.

---

## 7. Defense in depth and secure-by-default

- **Scheme allowlist:** `http`, `https` only.
- **Port allowlist:** `80,443` by default (`VAULT_IMPORT_URL_ALLOWED_PORTS`).
- **Size cap:** enforced on the bytes actually read (`VAULT_IMPORT_MAX_BYTES`), never trusting
  `Content-Length`.
- **Media-type + extension allowlist:** the `media_type` must be known and the target file
  extension must match it; the response `Content-Type` must match the requested `media_type`.
- **Optional checksum:** `expected_sha256` is verified before the file is written.
- **Atomic write, confined target:** bytes are written to a temp file and `os.replace`d into a
  path that `resolve_vault_path` has confined to the vault.
- **Off by default:** `vault_import_url` returns a capability error until
  `VAULT_IMPORT_URL_ENABLED=true`; `vault_import_file` until `VAULT_IMPORT_FILE_ALLOWED_ROOTS`
  is set.

---

## 8. Residual risks and explicit non-goals

- **`VAULT_IMPORT_URL_ALLOW_PRIVATE=true` disables vectors 1–4.** It exists for trusted
  single-user deployments that intentionally import from internal URLs. Treat it as "I accept
  SSRF on this box." Leave it unset on anything exposed.
- **Public-but-malicious targets are not judged.** If the caller imports
  `https://evil.example/x.pdf`, they get that PDF. Content scanning is out of scope.
- **No protection against the authenticated caller themselves.** They can already write to the
  vault; importing a file they then read back is not an escalation.
- **DNS TTL races inside a single hop are closed by pinning**, but if `ALLOW_PRIVATE` is on, no
  IP check runs at all — by design.
- **Not a substitute for direct-upload.** Where the client already has the bytes, signed
  direct-upload (`vault_request_upload_url` on the host) fetches nothing and has no SSRF surface;
  prefer it. URL import is for "the file is reachable by HTTP and not easily by the client."

---

## 9. Control → test mapping

| Control | Test(s) (`tests/test_imports_extension.py`) |
|---|---|
| IP pinning / single resolution (vector 1) | `test_rebinding_closed_resolves_once_and_pins` |
| Private direct target (vector 3) | `test_private_resolution_rejected` |
| Public/non-public classification (3,4) | `test_public_ips_pass`, `test_non_public_ips_rejected` |
| Redirect to internal (vector 2) | `test_redirect_to_metadata_rejected` |
| Redirect loop budget (vector 8) | `test_redirect_budget_enforced` |
| Scheme/port allowlist (5,6) | `test_validate_url_rejects_bad_scheme`, `test_validate_url_rejects_disallowed_port` |
| Size cap (vector 7) | `test_size_cap_enforced` |
| Content-type / media-type (vector 9) | `test_url_import_media_type_mismatch`, `test_url_import_rejects_unsupported_media_type` |
| Off-by-default | `test_url_import_disabled_by_default`, `test_file_import_disabled_without_roots` |
| Local-file root allowlist (vector 11) | `test_file_import_writes_and_blocks_outside_root` |
| Vault-confined write (vector 10) | exercised via `resolve_vault_path` in `test_url_import_writes_file` |

---

## 10. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_IMPORT_URL_ENABLED` | `false` | Master switch for `vault_import_url`; fails soft until set. |
| `VAULT_IMPORT_URL_ALLOW_PRIVATE` | `false` | Allow non-public targets. Opt-in only; see §8. |
| `VAULT_IMPORT_URL_TIMEOUT` | `30` | Per-connection timeout (s). |
| `VAULT_IMPORT_URL_MAX_REDIRECTS` | `5` | Max redirect hops; each hop re-validated and re-pinned. |
| `VAULT_IMPORT_URL_ALLOWED_PORTS` | `80,443` | Comma-separated allowed ports. |
| `VAULT_IMPORT_MAX_BYTES` | `10485760` | Hard size cap (bytes) for URL and file import. |
| `VAULT_IMPORT_ALLOWED_MEDIA_TYPES_JSON` | _(images + PDF)_ | JSON `{media_type: [".ext"]}` overriding the default allowlist. |
| `VAULT_IMPORT_FILE_ALLOWED_ROOTS` | _(empty)_ | OS-pathsep list of roots `vault_import_file` may read from. Empty disables it. |

Default media allowlist: `image/png`, `image/jpeg`, `image/webp`, `image/gif`, `image/svg+xml`,
`application/pdf`.

---

## 11. Operational guidance

- **Enable URL import only where you need it**, and keep `ALLOW_PRIVATE` unset on tunnel-exposed
  hosts.
- **Prefer signed direct-upload** when the client already holds the bytes.
- **Watch for unexpected import calls** in the host's tool-usage observability — a spike in
  `vault_import_url` rejections is the signature of someone probing the SSRF guards.
- Note `vault_import_file` reads from the **host's** filesystem, not the caller's; the allowlist
  is the only thing standing between a caller and arbitrary host-file reads, so scope it tightly.

---

## 12. Relationship to upstream

URL import is intentionally **not** proposed for the host core. The upstream maintainer prefers
signed direct-upload (client sends the bytes; the server fetches nothing) and treats URL import
as an SSRF surface to keep out of core, with private/loopback/link-local guards as a hard
requirement rather than an opt-in. Running it as an extension respects that boundary while
keeping the feature for operators who want it. The hardened fetcher here is self-contained, so
if upstream ever decides to accept URL import, `imports/_fetch.py` plus this threat model can be
lifted into a PR as-is — the extension is the proving ground, not a fork divergence.
