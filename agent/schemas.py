from __future__ import annotations

import uuid
from pydantic import BaseModel, Field


class EnrichRequest(BaseModel):
    name: str = Field(..., max_length=200)
    company: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    output_format: str = Field(default="structured", pattern="^(structured|narrative|both)$")
    use_case: str = Field(default="sales", pattern="^(sales|recruiting|job_search)$")


class GitHubProfile(BaseModel):
    username: str = ""
    url: str = ""
    bio: str = ""
    location: str = ""
    public_repos: int = 0
    followers: int = 0
    top_languages: list[str] = Field(default_factory=list)
    notable_repos: list[str] = Field(default_factory=list)
    recent_stars: list[str] = Field(default_factory=list)
    recent_activity_summary: str = ""
    activity_level: str = ""


class Finding(BaseModel):
    fact: str = ""
    source: str = ""


class ConfidenceScores(BaseModel):
    name: float = 0.0
    company: float = 0.0
    role: float = 0.0
    location: float = 0.0
    email: float = 0.0
    bio: float = 0.0
    github: float = 0.0
    linkedin_url: float = 0.0
    twitter_handle: float = 0.0
    recent_news: float = 0.0


class FieldFreshness(BaseModel):
    field: str = ""
    last_confirmed: str = ""
    source_type: str = ""
    freshness_score: float = 1.0


class EnrichedProfile(BaseModel):
    name: str = ""
    company: str = ""
    role: str = ""
    location: str = ""
    email: str = ""
    bio: str = ""
    education: list[str] = Field(default_factory=list)
    previous_companies: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    github: GitHubProfile | None = None
    linkedin_url: str = ""
    linkedin_summary: str = ""
    website: str = ""
    notable_achievements: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: ConfidenceScores = Field(default_factory=ConfidenceScores)
    findings: list[Finding] = Field(default_factory=list)
    disambiguation_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    candidates_found: int = Field(default=1, ge=0)
    disambiguation_signals: list[str] = Field(default_factory=list)
    freshness: list[FieldFreshness] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    recent_news: list[str] = Field(default_factory=list)
    twitter_handle: str = ""
    twitter_bio: str = ""
    community_highlights: list[str] = Field(default_factory=list)
    media_appearances: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)


class EnrichResponse(BaseModel):
    success: bool = False
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    profile: EnrichedProfile | None = None
    sources_searched: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    narrative: str = ""
    talking_points: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    tool_name: str = ""
    raw_data: str = ""
    urls: list[str] = Field(default_factory=list)
    success: bool = False
    error: str = ""
    latency_ms: float = 0.0


class PlannerDecision(BaseModel):
    tools_to_run: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    urls_to_scrape: list[str] = Field(default_factory=list)
    reasoning: str = ""
