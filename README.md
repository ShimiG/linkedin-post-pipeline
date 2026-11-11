# LinkedIn post pipeline (GitHub Actions)

Drafts a LinkedIn post three times a week from free sources, fact-checks it, translates it to Hebrew, and opens a GitHub issue for you to approve. The published post opens with a short Hebrew headline about the post and a `*English below` line, then the Hebrew text, then the English. Comment `/approve` and it is published on your personal profile. No server, no database, no paid services.

```
schedule Mon/Wed/Fri ──▶ draft job
                           ├─ your queued `topic` issue, if any, else take turns:
                           │    AI news     ← Hacker News (top AI story this week) + the article text
                           │    coding fact ← a Wikipedia article from config.yml
                           │    on this day ← Wikipedia's "On this day" list for today's date, tech events only
                           ├─ Groq writes the post in your voice (Gemini as fallback)
                           ├─ guardrail: every number and outlet must be in the sources, no links, ≤ 3 hashtags,
                           │    no article story retold as yours, no wording copied from your examples
                           ├─ Hebrew translation + headline, checked: Hebrew script, no new numbers, no links or hashtags
                           ├─ image: a branded card, or about one post in five an AI illustration (safe topics only)
                           └─ opens a `draft` issue  ──▶ you get a GitHub notification

you comment on the issue ──▶ review job
                           ├─ /approve          → one LinkedIn post (headline, *English below, Hebrew, • • •, English);
                           │                       the issue then gives you the source link to paste as the first comment
                           │                       (an "On this day" post approved on another date needs /approve anyway)
                           ├─ /schedule dd/mm/yyyy HH:MM → the same as /approve, but at that time (/unschedule cancels)
                           ├─ /reject           → closed; that story/article may come back after a 7-day cooldown
                           ├─ /block [site]     → closed; that story/article (or the whole website) is never used again
                           ├─ /image [card|ai|none] → a new image, another kind, or none
                           └─ /rewrite <ask>    → English rewritten and re-checked, translated again, issue updated
```

The issues are the whole system of record: what was drafted, which sources were used, what was published. The bot only commits post images (to the `post-images` branch, never `main`) and saved writing examples.

## Using it
- **Approve from your phone:** the GitHub mobile app notifies you about the new issue; reply `/approve`.
- **Publish later:** reply `/schedule 25/12/2026 09:30` (day/month/year, 24-hour clock, in your `TIMEZONE`). It is a delayed `/approve`: the draft gets the `scheduled` label, and the *Publish scheduled posts* workflow posts it with its first check after that time (checks run every 15 minutes, so allow up to about 20 minutes). What is posted is the text in the issue at that moment, so you can keep editing. `/unschedule` cancels, `/schedule` with a new time moves it, `/approve` publishes right away, `/reject` drops it. If publishing fails at that time (an expired token, say), the issue tells you and the schedule is cancelled, it does not retry on its own.
- **Edit by hand:** edit the issue and change the Hebrew (`<!-- post-he:… -->`) or English (`<!-- post-en:… -->`) block, then `/approve`. What you approve is exactly what is posted: the Hebrew block (headline, `*English below`, Hebrew text), a `• • •` line, then the English. Empty the Hebrew block to post English only. The _italic_ notes in the issue are never posted.
- **Bilingual on or off:** `config.yml` → `bilingual.enabled`. The headline is `bilingual.headline`, the line under it `bilingual.english_marker`, the separator `bilingual.separator`.
- **Reject or block:** `/reject` drops just this post; its story or article can be drafted again after `review.reject_cooldown_days` (7). `/block` drops it and never uses that story, Wikipedia article or event again; `/block site` never uses that website again (news only). Both work on closed drafts too, and `/unblock` undoes them.
- **Writing examples:** posts you like teach the drafts your style. Open an issue labelled `example` (title: why you like it, body: the post text), or comment `/example` on one of your own drafts. They are saved in `examples/`; each draft sees 3 of them as style references, never as facts, and a draft that copies 8 words in a row from one is rejected.
- **On this day:** every third automatic post is about something that happened on today's date in an earlier year: an event, a birth or a death from Wikipedia's "On this day" list, with the linked articles as background. Only entries matching `config.yml` → `on_this_day.keywords` (computing and AI by default) are used; on a date with no match the run drafts the next kind instead. The draft is tied to its date: approve it the same day. On a later day `/approve` posts nothing and asks you to fix the opening and comment `/approve anyway`. To turn these posts off, remove `onthisday` from `rotation`.
- **Images:** every post goes out with a picture, shown on the draft issue before you approve. Most get a **branded card**: the Hebrew headline and the English first paragraph on the brand gradient, in the Heebo font (`fonts/`, SIL Open Font License). About one in five (`images.ai_every`) gets an **AI illustration** of the topic from Cloudflare Workers AI instead. AI images are only made for safe topics, checked three ways: the post must not mention politics, war, violence, crime, death, drugs, sex, religion and similar subjects (`SENSITIVE_WORDS` in `poster/images.py`); the model writing the image prompt must judge the topic safe under the rules in `prompts/image-prompt.md`; and its prompt is checked for the same words. Every image prompt also ends with a fixed line: no people, no text, no logos, no flags. When any check says no, the post gets a card and the review notes say why. On the issue, `/image` makes a new image of the same kind, `/image card` or `/image ai` switches, `/image none` posts text only. The card shows the headline, so after editing it by hand comment `/image card` to redraw it (`/rewrite` redraws it for you).
- **Queue your own topic:** open an issue with the label `topic`. The title is the subject, the body is your notes (facts, the point you want to make). Queued topics go before automatic ones, oldest first, and get your full profile.
- **Draft now:** Actions → *Draft a post* → *Run workflow* (choose `auto`, `news`, `fact` or `onthisday`).
- **Change the voice or rules:** `prompts/post-system-prompt.md`. **Change the translation style:** `prompts/translate-prompt.md`. **Change the AI image rules:** `prompts/image-prompt.md` and `SENSITIVE_WORDS` in `poster/images.py`. **Change sources, schedule, limits:** `config.yml` and `.github/workflows/draft.yml`.

