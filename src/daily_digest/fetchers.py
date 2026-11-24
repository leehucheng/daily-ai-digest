"""Multi-SNS fetchers: last30days engine + Apify X + Reddit/HN community comments + dedup."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import certifi

from .config import PROJECT_ROOT, DigestConfig, TopicConfig

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def format_taipei_timestamp(raw: str | int | float | None) -> str:
    """Convert ISO / RFC2822 / unix timestamp to Asia/Taipei 'YYYY-MM-DD HH:MM' (or 'YYYY-MM-DD')."""
    if raw is None or raw == "":
        return ""
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""
    s = str(raw).strip()
    if not s:
        return ""
    # Date-only string YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    # ISO 8601 (with Z or offset)
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    # RFC 2822 / Twitter createdAt e.g. 'Mon Nov 23 05:12:00 +0000 2026'
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s[:16]

LAST30DAYS_SCRIPT = (
    PROJECT_ROOT / ".agents" / "skills" / "last30days" / "scripts" / "last30days.py"
)

AI_SUBREDDITS = [
    ("LocalLLaMA", "models-edge"),
    ("ClaudeAI", "agents"),
    ("MachineLearning", "models-edge"),
    ("OpenAI", "industry"),
    ("artificial", "industry"),
]


@dataclass
class RawItem:
    id: str
    title: str
    url: str
    source: str  # reddit | x | instagram | threads | hackernews | github | arxiv | xiaohongshu
    topic_id: str
    topic_name: str
    published_at: str
    summary: str = ""
    engagement_score: float = 0.0
    engagement_raw: dict[str, Any] = field(default_factory=dict)
    comments: list[dict[str, Any]] = field(default_factory=list)
    discussion_url: str = ""
    popularity_norm: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def is_within_max_age(
    published_at: str,
    max_age_days: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if published_at (YYYY-MM-DD[ HH:MM]) is within max_age_days, or if unparseable."""
    if not published_at or max_age_days <= 0:
        return True
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}):(\d{2}))?", published_at.strip())
    if not m:
        return True
    ref = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    try:
        date_part = m.group(1)
        hh = int(m.group(2)) if m.group(2) else 23
        mm = int(m.group(3)) if m.group(3) else 59
        dt = datetime.strptime(f"{date_part} {hh:02d}:{mm:02d}", "%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI_TZ)
        return (ref - dt) <= timedelta(days=max_age_days)
    except Exception:
        return True


def _canonical_fingerprint(title: str, url: str) -> str:
    norm_url = re.sub(r"[?#].*$", "", (url or "").strip().lower()).rstrip("/")
    norm_title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (title or "").lower())[:64]
    basis = norm_url if norm_url and not norm_url.startswith("http://localhost") else norm_title
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def compute_engagement_value(source: str, eng: dict[str, Any]) -> float:
    """Convert platform-specific engagement metrics into a unified raw engagement score."""
    if not eng:
        return 1.0
    pts = float(eng.get("points") or eng.get("score") or eng.get("upvotes") or eng.get("likes") or 0)
    comments = float(eng.get("comments") or eng.get("num_comments") or eng.get("replies") or 0)
    reposts = float(eng.get("retweets") or eng.get("reposts") or eng.get("shares") or 0)
    stars = float(eng.get("stars") or 0)
    views = float(eng.get("views") or 0)
    # Weight comments heavily because community discussion is core to this digest
    val = pts + comments * 2.5 + reposts * 2.0 + stars * 0.5 + (views * 0.01 if pts == 0 else 0)
    if source in ("x", "instagram", "threads"):
        # Social platforms naturally have larger raw like counts; scale modestly
        val *= 0.65
    return max(1.0, val)


def normalize_popularity_scores(items: list[RawItem]) -> None:
    """Assign 0-100 log-normalized popularity scores across all candidate items."""
    if not items:
        return
    logs = [math.log1p(max(0.0, it.engagement_score)) for it in items]
    lo, hi = min(logs), max(logs)
    span = max(hi - lo, 1e-6)
    for it, lv in zip(items, logs):
        if len(items) == 1 or hi == lo:
            it.popularity_norm = 65.0
        else:
            it.popularity_norm = round(25.0 + 73.0 * ((lv - lo) / span), 1)


