<!-- ctx-daily-summary:start -->
## Yesterday's Summary
<!-- ctx-daily-summary:end -->

## Plan

- 

## Memo

- 

## Agent sessions

```dataview
TABLE WITHOUT ID
  file.link AS "Session",
  project AS "Project",
  provider AS "Agent",
  started AS "Started",
  cwd AS "cwd"
FROM "ctx-sessions"
WHERE date = this.file.day
SORT started ASC
```
