# Rewrite prompt (`/rewrite <instruction>` on a draft issue)

Sent as the user message, with the same system prompt as a first draft (`post-system-prompt.md`). `{instruction}`, `{draft}` and `{input}` are filled in by `poster/compose.py`. The result goes through the same guardrail before the issue is updated.

<!-- REWRITE PROMPT START -->
A human reviewer asked for a change to an existing draft. Rewrite the post applying this instruction while keeping every rule above.

REVIEWER INSTRUCTION: {instruction}

CURRENT DRAFT:
{draft}

INPUT DATA (the only allowed source of facts):
{input}
<!-- REWRITE PROMPT END -->
