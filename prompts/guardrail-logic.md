# Deterministic post guardrail (Code node `PARSE + FACT GUARDRAIL` in A, `REWRITE GUARDRAIL` in B)

Purpose: cheap, explainable checks that run on every draft before it reaches Slack. No second LLM call.

Sources of allowed facts: every `profile` row given to the model (all active rows for a personal post, `always` rows for a general one), the topic `title` and `angle`, every kept news item (title, snippet, date), and today's year/month/day.

Checks, in order (first failure wins, each is retried up to `LLM_MAX_ATTEMPTS` with the offending items named in the prompt):
1. `llm_error`: provider error, non-JSON answer, or empty `post_text`. The next attempt switches provider (`LLM_PRIMARY` ↔ `LLM_FALLBACK`).
2. `too_long`: `post_text` longer than `POST_MAX_CHARS` (default 1300; LinkedIn allows 3000).
3. `fact_check`: every numeric token in the post must normalise to a number present in the sources. Token rule: a digit not glued to a letter, optional `K/M/B` suffix not followed by a letter, or the words thousand/million/billion; commas, `+` and `#` stripped. So "B2B" is not a number, "1.1 million" equals "1.1M", and "2026" from `today` is allowed.
4. `unverified_source`: a known outlet or company named in the post (TechCrunch, Reuters, The Verge, Bloomberg, Forbes, BBC, Wired, Ars Technica, Stack Overflow, GitHub, Gartner, McKinsey, HBR, NYT, WSJ, Financial Times, Business Insider, Axios, Hacker News) must appear in a news item, in `angle` or in a profile row.
5. `too_many_hashtags`: more `#tags` than `MAX_HASHTAGS`.

Not failures, just reviewer warnings: a general post whose news search returned nothing usable; a personal post with no active profile rows; the model's own warnings; scope decided by the keyword fallback because the classifier call failed.

`POST_SIGNATURE` (CONFIG, optional, default empty): a fixed last line (for example a portfolio link). Appended if missing, excluded from all checks.
