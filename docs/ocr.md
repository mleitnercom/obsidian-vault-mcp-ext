# OcrExtension

OCR text for screenshots and scanned PDFs, through the host's own `vault_read` and
`vault_batch_read`. No new tool: a client reads the file it already knows about.

This is the consumer of the host's read-side content-extractor seam
(`obsidian_vault_mcp.content_extractors.register_content_extractor`, jimprosser#63). It
exists so the seam's callback signature is fixed against real use.

## How it plugs in

`OcrExtension.before_indexes_start` registers one callback:

```python
extract(relative_path: str, path: Path) -> str | None
```

The host calls it only from the read tools, only for a file that is not valid UTF-8, and
only after its own containment and hardlink checks, so `path` is already resolved and safe
to hand to a subprocess. The extension picks a command by suffix, runs it, and returns the
text, or `None` to decline. Declining leaves the host's behaviour unchanged: an unreadable
file is an error.

What the extension uses from the signature, and why:

- `path`: the command needs a real file path. Receiving it resolved means the extension
  never re-implements the host's path policy.
- `relative_path`: log lines name the file the client asked for, not a server path.

## Why OCR text can never be written back

`read_file` is also the read half of `vault_edit`, `vault_append`,
`vault_batch_frontmatter_update` and `vault_write(merge_frontmatter=True)`. If those received
OCR text they would save it over the original. The host passes `extract=True` only from
`vault_read` and `vault_batch_read`; every other caller keeps failing on a binary.
`tests/test_ocr_extension.py` proves OCR text is available for a file and then calls each
write-back tool through the host's registered MCP tools, asserting the bytes are unchanged.

## Commands

Commands run without a shell. The template is split once with `shlex`, and `{path}` must
be its own argument; it is replaced by the resolved path as a single argv element, so a file
named `a; rm -rf x $(id).png` stays a file name. Non-zero exit, timeout or empty output all
decline.

- Images: `VAULT_OCR_IMAGE_CMD`, default `tesseract {path} - -l eng`. For German and English
  use `-l deu+eng` with the `tesseract-ocr-deu` language pack installed.
- PDFs: tesseract cannot read a PDF. Set `VAULT_OCR_PDF_CMD` to a command that renders the
  pages first, for example a small wrapper around `pdftoppm` and `tesseract`, or
  `ocrmypdf --sidecar`. Empty (the default) leaves PDFs alone.

Results are cached in memory by path, size and modification time, so reading an unchanged
file twice runs OCR once. A restart empties the cache.

## Tests

Tests that need the host seam, `tesseract` or `pdftoppm` skip where they are missing. Set
`VAULT_TEST_REQUIRE_TOOLS=1` for a run that is meant to prove OCR; a missing tool then fails
the run instead. The real-OCR test renders an actual page with poppler rather than using a
hand-made image, because a malformed image makes OCR abort and every later assertion pass
for the wrong reason.
