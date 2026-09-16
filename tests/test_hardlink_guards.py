"""The hardlink guard, on every path in this package that reads vault content.

A hardlink inside the vault to a file outside it is a real directory entry: nothing to
follow, so containment checks cannot see it and the file reads as an ordinary note.
Upstream #79 guards the host's own read paths (read_file, search, and friends). The
extensions here read files directly or enumerate them with rglob, which #79 does not
reach, and on a host older than #79 even read_file is unguarded. So each extension
carries its own guard, independent of the host version.

Every test is a comparison, not a single assertion. The same content sits in the vault
twice: once as an ordinary copy, once as a hardlink to a file outside. The copy must be
indexed, scanned, repaired, listed or materialized - that is the negative control,
proving the path genuinely reads such a file. Only then is the absence of the hardlink
meaningful. A test that merely asserts "the outside content is not in the output" also
passes when the path never looked at either file.
"""

import importlib
import json
import os

import pytest

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp_ext.maintenance import tools as maintenance
from obsidian_vault_mcp_ext.recurring import _config as recurring_config
from obsidian_vault_mcp_ext.recurring import tools as recurring
from obsidian_vault_mcp_ext.semantic import _config as semantic_config
from obsidian_vault_mcp_ext.semantic import tools as semantic_tools
from obsidian_vault_mcp_ext.semantic.engine import SemanticSearchEngine
from obsidian_vault_mcp_ext.templates import _config as templates_config
from obsidian_vault_mcp_ext.templates import tools as templates

CANARY = "KANARIENVOGEL-AUSSERHALB"
NOTE = f"---\ntitle: Geheim\n---\n# Abschnitt\n\n{CANARY} steht hier und sonst nirgends.\n"


def _link(source, target):
    try:
        os.link(source, target)
    except (OSError, NotImplementedError, AttributeError) as exc:
        pytest.skip(f"hardlinks unavailable here: {exc}")
    assert target.stat().st_nlink == 2


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(host_config, "VAULT_PATH", v)
    return v


@pytest.fixture
def copy_and_link(vault, tmp_path):
    """Identical content twice: an ordinary copy and a hardlink to an outside file."""
    outside = tmp_path / "ausserhalb.md"
    outside.write_text(NOTE, encoding="utf-8")
    (vault / "kopie.md").write_text(NOTE, encoding="utf-8")
    _link(outside, vault / "verlinkt.md")
    return outside


class TestSemantic:
    def test_indexable_accepts_the_copy_but_not_the_link(self, copy_and_link):
        assert SemanticSearchEngine._is_indexable_path("kopie.md") is True
        assert SemanticSearchEngine._is_indexable_path("verlinkt.md") is False

    def test_change_detection_queues_the_copy_but_not_the_link(self, copy_and_link):
        """Change detection feeds the incremental reindex; it runs without the heavy extra."""
        updates = SemanticSearchEngine()._detect_updates_unlocked()

        assert "kopie.md" in updates, "negative control failed: detection did not see the copy"
        assert "verlinkt.md" not in updates

    def test_incremental_reindex_of_the_link_by_name_indexes_nothing(self, copy_and_link, monkeypatch):
        """An explicit path (reindex(paths=[...]) or a file watcher event) bypasses the
        vault walk. The decision point must still refuse the link."""
        engine = SemanticSearchEngine()
        chunked = []
        monkeypatch.setattr(
            "obsidian_vault_mcp_ext.semantic.engine.chunk_markdown_file",
            lambda path: chunked.append(path.name) or [],
        )
        monkeypatch.setattr(engine, "_rebuild_indices_unlocked", lambda: None)
        monkeypatch.setattr(engine, "_persist_unlocked", lambda: None)

        engine._incremental_reindex_unlocked({"kopie.md": "modify", "verlinkt.md": "modify"})

        assert "kopie.md" in chunked, "negative control failed: the copy was not chunked"
        assert "verlinkt.md" not in chunked


def _semantic_deps_available() -> bool:
    for mod in ("faiss", "fastembed", "rank_bm25", "numpy"):
        try:
            importlib.import_module(mod)
        except Exception:
            return False
    return True


@pytest.mark.skipif(
    not _semantic_deps_available(),
    reason="semantic extra not installed; unproven here, proven on the server run",
)
def test_semantic_search_finds_the_copy_but_never_the_link(copy_and_link, monkeypatch):
    """End to end through the registered tools, with a real index."""
    monkeypatch.setattr(semantic_config, "SEMANTIC_SEARCH_ENABLED", True)
    monkeypatch.setattr(semantic_config, "SEMANTIC_BUILD_ON_DEMAND", True)
    semantic_tools.set_engine(None)
    try:
        reindex = json.loads(semantic_tools.vault_reindex(full=True))
        assert "error" not in reindex, reindex
        result = json.loads(semantic_tools.vault_semantic_search(CANARY, max_results=20))
    finally:
        semantic_tools.set_engine(None)

    paths = {hit["path"] for hit in result["results"]}
    assert "kopie.md" in paths, f"negative control failed: {result}"
    assert "verlinkt.md" not in paths


