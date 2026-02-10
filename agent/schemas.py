from __future__ import annotations

import uuid
from pydantic import BaseModel, Field


class EnrichRequest(BaseModel):
    name: str = Field(..., max_length=200)
    company: str = Field(default="", max_length=200)


class GitHubProfile(BaseModel):
    username: str = ""
    url: str = ""
    bio: str = ""
    location: str = ""
    public_repos: int = 0
    followers: int = 0
    top_languages: list[str] = Field(default_factory=list)
    notable_repos: list[str] = Field(default_factory=list)


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


class EnrichResponse(BaseModel):
    success: bool = False
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    profile: EnrichedProfile | None = None
    sources_searched: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0


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
