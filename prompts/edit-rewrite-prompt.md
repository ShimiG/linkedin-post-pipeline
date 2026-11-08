# Prompt: AI rewrite on reviewer instructions (Workflow B, `BUILD REWRITE PROMPT`)

Same system prompt as `post-generation-system-prompt.md` (embedded by `scripts/build_workflows.py`), with this user message:

```
A human reviewer asked for a change to an existing draft. Rewrite the post applying this instruction while keeping every rule above.

REVIEWER INSTRUCTION: <instruction typed in the Slack modal>

CURRENT DRAFT:
<draft_text>

INPUT DATA (the only allowed source of facts):
<same JSON object as Workflow A: author, topic, news (from the stored news_json), has_news, today>

Return the JSON object only.
```

The output goes through `REWRITE GUARDRAIL` (same rules as Workflow A) before the approval message is refreshed.