## Setup (once, about 20 minutes)

### 1. AI keys (free)
- Groq: <https://console.groq.com/keys>
- Gemini: <https://aistudio.google.com/apikey>

- Cloudflare (for AI images; without it every post gets a card): sign up at <https://dash.cloudflare.com>, copy your **Account ID** (on the account home page), and create an API token under *My Profile → API Tokens → Create Token* with the **Workers AI** permission (*Read*). The free daily allowance covers far more images than this pipeline makes.

### 2. LinkedIn token
1. Create an app at <https://www.linkedin.com/developers/apps>. LinkedIn asks you to link a Company Page; any page you admin works (you can create a small one).
2. On the app's **Products** tab, add **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect** (both self-serve).
3. Open the **OAuth token generator** (<https://www.linkedin.com/developers/tools/oauth/token-generator>), pick your app, tick `openid`, `profile` and `w_member_social`, and generate.
4. The token lasts **60 days** and LinkedIn does not let personal apps refresh it. The draft job opens a reminder issue 10 days before it expires, as long as you record the date (step 4).

### 3. Your profile
`profile.yml` holds the facts posts may use about you. It is gitignored; copy `profile.example.yml` if you don't have one. News, fact and "On this day" posts only see the keys in `config.yml` → `general_post_profile_keys`.

### 4. Secrets and variables
```bash
gh secret set GROQ_API_KEY
gh secret set GEMINI_API_KEY
gh secret set LINKEDIN_ACCESS_TOKEN
gh secret set PROFILE_YML < profile.yml
gh secret set CLOUDFLARE_ACCOUNT_ID    # optional: AI images
gh secret set CLOUDFLARE_API_TOKEN
gh variable set LINKEDIN_TOKEN_EXPIRES --body 2027-01-08   # the date 60 days after you generated the token
```
Optional variables: `PUBLISH_MODE` (`simulated` by default, set to `linkedin` to post for real) and `TIMEZONE` (for "today" in prompts and "On this day", and the clock `/schedule` times are read in, e.g. `Europe/London`).

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

## The source link
Links inside a post cut its reach, so the source goes in the first comment. LinkedIn only lets partner apps comment through the API (self-serve apps get a 403), so by default (`post.source_link: manual`) the bot does not try: after `/approve` it comments on the issue with the post link and the line to paste. `auto` makes the bot comment itself if your app has LinkedIn's Community Management API; `off` drops the link.

## Costs and limits
- GitHub Actions: each run takes about a minute; the free plan includes 2,000 minutes a month.
- Hacker News search and Wikipedia (articles and the "On this day" feed): free, no key. Groq and Gemini: free tiers, a few calls per week. Cloudflare Workers AI: free daily allowance, about one image a week. Cards are drawn on the runner, no service at all.
- *Publish scheduled posts* is switched on by `/schedule` and switches itself off when no draft is waiting, so it shows as **disabled** in the Actions tab most of the time. That is normal. While a post is waiting it runs every 15 minutes, about 100 Actions minutes per waiting day; to spend less, make the cron in `.github/workflows/scheduled.yml` less frequent.
- Scheduled runs can start a few minutes late. If a scheduled run never fires, check the Actions tab for a disabled-workflow notice.

## Rule zero
No API keys, tokens or personal details in any committed file. Keys and your profile live only in GitHub secrets; `profile.yml` is gitignored.
