"""CreateNoteExtension: a write path that cannot replace an existing file."""

from obsidian_vault_mcp.extensions import Extension

from . import tools


class CreateNoteExtension(Extension):
    """vault_create_note for automated clients that may add notes but must never damage
    one. Inert until VAULT_CREATE_NOTE_PATH_PATTERN is set; see createnote/tools.py."""

    def register_tools(self, mcp) -> None:
        mcp.tool(
            name="vault_create_note",
            description=(
                "Create a new note at a path the server has been configured to allow, without "
                "ever replacing an existing file. Refuses if the note exists, including when two "
                "calls race, so a client that lost a response can retry without risking a "
                "duplicate write. Content is read back before success is reported. "
                "Inert until VAULT_CREATE_NOTE_PATH_PATTERN is configured. "
                "Error codes: create_note_disabled, path_not_allowed, invalid_frontmatter, "
                "frontmatter_missing_field, frontmatter_not_allowed, frontmatter_value_rejected, "
                "id_path_mismatch, missing_body_section, content_too_large, note_exists, "
                "parent_folder_missing, write_verification_failed, invalid_policy."
            ),
            annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
        )(tools.vault_create_note)
