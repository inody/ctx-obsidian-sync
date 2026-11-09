---
type: project-inbox
lookback_days: 120
ignored_workspaces:
  - "~"
  - "~/.cache"
  - "~/.local/state"
  - "/private/tmp"
  - "/tmp"
---

# Workspace Inbox

Recent ctx session workspaces that do not yet match a project note.

```dataviewjs
const lookbackDays = Number(dv.current().lookback_days ?? 120);
const ignored = Array.from(dv.current().ignored_workspaces ?? []);
const cutoff = dv.date("today").minus({ days: lookbackDays });
const groups = new Map();
let scannedSessions = 0;
let candidateSessions = 0;

const isIgnored = cwd => ignored.some(root =>
  cwd === root || (root !== "~" && cwd.startsWith(root + "/"))
);

for (const page of dv.pages('"ctx-sessions"')) {
  scannedSessions += 1;
  const frontmatter = page.file.frontmatter ?? {};
  const cwd = frontmatter.cwd;
  if (typeof cwd !== "string" || !cwd || frontmatter.project_id || isIgnored(cwd)) {
    continue;
  }

  const started = dv.date(frontmatter.date ?? page.file.day);
  if (!started || started.toMillis() < cutoff.toMillis()) {
    continue;
  }

  candidateSessions += 1;
  const current = groups.get(cwd) ?? {
    count: 0,
    latest: started,
    latestSession: page.file.link,
  };
  current.count += 1;
  if (started.toMillis() > current.latest.toMillis()) {
    current.latest = started;
    current.latestSession = page.file.link;
  }
  groups.set(cwd, current);
}

const rows = Array.from(groups.entries())
  .sort((a, b) =>
    b[1].latest.toMillis() - a[1].latest.toMillis()
    || b[1].count - a[1].count
    || a[0].localeCompare(b[0])
  )
  .map(([cwd, info]) => [
    `\`${cwd}\``,
    info.count,
    info.latest.toFormat("yyyy-MM-dd"),
    info.latestSession,
  ]);

dv.paragraph(
  `Local index: ${scannedSessions} session notes / ${candidateSessions} candidate sessions / ${rows.length} workspaces`
);
dv.table(["Workspace", "Sessions", "Latest", "Latest session"], rows);
```
