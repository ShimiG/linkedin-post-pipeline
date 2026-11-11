# Deterministic post guardrail (`poster/guardrail.py`)

Cheap, explainable checks that run on every draft and every `/rewrite` before it reaches the issue. No second LLM call.

Allowed facts come from: the profile keys given to the model, the topic title and angle, every field of every source item (title, snippet, date, discussion line, and for "On this day" posts `years_ago`), and today's year, month and day.

Checks, in order (the first failure wins; each failure is retried up to `llm.max_attempts` with the offending items named in the prompt):
1. `llm_error`: provider error, non-JSON answer, or empty `post_text`. The next attempt switches provider (Groq and Gemini).
2. `too_long`: longer than `post.max_chars` (default 1300; LinkedIn allows 3000).
3. `contains_url`: any link in the post body. Links cut reach, so the source goes in the first comment instead.
4. `fact_check`: every numeric token in the post must normalise to a number present in the sources. Token rule: a digit not glued to a letter, optional `K/M/B` suffix not followed by a letter, or the words thousand/million/billion; commas, `+` and `#` stripped. So "B2B" is not a number, "1.1 million" equals "1.1M", and the current year is allowed.
5. `unverified_source`: a known outlet or company (TechCrunch, Reuters, The Verge, Bloomberg, Wired, GitHub, Stack Overflow, Hacker News, Wikipedia and others) named in the post must appear in the sources.
6. `copied_from_example`: the post shares `examples.copy_ngram` (8) consecutive words with a saved writing example. Examples are for style only.
7. `borrowed_first_person` (news posts only): a sentence with "I / my / me" whose content words mostly (60%+) come from the article and not from your profile. That is the article author's experience retold as yours; the retry asks for third-person attribution ("a developer wrote that…").
8. `too_many_hashtags`: more `#tags` than `post.max_hashtags`.

## Translation checks (`check_translation`)
After the English passes, the Hebrew translation is checked the same way, retried up to `llm.max_attempts` times:
1. `contains_url` / `hashtags_in_translation`: no links, and no hashtags (the English part keeps them).
2. `not_hebrew`: at least half of the letters must be Hebrew (English technical terms are fine).
3. `fact_check`: every number in the Hebrew must be a number of the English post. The translator may not add, round or convert numbers.

The Hebrew headline (`check_headline`) gets the same checks, plus: present, one line, at most 90 characters. A rejected headline retries the translation with the reason named.

If no translation passes, the draft still opens with the English only and a review note; `/rewrite` tries again.

Not failures, just review notes on the issue: the model's own warnings, and LinkedIn token expiry.

An "On this day" draft is tied to its date: `/approve` on any other month and day posts nothing and asks for `/approve anyway`, so a stale "on this day" is never published by accident.

`/schedule dd/mm/yyyy HH:MM` is a delayed `/approve`: it checks the time (valid, in the future), the post (present, at most 3000 characters) and, for an "On this day" draft, that the day matches. At the scheduled time the draft is first taken off the schedule and then published, so a failure is reported once and never retried, and a post cannot go out twice.

`/approve` does not re-run these checks: what you approve is what gets posted (Hebrew, separator, English), after a 3000-character limit check on the combined text. Edit the text by hand freely.

`post.signature` (optional, default empty): a fixed last line, appended if missing and excluded from all checks.

## Image checks (`poster/images.py`)

Only for the AI illustration (about one draft in five); branded cards are drawn from the post's own headline and first paragraph, so they need none.

1. **Topic words:** the post title and English text must contain none of `SENSITIVE_WORDS` (politics, conflict and violence, death, crime, drugs, alcohol and gambling, sex and nudity, religion and hate, and countries and groups at the centre of current conflicts). Whole words, any case.
2. **Model verdict:** the prompt writer (`prompts/image-prompt.md`) must return `safe: true`. It is told to refuse rather than soften a sensitive topic.
3. **Prompt words:** the image prompt it wrote must also pass the word check.
4. **Fixed style:** code appends `STYLE` (no people, faces, text, logos, flags, national or religious symbols) to every prompt.
5. **Blank image:** an almost uniform image (a tripped model filter) is refused.

Any failure gives the draft a card instead, with the reason in the review notes. Every image is shown on the draft issue before you `/approve`.
