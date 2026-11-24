"""Configuration & .env loader for daily-ai-digest."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(env_path: Path | None = None) -> dict[str, str]:
    """Load key=value pairs from .env without overriding existing process env vars."""
    path = env_path or (PROJECT_ROOT / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


@dataclass
class TopicConfig:
    id: str
    name: str
    query: str
    weight: float = 1.0
    x_queries: list[str] = field(default_factory=list)


@dataclass
class DigestConfig:
    site_title: str
    site_subtitle: str
    timezone: str
    site_url: str
    max_items: int
    per_source_max_items: int
    lookback_days: int
    social_max_age_days: int
    dedup_window_days: int
    llm_provider: str
    llm_priority_order: list[str]
    gemini_model: str
    claude_model: str
    claude_fast_model: str
    max_tokens: int
    popularity_weight: float
    importance_weight: float
    sources: dict[str, bool]
    topics: list[TopicConfig]
    importance_boost: list[str]
    importance_penalize: list[str]
    exclude_keywords: list[str]
    glossary_text: str = ""


def load_config(
    config_path: Path | None = None,
    glossary_path: Path | None = None,
) -> DigestConfig:
    """Load topics.yaml + GLOSSARY.md with environment variable overrides."""
    load_dotenv()
    cfg_file = config_path or (PROJECT_ROOT / "config" / "topics.yaml")
    data: dict[str, Any] = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))

    site = data.get("site", {})
    llm = data.get("llm", {})
    ranking = data.get("ranking", {})
    sources = dict(data.get("sources", {}))
    rubric = data.get("importance_rubric", {})

    # Env overrides
    if os.environ.get("ENABLE_XHS", "").lower() in ("1", "true", "yes", "on"):
        sources["xiaohongshu"] = True

    provider = os.environ.get("LLM_PROVIDER", llm.get("provider", "auto")).strip().lower()
    gemini_model = os.environ.get("GEMINI_MODEL", llm.get("gemini_model", "gemini-2.5-flash")).strip()
    claude_model = os.environ.get(
        "ANTHROPIC_MODEL", llm.get("claude_model", "claude-sonnet-5-5")
    ).strip()
    claude_fast_model = os.environ.get(
        "ANTHROPIC_FAST_MODEL", llm.get("claude_fast_model", "claude-haiku-5-5")
    ).strip()
    site_url = os.environ.get("DIGEST_SITE_URL", site.get("site_url", "")).strip()

    topics = [
        TopicConfig(
            id=t["id"],
            name=t["name"],
            query=t["query"],
            weight=float(t.get("weight", 1.0)),
            x_queries=list(t.get("x_queries", [])),
        )
        for t in data.get("topics", [])
    ]

    g_path = glossary_path or (PROJECT_ROOT / "config" / "GLOSSARY.md")
    glossary_text = g_path.read_text(encoding="utf-8") if g_path.exists() else ""

    return DigestConfig(
        site_title=site.get("title", "AI 晨間特刊"),
        site_subtitle=site.get("subtitle", ""),
        timezone=site.get("timezone", "Asia/Taipei"),
        site_url=site_url,
        max_items=int(site.get("max_items", 8)),
        per_source_max_items=int(site.get("per_source_max_items", 5)),
        lookback_days=int(site.get("lookback_days", 2)),
        social_max_age_days=int(site.get("social_max_age_days", 7)),
        dedup_window_days=int(site.get("dedup_window_days", 14)),
        llm_provider=provider,
        llm_priority_order=list(llm.get("priority_order", ["gemini", "claude"])),
        gemini_model=gemini_model,
        claude_model=claude_model,
        claude_fast_model=claude_fast_model,
        max_tokens=int(llm.get("max_tokens", 16384)),
        popularity_weight=float(ranking.get("popularity_weight", 0.45)),
        importance_weight=float(ranking.get("importance_weight", 0.55)),
        sources=sources,
        topics=topics,
        importance_boost=list(rubric.get("boost", [])),
        importance_penalize=list(rubric.get("penalize", [])),
        exclude_keywords=[k.lower() for k in data.get("exclude_keywords", [])],
        glossary_text=glossary_text,
    )
