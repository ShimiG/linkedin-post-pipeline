# LinkedIn post pipeline (n8n)

A topic stack in Airtable feeds an n8n pipeline that drafts one LinkedIn post per run, checks it, and sends it to Slack for a human to approve, reject or edit. Approved posts are published to a personal LinkedIn profile (or simulated).

```
Airtable topics (stack)  ──pop one──▶  A: classify ─▶ sources ─▶ draft ─▶ guardrail ─▶ Slack approval
                                           │ personal: profile table        │
                                           │ general:  Tavily news          ▼
Airtable status board    ◀──────────────  B: approve / reject / edit / AI rewrite ─▶ LinkedIn (or simulated)
                                           C: reminders · D: error handler
```

## How a post happens
1. You add a row to the `topics` table: a title, optional notes (`angle`), optional category. That is a push.
2. Mon/Wed/Fri at 09:00 (or on a manual run) Workflow A pops the top of the stack: the newest `queued` row, or a `pinned` one.
3. A decides whether the post is **about you** (`personal`) or **about the world** (`general`). You can set `scope` on the row to skip the classifier.
   - personal → loads your facts from the `profile` table, no news search.
   - general → Tavily news search on the topic, plus your name and headline from `profile`.
4. The model (Groq, Gemini failover) writes the post in your voice. A deterministic guardrail rejects any number, outlet or hashtag count that is not backed by the sources, and retries with the problem named.
5. Slack gets the draft with scope, sources, review notes and **Approve / Reject / Edit** buttons.
6. Workflow B handles the click: status gate, claim with read-back, publish (simulated by default, LinkedIn node when `PUBLISH_MODE=linkedin`), message updated in place.
7. Nothing waits inside n8n. State lives in Airtable, so you can approve three days later.

## Where things live
| Folder | Purpose |
|---|---|
| `workflows/` | Generated n8n exports (sanitized). `raw/` holds your localized copies (gitignored) |
| `scripts/` | `build_workflows.py` (generator), `localize.py` (your ids in) |
| `prompts/` | System prompt, classifier prompt, guardrail spec, rewrite prompt |
| `data/` | `topics` + `profile` table schemas, Tavily search spec |
| `slack/` | Slack app manifest |

## Setup
1. **Airtable**: create tables `topics` and `profile` per `data/topics-table-schema.md`. Fill `profile` with the facts posts may use about you.
2. **n8n credentials**: Airtable, Slack bot, Tavily, Groq (Header Auth `Authorization: Bearer …`), Google Gemini (Header Auth `x-goog-api-key`). Optional: LinkedIn OAuth2 (needs the "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect" products on your LinkedIn app; the person id is the `sub` claim from `/v2/userinfo`).
3. **n8n Variable** `slack_signing_secret` (used by Workflow B to verify Slack signatures).
4. Copy `workflows/local-ids.example.json` to `workflows/raw/local-ids.json`, fill it (Airtable ids, Slack channel ids, timezone, credential ids), run `python3 scripts/localize.py`, import the four `workflows/raw/*.local.json` files.
5. Set D as the error workflow of A, B and C. Activate B; put its production webhook URL in the Slack app manifest (`slack/app-manifest.yaml`) and install the app.
6. Slack channel ids and timezone come from `local-ids.json`. In A's CONFIG: `MAX_HASHTAGS`, optional `POST_SIGNATURE`. In B's CONFIG: `PUBLISH_MODE`, `LINKEDIN_PERSON_ID`.
7. Add a topic row, run A manually, approve in Slack with `PUBLISH_MODE=simulated` first.

## Changing the prompt or the rules
Edit `prompts/post-generation-system-prompt.md` (post prompt and classifier prompt) or `scripts/build_workflows.py` (guardrail, node logic), then:
```
python3 scripts/build_workflows.py && python3 scripts/localize.py
```
and re-import A and B.

## Rule zero
No API keys, tokens or passwords in any file in this repo. Credentials live only in the n8n vault. `workflows/raw/` is gitignored.