def run_last30days_topic(
    topic: TopicConfig,
    cfg: DigestConfig,
    *,
    timeout: int = 120,
) -> tuple[list[RawItem], dict[str, str]]:
    """Run installed last30days v4.6 engine for a single topic and parse schema 1.4 JSON."""
    if not LAST30DAYS_SCRIPT.exists():
        return [], {"last30days": "missing-script"}

    env = os.environ.copy()
    include_sources: list[str] = []
    if env.get("SCRAPECREATORS_API_KEY"):
        env.setdefault("LAST30DAYS_REDDIT_BACKEND", "scrapecreators")
    if cfg.sources.get("threads") and env.get("SCRAPECREATORS_API_KEY"):
        include_sources.append("threads")
    if cfg.sources.get("instagram") and env.get("SCRAPECREATORS_API_KEY"):
        include_sources.append("instagram_comments")
    if include_sources:
        existing = env.get("INCLUDE_SOURCES", "")
        merged = [s for s in (existing.split(",") + include_sources) if s.strip()]
        env["INCLUDE_SOURCES"] = ",".join(dict.fromkeys(merged))

    search_list: list[str] = []
    if cfg.sources.get("reddit"):
        search_list.append("reddit")
    if cfg.sources.get("hackernews"):
        search_list.append("hn")
    if cfg.sources.get("github"):
        search_list.append("github")
    if cfg.sources.get("instagram") and env.get("SCRAPECREATORS_API_KEY"):
        search_list.append("instagram")
    if cfg.sources.get("threads") and env.get("SCRAPECREATORS_API_KEY"):
        search_list.append("threads")
    if cfg.sources.get("xiaohongshu"):
        search_list.append("xhs")

    cmd = [
        sys.executable,
        str(LAST30DAYS_SCRIPT),
        topic.query,
        "--quick",
        f"--days={cfg.lookback_days}",
        "--emit=json",
        "--no-browser-cookies",
    ]
    if search_list:
        cmd.append(f"--search={','.join(search_list)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], {"last30days": "timeout"}

    if proc.returncode != 0 or not proc.stdout.strip():
        return [], {"last30days": f"exit-{proc.returncode}"}

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], {"last30days": "invalid-json"}

    status_map = {str(k): str(v) for k, v in (payload.get("source_status") or {}).items()}
    items: list[RawItem] = []
    for row in payload.get("results", []):
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("candidate_id") or "").strip()
        if not title or not url:
            continue
        source = str(row.get("source") or "web").lower()
        eng = row.get("engagement") or {}
        items.append(
            RawItem(
                id=_canonical_fingerprint(title, url),
                title=title,
                url=url,
                source=source,
                topic_id=topic.id,
                topic_name=topic.name,
                published_at=format_taipei_timestamp(row.get("published_at")),
                summary=str(row.get("summary") or "")[:600],
                engagement_score=compute_engagement_value(source, eng),
                engagement_raw=eng,
                discussion_url=str(row.get("discussion_url") or url),
            )
        )
    return items, status_map


def fetch_reddit_subreddits(
    cfg: DigestConfig,
    *,
    limit_per_sub: int = 6,
    timeout: int = 15,
) -> tuple[list[RawItem], str]:
    """Fetch top 24h posts from key AI subreddits via old.reddit.com Atom RSS (avoids JSON 403)."""
    if not cfg.sources.get("reddit", True):
        return [], "disabled"

    import xml.etree.ElementTree as ET

    topic_map = {t.id: t.name for t in cfg.topics}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    }
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[RawItem] = []
    ok_count = 0

    for sub, default_topic in AI_SUBREDDITS:
        url = f"https://old.reddit.com/r/{sub}/top/.rss?t=day"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
        except Exception:
            continue
        ok_count += 1
        entries = root.findall("atom:entry", ns)[:limit_per_sub]
        for rank_idx, entry in enumerate(entries):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            permalink = (link_el.attrib.get("href", "") if link_el is not None else "").strip()
            updated = format_taipei_timestamp(entry.findtext("atom:updated", default="", namespaces=ns) or "")
            content_html = entry.findtext("atom:content", default="", namespaces=ns) or ""
            plain_summary = re.sub(r"<[^>]+>", " ", content_html)
            plain_summary = re.sub(r"\s+", " ", plain_summary).strip()
            if not title or not permalink:
                continue

            # Extract outbound link if present in RSS HTML body, otherwise keep Reddit thread
            ext_match = re.search(r'<a href="(https?://[^"]+)">\[link\]</a>', content_html)
            ext_url = ext_match.group(1) if ext_match else permalink

            # Fetch top 3 community comments from the thread's .rss feed for top posts
            top_comments: list[dict[str, Any]] = []
            if rank_idx < 3 and len(items) < 12:
                try:
                    c_rss_url = permalink.rstrip("/") + "/.rss?sort=top&limit=6"
                    c_req = urllib.request.Request(c_rss_url, headers=headers)
                    with urllib.request.urlopen(c_req, timeout=10, context=_ssl_ctx()) as c_resp:
                        c_root = ET.fromstring(c_resp.read())
                    for c_entry in c_root.findall("atom:entry", ns)[1:5]:
                        c_author = (
                            c_entry.findtext("atom:author/atom:name", default="", namespaces=ns) or ""
                        ).strip()
                        c_html = c_entry.findtext("atom:content", default="", namespaces=ns) or ""
                        c_txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c_html)).strip()
                        if c_txt and "AutoModerator" not in c_author:
                            top_comments.append(
                                {
                                    "author": c_author or "redditor",
                                    "score": 0,
                                    "text": c_txt[:320],
                                    "url": permalink,
                                }
                            )
                except Exception:
                    pass

            tid = default_topic if default_topic in topic_map else (cfg.topics[0].id if cfg.topics else "general")
            tname = topic_map.get(tid, sub)
            # Rank-derived proxy engagement when RSS omits numeric score (top #1 of day > #6)
            rank_weight = max(40, 260 - rank_idx * 35)
            eng = {
                "rank_in_sub": rank_idx + 1,
                "subreddit": f"r/{sub}",
                "comments_sampled": len(top_comments),
                "points": rank_weight,
            }
            items.append(
                RawItem(
                    id=_canonical_fingerprint(title, permalink),
                    title=title,
                    url=ext_url,
                    source="reddit",
                    topic_id=tid,
                    topic_name=tname,
                    published_at=updated,
                    summary=plain_summary[:500],
                    engagement_score=compute_engagement_value("reddit", eng),
                    engagement_raw=eng,
                    comments=top_comments,
                    discussion_url=permalink,
                )
            )
    return items, ("ok" if ok_count > 0 else ("needs-SCRAPECREATORS_API_KEY" if not os.environ.get("SCRAPECREATORS_API_KEY") else "no-results"))


