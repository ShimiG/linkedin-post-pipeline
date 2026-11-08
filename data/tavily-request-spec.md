# Tavily news search (Workflow A, `TAVILY NEWS SEARCH`)

Native Tavily n8n node (verified community node), operation Search, credential `Tavily`.

- Runs only when the topic row has `search_news` checked.
- Query: `news_query` if set, otherwise `topic`.
- Options: Topic `news` · Time Range `NEWS_TIME_RANGE` (CONFIG, default `month`) · Search Depth `basic` · Max Results `NEWS_MAX_RESULTS` (default 5) · Raw Content off.
- Node settings: Retry On Fail 3x · On Error: continue. A Tavily failure lands in `NORMALIZE NEWS` as an item with `.error` → `news_count = 0`, the run continues and the reviewer sees a warning.

## Response shape
Top level: `query, results[], response_time`. Each result: `url, title, content, score (0-1), published_date` (populated only with topic `news`).

## Filtering (`NORMALIZE NEWS`)
Tavily never returns an empty list, so relevance is computed: keep a result only if `score >= NEWS_MIN_SCORE` (default 0.5) and its title or content contains at least one meaningful token of the query. Kept items are stored as `{title, source (hostname), date (ISO), snippet (400 chars), url, score}`.
