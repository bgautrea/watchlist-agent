"""Reddit attention agent.

Read-only Reddit reader for a personal, non-commercial pre-market trading
research pipeline. Counts ticker mentions in the top posts of five finance-
adjacent subreddits, computes a 30-day attention z-score, and classifies
sentiment on high-attention names via a locally hosted SLM.

Compliance commitments (also in README.md):

- Application-only OAuth, read-only. No username/password. No refresh token.
- No posts, comments, votes, awards, follows, or DMs.
- No retention of post bodies, comment text, usernames, or per-user data.
- No re-identification or sensitive-attribute inference.
- LLM use is local-only inference for sentiment labeling; no training, no
  fine-tuning, no model weights derived from Reddit content.
- No commercialization or downstream redistribution.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Iterable

import praw

from watchlist.schemas import AgentOutput, RedditDetails

# -- Configuration -----------------------------------------------------------

SUBREDDITS: tuple[str, ...] = (
    "wallstreetbets",
    "stocks",
    "investing",
    "options",
    "SecurityAnalysis",
)

TOP_POSTS_PER_SUB: int = 100
TIME_FILTER: str = "day"  # PRAW: last 24h

# Tickers that are also common English words or abbreviations. A bare
# uppercase match for one of these is dropped UNLESS the company's full name
# (or a configured alias) also appears in the post body. The list is curated
# manually and is intended to be exhaustive enough to keep false-positive
# rate under ~10% on a labeled sample.
AMBIGUOUS_TICKER_BLOCKLIST: frozenset[str] = frozenset({
    "A", "ALL", "ANY", "ARE", "AS", "AT", "BE", "BIG", "BY", "CAN",
    "CEO", "CFO", "EOD", "EOM", "EOY", "EPS", "EV", "FOR", "GO", "HE",
    "I", "IF", "IN", "IS", "IT", "KEY", "LOW", "NEW", "NO", "NOW",
    "ON", "ONE", "OR", "OUT", "PUT", "R", "REAL", "SEE", "SO", "T",
    "THE", "TWO", "UP", "US", "V", "WE", "X", "Y", "YES",
})

CASHTAG_RE: re.Pattern[str] = re.compile(r"\$([A-Z]{1,5})\b")
BARE_TICKER_RE: re.Pattern[str] = re.compile(r"\b[A-Z]{1,5}\b")

ATTENTION_Z_THRESHOLD: float = 1.5  # tickers above this go to the SLM


# -- Agent -------------------------------------------------------------------


class RedditAgent:
    """Daily Reddit attention scanner."""

    def __init__(
        self,
        universe: dict[str, str],
        baseline_counts: dict[str, list[int]],
    ) -> None:
        """
        Parameters
        ----------
        universe:
            Mapping of ticker -> canonical company name (e.g., {"AAPL": "Apple Inc."}).
            Used to (a) gate ambiguous-blocklist tickers on company-name
            co-occurrence and (b) limit attention to the Russell 1000.
        baseline_counts:
            Mapping of ticker -> list of the last 30 days' mention counts.
            Used to compute the attention z-score. Maintained by the
            orchestrator; this agent does not read or write it directly.
        """
        self._universe = universe
        self._baseline = baseline_counts
        self._reddit = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
        )
        assert self._reddit.read_only, "PRAW did not enter read-only mode"

    # -- Public entry point --------------------------------------------------

    def run(self) -> list[AgentOutput]:
        """Pull, count, score, and (where warranted) classify sentiment.

        Returns one `AgentOutput` per ticker that received any mention during
        the 24h window. The orchestrator filters/ranks downstream.
        """
        per_sub_counts: dict[str, Counter[str]] = {}
        top_titles_by_ticker: dict[str, list[str]] = defaultdict(list)

        for sub in SUBREDDITS:
            counts, titles = self._scan_subreddit(sub)
            per_sub_counts[sub] = counts
            for ticker, title_list in titles.items():
                top_titles_by_ticker[ticker].extend(title_list)

        outputs: list[AgentOutput] = []
        total_counts: Counter[str] = sum(per_sub_counts.values(), Counter())
        for ticker, count in total_counts.items():
            z = self._attention_z(ticker, count)
            sentiment = "n/a"
            if z >= ATTENTION_Z_THRESHOLD:
                sentiment = self._classify_sentiment(
                    titles=top_titles_by_ticker[ticker][:3],
                )

            details = RedditDetails(
                mention_count=count,
                attention_z=z,
                per_subreddit_counts={s: c[ticker] for s, c in per_sub_counts.items() if c[ticker] > 0},
                aggregate_sentiment=sentiment,
                top_post_titles=top_titles_by_ticker[ticker][:3],
            )
            outputs.append(
                AgentOutput(
                    agent="reddit",
                    ticker=ticker,
                    score=_score_from(z=z, sentiment=sentiment),
                    signal=_one_line_signal(count=count, z=z, sentiment=sentiment),
                    details=details.model_dump(),
                    timestamp=datetime.now(UTC),
                )
            )
        return outputs

    # -- Internals -----------------------------------------------------------

    def _scan_subreddit(self, sub: str) -> tuple[Counter[str], dict[str, list[str]]]:
        """Return per-ticker mention counts and title-per-ticker for this sub."""
        counts: Counter[str] = Counter()
        titles_for_ticker: dict[str, list[str]] = defaultdict(list)

        for post in self._reddit.subreddit(sub).top(time_filter=TIME_FILTER, limit=TOP_POSTS_PER_SUB):
            title = post.title or ""
            body = post.selftext or ""
            tickers = self._extract_tickers(title=title, body=body)
            for ticker in tickers:
                counts[ticker] += 1
                if len(titles_for_ticker[ticker]) < 3:
                    titles_for_ticker[ticker].append(title)

        return counts, titles_for_ticker

    def _extract_tickers(self, *, title: str, body: str) -> set[str]:
        """Two-pass extraction. See README.md for rationale.

        Pass A: `$TICKER` cashtags always count.
        Pass B: bare uppercase words match against the universe; if on the
                ambiguity blocklist, also require the company name (or any
                alias) to appear in title + body.

        Post bodies are read here only to gate ambiguous matches and are
        discarded when this function returns; nothing about the post body
        is persisted.
        """
        text = f"{title}\n{body}"
        found: set[str] = set()

        # Pass A: cashtags
        for m in CASHTAG_RE.finditer(text):
            ticker = m.group(1)
            if ticker in self._universe:
                found.add(ticker)

        # Pass B: bare uppercase
        haystack_lower = text.lower()
        for m in BARE_TICKER_RE.finditer(text):
            ticker = m.group(0)
            if ticker not in self._universe:
                continue
            if ticker in AMBIGUOUS_TICKER_BLOCKLIST:
                company_name = self._universe[ticker].lower()
                # Crude alias: first whitespace-token of the company name
                # (e.g., "Apple Inc." -> "apple"). The private repo's
                # universe loader keeps a more complete alias map; this
                # public skeleton uses just the first word for brevity.
                alias = company_name.split()[0] if company_name else ""
                if not alias or alias not in haystack_lower:
                    continue
            found.add(ticker)

        return found

    def _attention_z(self, ticker: str, today_count: int) -> float:
        history = self._baseline.get(ticker, [])
        if len(history) < 7:
            return 0.0
        mean = sum(history) / len(history)
        var = sum((x - mean) ** 2 for x in history) / len(history)
        std = var ** 0.5
        if std == 0.0:
            return 0.0
        return (today_count - mean) / std

    def _classify_sentiment(self, titles: Iterable[str]) -> str:
        """Classify aggregate sentiment for one ticker via local Ollama.

        Only post *titles* are sent to the local SLM. Bodies are not sent.
        This is inference only against an off-the-shelf open-weights model
        running on the operator's hardware; no training, no fine-tuning,
        no model weights derived from Reddit content.

        Returns one of: "bullish", "bearish", "neutral", "n/a".
        """
        # Stubbed in the public skeleton. The private repo's implementation
        # calls the local Ollama HTTP API and parses the JSON response.
        raise NotImplementedError("Ollama inference is implemented in the private repo.")


# -- Helpers -----------------------------------------------------------------


def _score_from(*, z: float, sentiment: str) -> float:
    """Map z-score and sentiment to the uniform 0..1 'interestingness' scale.

    The synthesis agent in the private repo treats high WSB attention on
    liquid large-caps as contrarian; that policy lives there, not here.
    """
    base = min(max(z / 4.0, 0.0), 1.0)
    if sentiment == "bullish":
        return min(base + 0.1, 1.0)
    if sentiment == "bearish":
        return max(base - 0.1, 0.0)
    return base


def _one_line_signal(*, count: int, z: float, sentiment: str) -> str:
    return f"{count} mentions, z={z:+.2f}, sentiment={sentiment}"