def fetch_github_ai_repos(cfg: DigestConfig, *, timeout: int = 15) -> tuple[list[RawItem], str]:
    """Fetch fast-rising recent AI repos from the official GitHub Search API."""
    if not cfg.sources.get("github", True):
        return [], "disabled"
    since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    q = f"(llm OR agent OR mcp OR vlm OR ai) created:>={since} stars:>100"
    params = urllib.parse.urlencode({"q": q, "sort": "stars", "order": "desc", "per_page": 8})
    url = f"https://api.github.com/search/repositories?{params}"
    headers = {"User-Agent": "daily-ai-digest/0.1", "Accept": "application/vnd.github+json"}
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return [], "error"

    default_tid = cfg.topics[1].id if len(cfg.topics) > 1 else (cfg.topics[0].id if cfg.topics else "models-edge")
    topic_map = {t.id: t.name for t in cfg.topics}
    items: list[RawItem] = []
    for repo in data.get("items", []):
        full_name = str(repo.get("full_name") or "").strip()
        html_url = str(repo.get("html_url") or "").strip()
        desc = str(repo.get("description") or "").strip()
        if not full_name or not html_url:
            continue
        stars = int(repo.get("stargazers_count") or 0)
        forks = int(repo.get("forks_count") or 0)
        eng = {"stars": stars, "forks": forks, "language": repo.get("language") or ""}
        items.append(
            RawItem(
                id=_canonical_fingerprint(full_name, html_url),
                title=f"GitHub: {full_name} — {desc[:90]}" if desc else f"GitHub: {full_name}",
                url=html_url,
                source="github",
                topic_id=default_tid,
                topic_name=topic_map.get(default_tid, "Open-Source AI"),
                published_at=format_taipei_timestamp(repo.get("pushed_at")),
                summary=desc[:450],
                engagement_score=compute_engagement_value("github", eng),
                engagement_raw=eng,
                discussion_url=html_url,
            )
        )
    return items, ("ok" if items else "no-results")


