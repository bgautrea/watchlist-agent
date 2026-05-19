# Watchlist Agent — public skeleton (Reddit-touching code)

This is the **public, Reddit-touching subset** of a personal pre-market trading
research pipeline. It is published here for transparency in support of a
Reddit Data API access request under the
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy).

The full project is a private personal codebase. **Only the Reddit-relevant
files are in this repository.** Everything else (market data integrations,
Claude prompts, attribution job, Discord delivery) lives in the private repo
because it isn't relevant to Reddit's review.

---

## Reviewer fast path

If you're reviewing this for a Reddit Data API access request, the three files
that matter are:

| File | What it shows |
|---|---|
| [`src/watchlist/agents/reddit.py`](src/watchlist/agents/reddit.py) | The complete Reddit-touching agent. Read-only PRAW, two-pass ticker extraction with an explicit ambiguous-word blocklist, attention z-score over a 30-day baseline, sentiment classification via a locally hosted SLM (inference only). |
| [`migrations/001_initial_schema.sql`](migrations/001_initial_schema.sql) | The complete SQLite schema. Confirms what data is persisted from Reddit: counts, attention z-scores, and aggregate sentiment labels. **No post bodies, no usernames, no comment text, no per-user data.** |
| [`src/watchlist/schemas.py`](src/watchlist/schemas.py) | The pydantic types that govern in-memory shape of agent outputs. |

---

## What the app does (Reddit-scoped)

- **Runs once per day** (~06:30 US Eastern) on a single Linux host owned by the operator.
- **Authenticates** via PRAW in application-only OAuth (read-only mode — no
  username, no password, no refresh token).
- For each of five subreddits (`r/wallstreetbets`, `r/stocks`, `r/investing`,
  `r/options`, `r/SecurityAnalysis`), fetches the top ~100 posts of the last
  24 hours. Five `top()` calls per run.
- **Extracts ticker mentions** from titles and bodies using two passes:
  1. Cashtag regex (`\$[A-Z]{1,5}\b`) — accepted unconditionally.
  2. Bare uppercase regex matched against the Russell 1000 list, with
     ambiguous English/abbreviation tickers (`A`, `ALL`, `GO`, `NOW`, `US`, …)
     gated on the company name also appearing in the post body. The full
     blocklist is in [`reddit.py`](src/watchlist/agents/reddit.py) — auditable
     and PR-able.
- **Counts mentions per ticker** per subreddit. Computes an attention z-score
  against a 30-day rolling baseline.
- For tickers with z > 1.5, classifies post titles + short excerpts via a
  locally hosted SLM (Ollama, `qwen2.5:7b`) for sentiment
  (`bullish` / `bearish` / `neutral`). **Inference only — no training, no
  fine-tuning, no model weights derived from Reddit content.** The model
  itself is an off-the-shelf open-weights model running entirely on the
  operator's hardware.
- **Persists** to local SQLite: `(run_date, subreddit, ticker, mention_count,
  attention_z, aggregate_sentiment_label)`. No post bodies retained after
  classification returns.

## What the app does NOT do

- No posts, comments, votes, awards, follows, or DMs.
- No interaction with Redditors of any kind.
- No moderator actions.
- No data retention beyond the aggregate counts/scores listed above.
- No re-identification, user profiling, or inference of sensitive attributes.
- No commercialization. The watchlist outputs are private; not sold, shared,
  syndicated, or used as an input to any product or service.
- No AI training, fine-tuning, or RAG indexing of Reddit content.

## Expected volume

~5 subreddits × top ~100 posts each × 1 run/day ≈ **~500 read calls/day**.
PRAW honors `X-Ratelimit-*` headers; well below Reddit's 100 QPM per-OAuth-
client limit.

## Compliance commitments

- Application-only OAuth (no user-scope tokens, ever).
- Dedicated app account (per the Responsible Builder Policy's "no mixed-use
  accounts" rule).
- Single OAuth client, single hardware host, single human operator.
- User-Agent strictly follows the format Reddit requires:
  `linux:com.briangautreau.watchlist:vN.N.N (by /u/<operator-handle>)`.
- If access is revoked, the agent is removed from the pipeline; the rest of
  the watchlist runs without it.

## License

MIT. See [LICENSE](LICENSE).