class TestMaintenance:
    CP1252 = f"Geschäftsführer {CANARY}\n".encode("cp1252")

    @pytest.fixture
    def broken_copy_and_link(self, vault, tmp_path):
        outside = tmp_path / "ausserhalb.md"
        outside.write_bytes(self.CP1252)
        (vault / "kopie.md").write_bytes(self.CP1252)
        _link(outside, vault / "verlinkt.md")
        return outside

    def test_scan_reports_the_copy_but_not_the_link(self, broken_copy_and_link):
        result = json.loads(maintenance.vault_scan_encoding())
        paths = {issue["path"] for issue in result["issues"]}

        assert "kopie.md" in paths
        assert "verlinkt.md" not in paths

    def test_repair_fixes_the_copy_and_does_not_pull_outside_content_in(self, vault, broken_copy_and_link):
        """The repair writes atomically, so it would not alter the outside file. It would
        replace the link with a decoded copy of the outside content: a regular vault file
        that every guard downstream then treats as legitimate."""
        result = json.loads(maintenance.vault_repair_encoding())
        repaired = {item["path"] for item in result["repaired"]}

        assert "kopie.md" in repaired, f"negative control failed: {result}"
        assert (vault / "kopie.md").read_bytes().decode("utf-8").startswith("Geschäfts")
        assert "verlinkt.md" not in repaired
        assert (vault / "verlinkt.md").stat().st_nlink == 2, "the link was replaced by outside content"


class TestTemplates:
    @pytest.fixture
    def template_copy_and_link(self, vault, tmp_path, monkeypatch):
        (vault / "templates").mkdir()
        body = f"# {{{{title}}}}\n\n{CANARY}\n"
        outside = tmp_path / "vorlage.md"
        outside.write_text(body, encoding="utf-8")
        (vault / "templates" / "kopie.md").write_text(body, encoding="utf-8")
        _link(outside, vault / "templates" / "verlinkt.md")
        monkeypatch.setattr(templates_config, "VAULT_TEMPLATER_FOLDER", "templates")
        monkeypatch.setattr(templates_config, "VAULT_OBSIDIAN_REST_URL", "")
        return outside

    def test_list_shows_the_copy_but_not_the_link(self, template_copy_and_link):
        names = {t["name"] for t in json.loads(templates.vault_template_list())["templates"]}

        assert "kopie" in names
        assert "verlinkt" not in names

    def test_render_uses_the_copy_but_refuses_the_link(self, template_copy_and_link):
        copy = json.loads(templates.vault_template_render("kopie", target_path_hint="a.md"))
        link = json.loads(templates.vault_template_render("verlinkt", target_path_hint="b.md"))

        assert CANARY in copy.get("content", ""), f"negative control failed: {copy}"
        assert "error" in link and CANARY not in json.dumps(link)

    def test_apply_refuses_the_link_and_writes_nothing(self, vault, template_copy_and_link):
        link = json.loads(templates.vault_template_apply("verlinkt", target_path="neu.md"))

        assert "error" in link, link
        assert not (vault / "neu.md").exists()


class TestRecurring:
    TEMPLATE = (
        "---\n"
        "type: recurring-template\n"
        "id: {id}\n"
        "title: {title}\n"
        "recurrence_anchor_mode: absolute\n"
        "recurrence_anchor: fixed-07-31\n"
        "created: 2026-01-01\n"
        "target_folder: tasks\n"
        "due_offset_days: 0\n"
        "---\n"
        "## Next Action (Template)\n"
        "{canary}\n"
    )

    @pytest.fixture
    def recurring_copy_and_link(self, vault, tmp_path, monkeypatch):
        (vault / "templates").mkdir()
        (vault / "tasks").mkdir()
        (vault / "templates" / "kopie.md").write_text(
            self.TEMPLATE.format(id="kopie", title="Kopie", canary=CANARY), encoding="utf-8"
        )
        outside = tmp_path / "vorlage.md"
        outside.write_text(self.TEMPLATE.format(id="verlinkt", title="Verlinkt", canary=CANARY), encoding="utf-8")
        _link(outside, vault / "templates" / "verlinkt.md")
        monkeypatch.setattr(recurring_config, "VAULT_RECURRING_ENABLED", True)
        monkeypatch.setattr(recurring_config, "VAULT_RECURRING_TEMPLATES_FOLDER", "templates")
        monkeypatch.setattr(recurring_config, "VAULT_RECURRING_CATCHUP_MODE", "next")
        monkeypatch.setattr(recurring_config, "VAULT_RECURRING_DONE_STATUS", "done")
        return outside

    def test_materialize_instantiates_the_copy_but_not_the_link(self, vault, recurring_copy_and_link):
        result = json.loads(recurring.recurring_materialize(as_of="2026-08-01"))
        created = {item["template_id"] for item in result["created"]}

        assert "kopie" in created, f"negative control failed: {result}"
        assert "verlinkt" not in created
        written = "".join(p.read_text(encoding="utf-8") for p in (vault / "tasks").glob("*.md"))
        assert written.count(CANARY) == 1, "outside template content reached a vault instance"
