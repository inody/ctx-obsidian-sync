---
type: project
project_id: example-project
status: active
roots:
  - "~/work/example-project"
root_prefixes:
  - "~/work/example-project-"
repositories:
  - "https://github.com/example/example-project"
---

# Example Project

Describe the purpose and scope of the project here.

## Project locations

Document the roles of the workspaces and repositories listed in frontmatter.

## Agent sessions

```dataview
TABLE WITHOUT ID
  file.link AS "Session",
  date AS "Date",
  provider AS "Agent",
  pc AS "PC",
  cwd AS "cwd"
FROM "ctx-sessions"
WHERE project_id = this.project_id
SORT started DESC
```