def fetch_arxiv_ai_papers(cfg: DigestConfig, *, timeout: int = 15) -> tuple[list[RawItem], str]:
    """Fetch latest cs.AI / cs.CL / cs.CV papers from official arXiv Atom API."""
    if not cfg.sources.get("arxiv", True):
        return [], "disabled"
    import xml.etree.ElementTree as ET

    url = (
        "https://export.arxiv.org/api/query?"
        "search_query=cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.CV&sortBy=submittedDate&sortOrder=descending&max_results=6"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "daily-ai-digest/0.1"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            root = ET.fromstring(resp.read())
    except Exception:
        return [], "error"

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    default_tid = cfg.topics[1].id if len(cfg.topics) > 1 else (cfg.topics[0].id if cfg.topics else "models-edge")
    topic_map = {t.id: t.name for t in cfg.topics}
    items: list[RawItem] = []
    for entry in root.findall("atom:entry", ns):
        title = re.sub(r"\s+", " ", (entry.findtext("atom:title", "", ns) or "")).strip()
        summary = re.sub(r"\s+", " ", (entry.findtext("atom:summary", "", ns) or "")).strip()
        paper_url = (entry.findtext("atom:id", "", ns) or "").strip()
        pub = format_taipei_timestamp(entry.findtext("atom:published", "", ns) or "")
        if not title or not paper_url:
            continue
        items.append(
            RawItem(
                id=_canonical_fingerprint(title, paper_url),
                title=f"arXiv: {title}",
                url=paper_url,
                source="arxiv",
                topic_id=default_tid,
                topic_name=topic_map.get(default_tid, "Research"),
                published_at=pub,
                summary=summary[:480],
                engagement_score=55.0,
                engagement_raw={"source": "arxiv"},
                discussion_url=paper_url,
            )
        )
    return items, ("ok" if items else "no-results")


def fetch_hn_ai_stories(cfg: DigestConfig, *, timeout: int = 15) -> tuple[list[RawItem], str]:
    """Fetch top recent AI stories + top comments from Hacker News Algolia API (keyless)."""
    if not cfg.sources.get("hackernews", True):
        return [], "disabled"

    since_ts = int((datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days)).timestamp())
    topic_ids = [t.id for t in cfg.topics]
    t_agents = topic_ids[0] if len(topic_ids) > 0 else "agents"
    t_models = topic_ids[1] if len(topic_ids) > 1 else t_agents
    t_ind = topic_ids[2] if len(topic_ids) > 2 else t_agents

    # Algolia treats spaces as AND, not OR; run crisp single-term AI queries
    queries = [
        ("Claude", t_agents),
        ("Codex", t_agents),
        ("agent", t_agents),
        ("LLM", t_models),
        ("quantization", t_models),
        ("OpenAI", t_ind),
        ("Gemini", t_ind),
        ("Anthropic", t_ind),
        ("DeepSeek", t_ind),
        ("AI", t_ind),
    ]
    topic_map = {t.id: t.name for t in cfg.topics}
    items: list[RawItem] = []
    seen_ids: set[str] = set()

    for q, tid in queries:
        params = urllib.parse.urlencode(
            {
                "query": q,
                "tags": "story",
                "numericFilters": f"created_at_i>{since_ts},points>35",
                "hitsPerPage": 6,
            }
        )
        url = f"https://hn.algolia.com/api/v1/search?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "daily-ai-digest/0.1"})
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        for hit in data.get("hits", []):
            oid = str(hit.get("objectID") or "")
            if not oid or oid in seen_ids:
                continue
            seen_ids.add(oid)
            title = str(hit.get("title") or "").strip()
            story_url = str(hit.get("url") or f"https://news.ycombinator.com/item?id={oid}")
            hn_url = f"https://news.ycombinator.com/item?id={oid}"
            points = int(hit.get("points") or 0)
            num_comments = int(hit.get("num_comments") or 0)
            pub = format_taipei_timestamp(hit.get("created_at"))

            # Pull top 3 HN comments for rich community consensus
            top_comments: list[dict[str, Any]] = []
            if num_comments >= 15 and len(items) < 16:
                try:
                    c_url = f"https://hn.algolia.com/api/v1/items/{oid}"
                    with urllib.request.urlopen(c_url, timeout=10, context=_ssl_ctx()) as c_resp:
                        item_detail = json.loads(c_resp.read().decode("utf-8"))
                    for child in (item_detail.get("children") or [])[:4]:
                        raw_txt = re.sub(r"<[^>]+>", " ", str(child.get("text") or ""))
                        raw_txt = re.sub(r"\s+", " ", raw_txt).strip()
                        if raw_txt and child.get("author"):
                            top_comments.append(
                                {
                                    "author": str(child["author"]),
                                    "score": int(child.get("points") or 0),
                                    "text": raw_txt[:320],
                                    "url": f"https://news.ycombinator.com/item?id={child.get('id', oid)}",
                                }
                            )
                except Exception:
                    pass

            eng = {"points": points, "comments": num_comments}
            items.append(
                RawItem(
                    id=_canonical_fingerprint(title, story_url),
                    title=title,
                    url=story_url,
                    source="hackernews",
                    topic_id=tid,
                    topic_name=topic_map.get(tid, "AI"),
                    published_at=pub,
                    summary="",
                    engagement_score=compute_engagement_value("hackernews", eng),
                    engagement_raw=eng,
                    comments=top_comments,
                    discussion_url=hn_url,
                )
            )
    return items, ("ok" if items else "no-results")


