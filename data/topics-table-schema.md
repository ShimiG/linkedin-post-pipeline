# Airtable table `topics` (the stack)

One row per post idea. The Preparer pops **one** row per run: the newest `queued` row, unless a row is `pinned`, in which case the newest pinned row wins. Add a row = push. Pin a row = move it to the top. Nothing else is needed to reorder.

Credential in n8n: `Airtable` (OAuth or PAT with `data.records:read`, `data.records:write`, `schema.bases:read`). Record ids (`recXXXX`) are used for updates; `topic` is the human primary field.

| Field | Airtable type | Set by | Notes |
|---|---|---|---|
| topic | Single line text (primary) | you | what the post is about, e.g. `Open to work: automation / solutions engineer` |
| category | Single select: job_search, project, fun_fact, dev_stat, opinion, other | you (optional) | drives the prompt's structure rules. Leave empty and the classifier fills it |
| angle | Long text | you | your own notes: facts to include, the point, what to avoid. Facts here count as sources for the guardrail |
| scope | Single select: personal, general | you (optional) or A | `personal` = the post is about you: your profile facts are loaded from the `profile` table and no news search runs. `general` = a Tavily news search runs on the topic. Leave empty and `CLASSIFY TOPIC` decides (LLM, with a keyword fallback) |
| scope_source | Single select: row, llm, fallback | A | how `scope` was decided, shown in the Slack message |
| news_query | Single line text | you (optional) | overrides `topic` as the news search query (general posts only) |
| pinned | Checkbox | you | pop this before anything else |
| status | Single select | workflows | `queued`, `processing`, `pending_approval`, `changes_requested`, `published`, `rejected`, `failed` |
| news_json | Long text | A | JSON array of {title, source, date, snippet, url, score} |
| news_count | Number (integer) | A | 0 is valid |
| draft_text | Long text | A, B | guardrail-passed draft |
| final_text | Long text | B | what was published |
| slack_message_ts | Single line text | A | for chat.update and thread replies |
| reviewer | Single line text | B | Slack user name |
| reviewed_at | Date with time | B | |
| published_at | Date with time | B | |
| linkedin_post_id | Single line text | B | URN returned by LinkedIn, or `sim-…` in simulated mode |
| last_reminded_at | Date with time | C | |
| error_message | Long text | A/B/D | reason codes: `fact_check`, `unverified_source`, `too_long`, `too_many_hashtags`, `llm_error`, or a crash message |
| execution_id | Single line text | A/B/D | n8n `$execution.id`, also the claim token |
| claimed_at | Date with time | A/B | written with `execution_id`; `processing` rows older than `STALE_CLAIM_HOURS` are re-popped |
| created_at | Created time (auto) | Airtable | stack order |
| updated_at | Last modified time (auto) | Airtable | used by the reminder workflow |

## Views
- **Stack** (grid, filter `status = queued`, sort `pinned` desc then `created_at` desc): what will be popped next, top row first.
- **Pipeline board** (Kanban grouped by `status`).

## Status lifecycle
`queued` → `processing` (popped and claimed) → `pending_approval` (draft in Slack) → `published` | `rejected`.
Side states: `failed` (see error_message), `changes_requested` (AI rewrite in progress, returns to `pending_approval`).
Re-queue after reject or failed: set status back to `queued`.

## Why one topic per run
The schedule is the cadence. Three runs a week means three posts a week, and an empty stack means a quiet run with no alert. Nothing waits inside n8n; state lives in this table, so a reviewer can approve a post days later.

# Airtable table `profile` (facts about you)

Key/value rows the Preparer loads for **personal** posts. The model may only use facts that are in this table, in `angle`, or in news, so put here what you want posts to be able to say.

| Field | Airtable type | Notes |
|---|---|---|
| key | Single line text (primary) | e.g. `name`, `headline`, `location`, `years_experience`, `skills`, `looking_for`, `recent_project`, `achievement_1`, `links`, `tone` |
| value | Long text | the fact, in your words. Numbers here are allowed by the guardrail |
| use_for | Single select: personal_only, always | `always` rows (name, headline) are also given to general posts so the voice stays yours. `personal_only` rows are loaded only for personal posts |
| active | Checkbox | untick to hide a fact without deleting it |

The Preparer reads every active row and passes them to the model as `author: { key: value, ... }`.
