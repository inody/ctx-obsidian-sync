# Obsidian examples

These files show the Obsidian-side conventions understood by `ctx-obsidian-sync`.
They are examples, not an installer-managed vault and not a complete personal
knowledge-management system.

- `diary.md` contains the marker pair used by the daily digest and a Dataview
  table for sessions started on that date.
- `project.md` shows the minimum project frontmatter used for deterministic
  `cwd` matching.
- `workspace-inbox.md` uses DataviewJS to list recent session workspaces that
  do not yet have a `project_id`.

Copy and adapt them in your own vault. Run `ctx_obsidian_doctor.py` after
changing project definitions or the diary template.