def fetch_scrapecreators_reddit(
    cfg: DigestConfig,
    *,
    timeout: int = 25,
) -> tuple[list[RawItem], str]:
    """Fetch top 24h AI Reddit discussions via ScrapeCreators API (works even when reddit.com 403s)."""
    if not cfg.sources.get("reddit", True):
        return [], "disabled"
    key = os.environ.get("SCRAPECREATORS_API_KEY", "").strip()
    if not key:
        return [], "missing-SCRAPECREATORS_API_KEY"

    topic_ids = [t.id for t in cfg.topics]
    t_agents = topic_ids[0] if len(topic_ids) > 0 else "agents"
    t_models = topic_ids[1] if len(topic_ids) > 1 else t_agents
    t_ind = topic_ids[2] if len(topic_ids) > 2 else t_agents
    topic_map = {t.id: t.name for t in cfg.topics}

    queries = [
        ("Claude Code OR Codex OR AI agent", t_agents),
        ("LocalLLaMA OR open source LLM OR VLM OR quantization", t_models),
        ("OpenAI OR Anthropic OR Gemini OR DeepSeek", t_ind),
    ]
    headers = {"x-api-key": key, "User-Agent": "daily-ai-digest/0.1"}
    items: list[RawItem] = []
    seen_urls: set[str] = set()

    ai_relevance_re = re.compile(
        r"\b(ai|llm|gpt|claude|openai|anthropic|gemini|deepseek|qwen|llama|mistral|vlm|"
        r"agent|mcp|codex|cursor|vllm|quantiz|transformer|diffusion|neural|inference|model)\b",
        re.I,
    )
    ai_sub_allow = {
        "localllama", "claudeai", "openai", "machinelearning", "artificial",
        "singularity", "ai_agents", "chatgpt", "bard", "llmdevs", "ollama",
    }

    for q, tid in queries:
        params = urllib.parse.urlencode({"query": q, "sort": "top", "timeframe": "day"})
        url = f"https://api.scrapecreators.com/v1/reddit/search?{params}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        for post in (data.get("posts") or [])[:12]:
            title = str(post.get("title") or "").strip()
            permalink_raw = str(post.get("permalink") or "").strip()
            if permalink_raw.startswith("/"):
                permalink = f"https://www.reddit.com{permalink_raw}"
            else:
                permalink = str(post.get("url") or permalink_raw).strip()
            if not title or not permalink or permalink in seen_urls:
                continue

            sub = str(post.get("subreddit") or "").strip()
            selftext = str(post.get("selftext") or "")
            # Filter out off-topic posts (e.g. r/GTA) that accidentally match a broad token
            if sub.lower() not in ai_sub_allow and not ai_relevance_re.search(f"{title} {selftext[:200]}"):
                continue
            seen_urls.add(permalink)

            ups = int(post.get("ups") or post.get("score") or 0)
            num_comments = int(post.get("num_comments") or 0)
            if ups < 15 and num_comments < 5:
                continue
            created = format_taipei_timestamp(post.get("created_utc") or post.get("created"))
            ext_url = str(post.get("url") or permalink)
            if not ext_url.startswith("http"):
                ext_url = permalink
            eng = {
                "upvotes": ups,
                "comments": num_comments,
                "subreddit": f"r/{sub}" if sub else "reddit",
            }
            items.append(
                RawItem(
                    id=_canonical_fingerprint(title, permalink),
                    title=f"[r/{sub}] {title}" if sub else title,
                    url=ext_url,
                    source="reddit",
                    topic_id=tid,
                    topic_name=topic_map.get(tid, "Reddit AI"),
                    published_at=created,
                    summary=selftext[:500],
                    engagement_score=compute_engagement_value("reddit", eng),
                    engagement_raw=eng,
                    discussion_url=permalink,
                )
            )

    # Fetch top comments (sorted by ups) for top 6 AI Reddit posts to enrich community consensus
    items.sort(key=lambda x: x.engagement_score, reverse=True)
    for it in items[:6]:
        try:
            c_url = (
                "https://api.scrapecreators.com/v1/reddit/post/comments?"
                + urllib.parse.urlencode({"url": it.discussion_url})
            )
            c_req = urllib.request.Request(c_url, headers=headers)
            with urllib.request.urlopen(c_req, timeout=15, context=_ssl_ctx()) as c_resp:
                c_data = json.loads(c_resp.read().decode("utf-8"))
            raw_cmts = [
                cm for cm in (c_data.get("comments") or [])
                if isinstance(cm, dict)
                and str(cm.get("body") or "").strip()
                and str(cm.get("author") or "") not in ("AutoModerator", "[deleted]", "[removed]")
            ]
            raw_cmts.sort(key=lambda c: int(c.get("ups") or c.get("score") or 0), reverse=True)
            for cm in raw_cmts[:4]:
                body = str(cm.get("body") or "").strip()
                author = str(cm.get("author") or "")
                score = int(cm.get("ups") or cm.get("score") or 0)
                cm_url = str(cm.get("url") or it.discussion_url)
                it.comments.append(
                    {
                        "author": f"u/{author}" if author else "redditor",
                        "score": score,
                        "text": body[:320],
                        "url": cm_url,
                    }
                )
        except Exception:
            continue

    return items, ("ok" if items else "no-results")


