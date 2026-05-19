-- Watchlist Agent — initial SQLite schema (Reddit-relevant subset).
--
-- This is the *full* schema for the daily pipeline; only the rows touching
-- Reddit are commented in detail. Tables for the other agents (earnings,
-- news, filings, quant, options) appear identically in shape but their
-- `details_json` payloads are documented in the private repo.
--
-- Key property for the Reddit reviewer: NOTHING in this schema retains
-- post bodies, comment text, usernames, post IDs, or per-user data sourced
-- from Reddit. Only mention counts, attention z-scores, aggregate sentiment
-- labels, and (in `reddit_mentions`) the matched ticker span are persisted.

-- One row per final pick per run. The Reddit agent does not write here
-- directly; the synthesis agent does, based on combined inputs.
CREATE TABLE runs (
    run_date            TEXT NOT NULL,    -- ET market date
    ticker              TEXT NOT NULL,
    tier                TEXT NOT NULL,    -- "A" | "B" | "C"
    confidence          REAL NOT NULL,
    thesis              TEXT NOT NULL,
    falsifier_json      TEXT NOT NULL,    -- structured Falsifier (no Reddit content)
    catalyst            TEXT,
    discord_message_id  TEXT,             -- nullable; populated after delivery
    created_at          TEXT NOT NULL,    -- UTC ISO-8601
    PRIMARY KEY (run_date, ticker)
);

-- One row per (agent, ticker) per run. Reddit's row has its details_json
-- shaped by `watchlist.schemas.RedditDetails` — i.e. mention_count,
-- attention_z, per_subreddit_counts, aggregate_sentiment, top_post_titles.
-- Bodies are not stored.
CREATE TABLE agent_outputs (
    run_date        TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    agent           TEXT NOT NULL,        -- "reddit" for this agent
    score           REAL NOT NULL,
    signal          TEXT NOT NULL,
    disqualifying   INTEGER NOT NULL DEFAULT 0,
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    details_json    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (run_date, ticker, agent),
    FOREIGN KEY (run_date, ticker) REFERENCES runs(run_date, ticker)
);

-- Per-ticker mention rows for Reddit precision tracking (M3+). Stores the
-- minimum needed to evaluate the blocklist's false-positive rate manually
-- and tune it. Bodies are NOT stored — only the matched span and a hash of
-- the post for de-duplication across multiple subreddits.
CREATE TABLE reddit_mentions (
    run_date         TEXT NOT NULL,
    subreddit        TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    matched_via      TEXT NOT NULL,       -- "cashtag" | "bare_uppercase"
    title_excerpt    TEXT,                -- first 120 chars of the title (no body)
    post_hash        TEXT NOT NULL,       -- sha256(post_id) — opaque, non-reversible
    PRIMARY KEY (run_date, subreddit, ticker, post_hash)
);

-- Daily attribution rollup. Reddit-derived data does not propagate here.
CREATE TABLE attribution (
    run_date              TEXT NOT NULL,
    ticker                TEXT NOT NULL,
    return_1d             REAL,
    return_5d             REAL,
    return_20d            REAL,
    falsifier_triggered   INTEGER,
    updated_at            TEXT NOT NULL,
    PRIMARY KEY (run_date, ticker)
);

-- LLM call accounting (cost visibility). Reddit-related rows correspond to
-- local Ollama inference calls; these have model="ollama:<tag>" and zero
-- cost. No prompt text or response text is persisted here.
CREATE TABLE llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date      TEXT NOT NULL,
    agent         TEXT NOT NULL,
    ticker        TEXT,
    model         TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    created_at    TEXT NOT NULL
);

CREATE INDEX llm_calls_run_agent  ON llm_calls(run_date, agent);
CREATE INDEX llm_calls_run_ticker ON llm_calls(run_date, ticker);
