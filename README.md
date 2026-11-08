# LinkedIn post pipeline (GitHub Actions)

Drafts a LinkedIn post three times a week from free sources, fact-checks it, translates it to Hebrew, and opens a GitHub issue for you to approve. The published post has the Hebrew first, then the English. Comment `/approve` and it is published on your personal profile. No server, no database, no paid services.

```
schedule Mon/Wed/Fri ──▶ draft job
                           ├─ your queued `topic` issue, if any, else alternate:
                           │    AI news   ← Hacker News (top AI story this week) + the article text
                           │    coding fact ← a Wikipedia article from config.yml
                           ├─ Groq writes the post in your voice (Gemini as fallback)
                           ├─ guardrail: every number and outlet must be in the sources, no links, ≤ 3 hashtags
                           ├─ Hebrew translation, checked: Hebrew script, no new numbers, no links or hashtags
                           └─ opens a `draft` issue  ──▶ you get a GitHub notification

you comment on the issue ──▶ review job
                           ├─ /approve          → one LinkedIn post (Hebrew, • • •, English) + source link as the first comment
                           ├─ /reject           → closed, that source is never reused
                           └─ /rewrite <ask>    → English rewritten and re-checked, translated again, issue updated
```

The issues are the whole system of record: what was drafted, which sources were used, what was published. Nothing is committed by the bot.

## Using it
- **Approve from your phone:** the GitHub mobile app notifies you about the new issue; reply `/approve`.
- **Edit by hand:** edit the issue and change the Hebrew (`<!-- post-he:… -->`) or English (`<!-- post-en:… -->`) block, then `/approve`. What you approve is exactly what is posted: the Hebrew, a `• • •` line, then the English. Empty the Hebrew block to post English only.
- **Bilingual on or off:** `config.yml` → `bilingual.enabled`. The separator line is `bilingual.separator`.
- **Queue your own topic:** open an issue with the label `topic`. The title is the subject, the body is your notes (facts, the point you want to make). Queued topics go before automatic ones, oldest first, and get your full profile.
- **Draft now:** Actions → *Draft a post* → *Run workflow* (choose `auto`, `news` or `fact`).
- **Change the voice or rules:** `prompts/post-system-prompt.md`. **Change the translation style:** `prompts/translate-prompt.md`. **Change sources, schedule, limits:** `config.yml` and `.github/workflows/draft.yml`.

## Setup (once, about 20 minutes)

### 1. AI keys (free)
- Groq: <https://console.groq.com/keys>
- Gemini: <https://aistudio.google.com/apikey>

### 2. LinkedIn token
1. Create an app at <https://www.linkedin.com/developers/apps>. LinkedIn asks you to link a Company Page; any page you admin works (you can create a small one).
2. On the app's **Products** tab, add **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect** (both self-serve).
3. Open the **OAuth token generator** (<https://www.linkedin.com/developers/tools/oauth/token-generator>), pick your app, tick `openid`, `profile` and `w_member_social`, and generate.
4. The token lasts **60 days** and LinkedIn does not let personal apps refresh it. The draft job opens a reminder issue 10 days before it expires, as long as you record the date (step 4).

### 3. Your profile
`profile.yml` holds the facts posts may use about you. It is gitignored; copy `profile.example.yml` if you don't have one. News and fact posts only see the keys in `config.yml` → `general_post_profile_keys`.

### 4. Secrets and variables
```bash
gh secret set GROQ_API_KEY
gh secret set GEMINI_API_KEY
gh secret set LINKEDIN_ACCESS_TOKEN
gh secret set PROFILE_YML < profile.yml
gh variable set LINKEDIN_TOKEN_EXPIRES --body 2027-01-08   # the date 60 days after you generated the token
```
Optional variables: `PUBLISH_MODE` (`simulated` by default, set to `linkedin` to post for real) and `TIMEZONE` (for "today" in prompts, e.g. `Europe/London`).

### 5. Try it
1. Actions → *Draft a post* → *Run workflow*. A `draft` issue appears within a minute.
2. Comment `/approve`. In simulated mode it shows what would be posted and closes the issue.
3. When you are happy: `gh variable set PUBLISH_MODE --body linkedin`.

## Run it locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
DRY_RUN=1 GROQ_API_KEY=... .venv/bin/python -m poster.draft   # prints the issue instead of opening it
```

## Costs and limits
- GitHub Actions: each run takes about a minute; the free plan includes 2,000 minutes a month.
- Hacker News search and Wikipedia: free, no key. Groq and Gemini: free tiers, a few calls per week.
- Scheduled runs can start a few minutes late. If a scheduled run never fires, check the Actions tab for a disabled-workflow notice.

## Rule zero
No API keys, tokens or personal details in any committed file. Keys and your profile live only in GitHub secrets; `profile.yml` is gitignored.
