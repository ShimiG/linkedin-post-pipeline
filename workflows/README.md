# n8n workflow exports (sanitized, generated)

| File | Workflow | Trigger | Nodes |
|---|---|---|---|
| A-preparer.json | Pop one topic, classify, gather sources, draft, guardrail, send to Slack | Schedule Mon/Wed/Fri 09:00 + Manual | 38 |
| B-resolver.json | Approve / Reject / Edit / AI rewrite / publish | Webhook (Slack interactivity) | 46 |
| C-reminders.json | Nudge pending approvals | Schedule every 4 h | 7 |
| D-error-handler.json | Crash → mark row failed → alert | Error Trigger (set as error workflow on A, B, C) | 7 |

These files are **generated** by `scripts/build_workflows.py` from the code in that script and the prompts in `prompts/`. Edit there, re-run, re-import. Do not hand-edit the JSON.

Base, table and credential ids are placeholders here. `scripts/localize.py` writes importable copies to `workflows/raw/` (gitignored) using `workflows/raw/local-ids.json` (template: `workflows/local-ids.example.json`).

After importing B, activate it and re-publish it after every edit: the production webhook URL (`…/webhook/slack-interactions`, no `-test`) goes into the Slack app's interactivity request URL.