def fetch_scrapecreators_threads(
    cfg: DigestConfig,
    *,
    timeout: int = 25,
) -> tuple[list[RawItem], str]:
    """Fetch AI discussions on Threads via ScrapeCreators API."""
    if not cfg.sources.get("threads", True):
        return [], "disabled"
    key = os.environ.get("SCRAPECREATORS_API_KEY", "").strip()
    if not key:
        return [], "missing-SCRAPECREATORS_API_KEY"

    default_topic = cfg.topics[0] if cfg.topics else TopicConfig("industry", "AI", "AI")
    headers = {"x-api-key": key, "User-Agent": "daily-ai-digest/0.1"}
    items: list[RawItem] = []
    seen_urls: set[str] = set()

    for q in ("AI agent", "OpenAI Claude Gemini"):
        url = "https://api.scrapecreators.com/v1/threads/search?" + urllib.parse.urlencode({"query": q})
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        for post in (data.get("posts") or [])[:10]:
            cap = post.get("caption")
            text = cap.get("text", "") if isinstance(cap, dict) else str(cap or post.get("text") or "")
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 20:
                continue
            user = post.get("user") if isinstance(post.get("user"), dict) else {}
            handle = str(user.get("username") or "").strip()
            code = str(post.get("code") or post.get("shortcode") or "").strip()
            post_url = str(post.get("url") or post.get("share_url") or "").strip()
            if not post_url and handle and code:
                post_url = f"https://www.threads.net/@{handle}/post/{code}"
            elif not post_url and code:
                post_url = f"https://www.threads.net/post/{code}"
            if not post_url or post_url in seen_urls:
                continue
            seen_urls.add(post_url)

            likes = int(post.get("like_count") or 0)
            tinfo = post.get("text_post_app_info") if isinstance(post.get("text_post_app_info"), dict) else {}
            replies = int(post.get("reply_count") or tinfo.get("direct_reply_count") or 0)
            reposts = int(post.get("repost_count") or tinfo.get("repost_count") or 0)
            pub = format_taipei_timestamp(
                post.get("taken_at") or post.get("created_at") or post.get("published_at")
            )
            if not is_within_max_age(pub, cfg.social_max_age_days):
                continue
            eng = {"likes": likes, "replies": replies, "reposts": reposts, "author": f"@{handle}" if handle else "threads"}
            items.append(
                RawItem(
                    id=_canonical_fingerprint(text[:80], post_url),
                    title=f"Threads @{handle}: {text[:110]}" if handle else f"Threads: {text[:110]}",
                    url=post_url,
                    source="threads",
                    topic_id=default_topic.id,
                    topic_name=default_topic.name,
                    published_at=pub,
                    summary=text[:500],
                    engagement_score=compute_engagement_value("threads", eng),
                    engagement_raw=eng,
                    discussion_url=post_url,
                )
            )
    return items, ("ok" if items else "no-results")


