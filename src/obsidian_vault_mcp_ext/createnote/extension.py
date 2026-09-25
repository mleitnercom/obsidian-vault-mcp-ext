"""CreateNoteExtension: a write path that cannot replace an existing file."""

from obsidian_vault_mcp.extensions import Extension

from .._mutations import declare

from . import tools


class CreateNoteExtension(Extension):
    """vault_create_note: add a Markdown note, never replace one. Works unconfigured;
    VAULT_CREATE_NOTE_* can narrow it. See createnote/tools.py."""

    def register_tools(self, mcp) -> None:
        declare({
            "vault_create_note": "mutation",
        })
        mcp.tool(
            name="vault_create_note",
            description=(
                "Create a new Markdown note without ever replacing an existing file. Use it "
                "instead of vault_write whenever the note must not exist yet. Refuses if the "
                "note exists (error_code note_exists: nothing was changed; do not retry the same "
                "name and do not fall back to vault_write), including when two calls race, so a "
                "client that lost a response can retry safely. The parent folder must exist. "
                "Content is read back before success is reported. An operator may have narrowed "
                "paths and frontmatter with VAULT_CREATE_NOTE_*; a refusal then names the field. "
                "Error codes: path_not_allowed, invalid_frontmatter, "
                "frontmatter_missing_field, frontmatter_not_allowed, frontmatter_value_rejected, "
                "id_path_mismatch, missing_body_section, content_too_large, note_exists, "
                "parent_folder_missing, write_verification_failed, invalid_policy."
            ),
            annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        )(tools.vault_create_note)
