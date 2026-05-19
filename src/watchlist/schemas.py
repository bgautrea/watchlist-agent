"""Canonical pydantic types for the watchlist pipeline.

Only the types relevant to the Reddit-touching agent are included in this
public skeleton; the other agents' output schemas live in the private repo.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class AgentOutput(BaseModel):
    """One row per (agent, ticker) per run.

    The `score` field is uniformly directional across all agents: higher means
    "more interesting / higher quality." Negative signal (e.g. SEC filing
    risk) flows through `disqualifying` and `risk_flags`, never through an
    inverted score. The Reddit agent never sets `disqualifying`.
    """

    agent: Literal["earnings", "news", "reddit", "filings", "quant", "options"]
    ticker: str
    score: float = Field(ge=0.0, le=1.0)
    signal: str
    disqualifying: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)
    schema_version: int = 1
    timestamp: datetime  # UTC


class RedditDetails(BaseModel):
    """The shape of `AgentOutput.details` when `agent == "reddit"`.

    This is the *complete* set of Reddit-derived data persisted per ticker.
    Note what is NOT here: post bodies, comment text, usernames, post IDs,
    timestamps of individual posts.
    """

    mention_count: int                          # total mentions across scanned subs in the 24h window
    attention_z: float                          # z-score vs 30d rolling mention-count baseline
    per_subreddit_counts: dict[str, int]        # e.g. {"wallstreetbets": 12, "stocks": 3}
    aggregate_sentiment: Literal["bullish", "bearish", "neutral", "n/a"]
    top_post_titles: list[str] = Field(         # titles only (no bodies); for the daily brief readout
        default_factory=list, max_length=3,
    )