def fetch_scrapecreators_instagram(
    cfg: DigestConfig,
    *,
    timeout: int = 25,
) -> tuple[list[RawItem], str]:
    """Fetch AI Reels & discussions on Instagram via ScrapeCreators API (single-token queries)."""
    if not cfg.sources.get("instagram", True):
        return [], "disabled"
    key = os.environ.get("SCRAPECREATORS_API_KEY", "").strip()
    if not key:
        return [], "missing-SCRAPECREATORS_API_KEY"

    default_topic = cfg.topics[2] if len(cfg.topics) > 2 else (cfg.topics[0] if cfg.topics else TopicConfig("industry", "AI", "AI"))
    headers = {"x-api-key": key, "User-Agent": "daily-ai-digest/0.1"}
    items: list[RawItem] = []
    seen_urls: set[str] = set()

    # Single-token queries are the stable path for ScrapeCreators /v2/instagram/reels/search
    for q in ("aitools", "openai"):
        url = "https://api.scrapecreators.com/v2/instagram/reels/search?" + urllib.parse.urlencode({"query": q})
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        for reel in (data.get("reels") or [])[:8]:
            cap = reel.get("caption")
            text = cap.get("text", "") if isinstance(cap, dict) else str(cap or "")
            text = re.sub(r"\s+", " ", text).strip()
            shortcode = str(reel.get("shortcode") or "").strip()
            reel_url = str(reel.get("url") or "").strip()
            if not reel_url and shortcode:
                reel_url = f"https://www.instagram.com/reel/{shortcode}/"
            if not reel_url or not text or reel_url in seen_urls:
                continue
            seen_urls.add(reel_url)

            owner = reel.get("owner") if isinstance(reel.get("owner"), dict) else {}
            handle = str(owner.get("username") or "").strip()
            views = int(reel.get("video_view_count") or reel.get("play_count") or 0)
            likes = int(reel.get("like_count") or (reel.get("edge_media_preview_like") or {}).get("count") or 0)
            comments_cnt = int(reel.get("comment_count") or (reel.get("edge_media_to_comment") or {}).get("count") or 0)
            pub = format_taipei_timestamp(
                reel.get("taken_at_timestamp") or reel.get("taken_at") or reel.get("timestamp")
            )
            if not is_within_max_age(pub, cfg.social_max_age_days):
                continue
            eng = {"likes": likes, "comments": comments_cnt, "views": views, "author": f"@{handle}" if handle else "instagram"}
            items.append(
                RawItem(
                    id=_canonical_fingerprint(text[:80], reel_url),
                    title=f"IG @{handle}: {text[:110]}" if handle else f"IG: {text[:110]}",
                    url=reel_url,
                    source="instagram",
                    topic_id=default_topic.id,
                    topic_name=default_topic.name,
                    published_at=pub,
                    summary=text[:500],
                    engagement_score=compute_engagement_value("instagram", eng),
                    engagement_raw=eng,
                    discussion_url=reel_url,
                )
            )
    return items, ("ok" if items else "no-results")


