# Deterministic post guardrail (`poster/guardrail.py`)

Cheap, explainable checks that run on every draft and every `/rewrite` before it reaches the issue. No second LLM call.

Allowed facts come from: the profile keys given to the model, the topic title and angle, every field of every source item (title, snippet, date, discussion line), and today's year, month and day.

Checks, in order (the first failure wins; each failure is retried up to `llm.max_attempts` with the offending items named in the prompt):
1. `llm_error`: provider error, non-JSON answer, or empty `post_text`. The next attempt switches provider (Groq and Gemini).
2. `too_long`: longer than `post.max_chars` (default 1300; LinkedIn allows 3000).
3. `contains_url`: any link in the post body. Links cut reach, so the source goes in the first comment instead.
4. `fact_check`: every numeric token in the post must normalise to a number present in the sources. Token rule: a digit not glued to a letter, optional `K/M/B` suffix not followed by a letter, or the words thousand/million/billion; commas, `+` and `#` stripped. So "B2B" is not a number, "1.1 million" equals "1.1M", and the current year is allowed.
5. `unverified_source`: a known outlet or company (TechCrunch, Reuters, The Verge, Bloomberg, Wired, GitHub, Stack Overflow, Hacker News, Wikipedia and others) named in the post must appear in the sources.
6. `too_many_hashtags`: more `#tags` than `post.max_hashtags`.

## Translation checks (`check_translation`)
After the English passes, the Hebrew translation is checked the same way, retried up to `llm.max_attempts` times:
1. `contains_url` / `hashtags_in_translation`: no links, and no hashtags (the English part keeps them).
2. `not_hebrew`: at least half of the letters must be Hebrew (English technical terms are fine).
3. `fact_check`: every number in the Hebrew must be a number of the English post. The translator may not add, round or convert numbers.

If no translation passes, the draft still opens with the English only and a review note; `/rewrite` tries again.

Not failures, just review notes on the issue: the model's own warnings, and LinkedIn token expiry.

`/approve` does not re-run these checks: what you approve is what gets posted (Hebrew, separator, English), after a 3000-character limit check on the combined text. Edit the text by hand freely.

`post.signature` (optional, default empty): a fixed last line, appended if missing and excluded from all checks.
