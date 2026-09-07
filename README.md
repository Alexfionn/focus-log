# Focus Log

Pomodoro timer → four-tap check-in → Notion row per session → Claude-written weekly review as a callout block.

```
index.html (GitHub Pages)  →  repository_dispatch  →  log-session.yml  →  Notion database
                                                       weekly-review.yml (Sun 20:00)  →  callout on a Notion page
```

## Notion database — property names must match exactly

| Property     | Type                  |
|--------------|-----------------------|
| Name         | Title                 |
| Session ID   | Text                  |
| Start        | Date                  |
| Minutes      | Number                |
| Completed    | Checkbox              |
| Focus        | Select                |
| Energy       | Number                |
| Starting     | Select                |
| Environment  | Select                |
| Note         | Text                  |
| Place        | Select                |
| Weekday      | Select (drag options into Mon…Sun order once) |
| Time of Day  | Formula (see below)   |

Select options are created automatically on first write.

**Time of Day** formula:
```
if(hour(prop("Start")) < 6, "Night", if(hour(prop("Start")) < 12, "Morning", if(hour(prop("Start")) < 18, "Afternoon", "Evening")))
```

## Setup checklist

1. Notion integration at notion.so/my-integrations → copy secret.
2. Share the database **and** the review page with the integration (… → Connections).
3. Repo secrets (Settings → Secrets and variables → Actions):
   `NOTION_TOKEN`, `NOTION_DB_ID`, `NOTION_REVIEW_BLOCK_ID` (or `NOTION_REVIEW_PAGE_ID`), `ANTHROPIC_API_KEY`
   (IDs are the 32-char block in the Notion URL, before `?`).
4. GitHub Pages: Settings → Pages → Deploy from branch → `main` / root.
5. In `index.html` set `GITHUB_USER` and `GITHUB_REPO` if they differ.
6. Personal access token for the phone: fine-grained, this repo only, permission **Contents: read and write**. The page asks for it on first save and keeps it in localStorage. Tap the "Focus Log" footer text to remove it.
7. Optional: Actions → Weekly review → Run workflow, to test the review before Sunday.

## Behaviour

- Timer runs on timestamps, so it stays accurate in background tabs.
- "End session and log it" stops a focus session and logs the real minutes. Under 3 minutes is discarded.
- Every check-in question can be skipped; "Skip all" logs the time only.
- Failed sends are queued in the browser and retried on next load or when back online. Each session has an ID, so retries never create duplicates.
- Place: on Start the phone reads its location once. Within 100 m of a saved spot the place is filled in automatically; otherwise the check-in asks. Coordinates never leave the phone — Notion only receives the name. "Train" is tagged but never saved with a location.
- Public repo means public Actions logs, so the scripts print nothing personal (no times, names, notes or review text).
- Weekly review waits until 20 sessions exist, then replaces its own callout block every Sunday. Delete the block if you want; it will be recreated.