def fetch_x_via_apify(
    cfg: DigestConfig,
    *,
    timeout: int = 180,
) -> tuple[list[RawItem], str]:
    """Fetch X/Twitter AI discussions via Apify Tweet Scraper REST API (no personal cookie needed)."""
    if not cfg.sources.get("x", True):
        return [], "disabled"
    token = os.environ.get("APIFY_API_TOKEN", "").strip()
    if not token:
        return [], "missing-APIFY_API_TOKEN"

    actor = os.environ.get("APIFY_X_ACTOR", "apidojo~tweet-scraper").strip()
    search_terms: list[str] = []
    for topic in cfg.topics:
        search_terms.extend(topic.x_queries or [f"{topic.query} min_faves:50"])
    if not search_terms:
        return [], "no-queries"

    since_date = (datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days)).strftime("%Y-%m-%d")
    url = (
        f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
        f"?token={urllib.parse.quote(token)}&format=json&clean=true&limit=25"
    )
    payload = {
        "searchTerms": [f"{q} since:{since_date}" for q in search_terms[:2]],
        "maxItems": 15,
        "sort": "Top",
        "tweetLanguage": "en",
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return [], f"error:{type(exc).__name__}"

    if not isinstance(rows, list):
        return [], "invalid-response"

    default_topic = cfg.topics[0] if cfg.topics else TopicConfig("industry", "AI", "AI")
    items: list[RawItem] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("noResults"):
            continue
        text = str(row.get("full_text") or row.get("text") or "").strip()
        tweet_url = str(row.get("url") or row.get("twitterUrl") or "").strip()
        if not text or not tweet_url:
            continue
        author = (row.get("author") or {}).get("userName") or row.get("username") or "x_user"
        likes = int(row.get("likeCount") or row.get("favorite_count") or 0)
        retweets = int(row.get("retweetCount") or row.get("retweet_count") or 0)
        replies = int(row.get("replyCount") or row.get("reply_count") or 0)
        eng = {"likes": likes, "retweets": retweets, "replies": replies, "author": f"@{author}"}
        items.append(
            RawItem(
                id=_canonical_fingerprint(text[:80], tweet_url),
                title=f"@{author}: {text[:110]}",
                url=tweet_url,
                source="x",
                topic_id=default_topic.id,
                topic_name=default_topic.name,
                published_at=format_taipei_timestamp(row.get("createdAt") or row.get("created_at")),
                summary=text[:500],
                engagement_score=compute_engagement_value("x", eng),
                engagement_raw=eng,
                discussion_url=tweet_url,
            )
        )
    return items, ("ok" if items else "no-results")


def load_seen_store(seen_path: Path) -> dict[str, dict[str, Any]]:
    if not seen_path.exists():
        return {}
    try:
        data = json.loads(seen_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_seen_store(seen_path: Path, seen: dict[str, dict[str, Any]], *, window_days: int = 14) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    pruned = {
        k: v for k, v in seen.items() if str(v.get("first_seen", "9999-99-99")) >= cutoff
    }
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(
        json.dumps(pruned, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def filter_and_deduplicate(
    items: list[RawItem],
    cfg: DigestConfig,
    seen: dict[str, dict[str, Any]],
) -> list[RawItem]:
    """Exclude noise keywords, merge duplicate IDs across sources, and skip recently covered stories."""
    by_id: dict[str, RawItem] = {}
    for it in items:
        haystack = f"{it.title} {it.summary}".lower()
        if any(bad in haystack for bad in cfg.exclude_keywords):
            continue
        # Drop stale social posts (IG/Threads) older than social_max_age_days
        if it.source in ("instagram", "threads") and not is_within_max_age(
            it.published_at, cfg.social_max_age_days
        ):
            continue
        prev = seen.get(it.id)
        if prev:
            prev_eng = float(prev.get("engagement", 0))
            # Allow resurfacing only if engagement spiked > 3x
            if it.engagement_score < max(prev_eng * 3.0, prev_eng + 200):
                continue
        if it.id in by_id:
            existing = by_id[it.id]
            existing.engagement_score += it.engagement_score
            existing.comments.extend(it.comments)
            if len(it.summary) > len(existing.summary):
                existing.summary = it.summary
        else:
            by_id[it.id] = it

    merged = sorted(by_id.values(), key=lambda x: x.engagement_score, reverse=True)
    normalize_popularity_scores(merged)
    return merged


def collect_all_sources(
    cfg: DigestConfig,
    *,
    seen_path: Path | None = None,
    skip_last30days: bool = False,
) -> tuple[list[RawItem], dict[str, str]]:
    """Run all configured SNS fetchers, deduplicate, and return ranked candidate pool."""
    all_items: list[RawItem] = []
    source_status: dict[str, str] = {}

    if not skip_last30days:
        for topic in cfg.topics:
            t_items, t_status = run_last30days_topic(topic, cfg)
            all_items.extend(t_items)
            for k, v in t_status.items():
                if source_status.get(k) != "ok":
                    source_status[k] = v

    hn_items, hn_st = fetch_hn_ai_stories(cfg)
    all_items.extend(hn_items)
    if hn_st == "ok" or "hackernews" not in source_status:
        source_status["hackernews"] = hn_st

    if os.environ.get("SCRAPECREATORS_API_KEY"):
        sc_red_items, sc_red_st = fetch_scrapecreators_reddit(cfg)
        all_items.extend(sc_red_items)
        if sc_red_st == "ok" or source_status.get("reddit") != "ok":
            source_status["reddit"] = sc_red_st

        sc_th_items, sc_th_st = fetch_scrapecreators_threads(cfg)
        all_items.extend(sc_th_items)
        source_status["threads"] = sc_th_st

        sc_ig_items, sc_ig_st = fetch_scrapecreators_instagram(cfg)
        all_items.extend(sc_ig_items)
        source_status["instagram"] = sc_ig_st
    else:
        reddit_items, reddit_st = fetch_reddit_subreddits(cfg)
        all_items.extend(reddit_items)
        if reddit_st == "ok" or source_status.get("reddit") != "ok":
            source_status["reddit"] = reddit_st

    gh_items, gh_st = fetch_github_ai_repos(cfg)
    all_items.extend(gh_items)
    if gh_st == "ok" or "github" not in source_status:
        source_status["github"] = gh_st

    arxiv_items, arxiv_st = fetch_arxiv_ai_papers(cfg)
    all_items.extend(arxiv_items)
    source_status["arxiv"] = arxiv_st

    x_items, x_st = fetch_x_via_apify(cfg)
    all_items.extend(x_items)
    source_status["x"] = x_st

    if cfg.sources.get("instagram") and not os.environ.get("SCRAPECREATORS_API_KEY"):
        source_status.setdefault("instagram", "missing-SCRAPECREATORS_API_KEY")
    if cfg.sources.get("threads") and not os.environ.get("SCRAPECREATORS_API_KEY"):
        source_status.setdefault("threads", "missing-SCRAPECREATORS_API_KEY")
    source_status["xiaohongshu"] = "enabled" if cfg.sources.get("xiaohongshu") else "optional-off"

    seen = load_seen_store(seen_path) if seen_path else {}
    deduped = filter_and_deduplicate(all_items, cfg, seen)

    # Keep a balanced multi-platform pool (up to 10 per platform, max 70 total) for per-source Top 5 synthesis
    balanced: list[RawItem] = []
    per_source: dict[str, int] = {}
    for it in deduped:
        cnt = per_source.get(it.source, 0)
        if cnt < 10:
            balanced.append(it)
            per_source[it.source] = cnt + 1
        if len(balanced) >= 70:
            break
    return balanced, source_status
