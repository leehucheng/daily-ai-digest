"""Unit tests for daily-ai-digest pipeline (offline, deterministic)."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from daily_digest.config import load_config
from daily_digest.diagram_svg import render_quadrant_svg, render_top1_flow_svg
from daily_digest.fetchers import (
    RawItem,
    compute_engagement_value,
    filter_and_deduplicate,
    load_seen_store,
    normalize_popularity_scores,
    save_seen_store,
)
from daily_digest.humanizer import humanize_digest_payload, humanize_text
from daily_digest.llm_client import DualLLMClient, LLMError, extract_json
from daily_digest.notify import build_gmail_html, build_ntfy_payload, send_gmail, send_ntfy
from daily_digest.render_html import render_digest_html, write_digest_site
from daily_digest.synthesizer import compute_final_ranking


def _sample_digest() -> dict:
    return {
        "issue_date": "2026-11-23",
        "executive_takeaway_zh": "開源推論引擎本週聚焦低記憶體 KV-cache 與多 Agent 工具協議。",
        "top1_diagram": {
            "title_zh": "KV-Cache 壓縮實測脈絡",
            "nodes": [
                {"step": "01 BEFORE", "label_zh": "長文顯存爆掉", "detail_zh": "70B需雙高階卡", "tone": "neutral"},
                {"step": "02 CHANGE", "label_zh": "4-bit KV壓縮", "detail_zh": "動態分組量化", "tone": "change"},
                {"step": "03 IMPACT", "label_zh": "單卡順跑", "detail_zh": "延遲增幅低於5%", "tone": "impact"},
                {"step": "04 DEBATE", "label_zh": "長脈絡召回", "detail_zh": "社群實測極端長文微降", "tone": "tension"},
            ],
        },
        "items": [
            {
                "id": "story-a",
                "short_tag": "KV量化實測",
                "topic_id": "models-edge",
                "topic_name": "Open-Source LLMs / VLM / Edge AI",
                "importance_score": 92,
                "popularity_score": 85,
                "rank_reason_zh": "顯存減半且已有開源實測數據",
                "headline_zh": "開源社群推出 4-bit KV-cache 壓縮補丁，單張消費級顯卡可跑 70B 模型。",
                "community_consensus_zh": "7 成工程師實測省顯存有感；3 成質疑 64K 以上長文精度退化。",
                "sentiment_tag": "mostly_positive",
                "sources": [
                    {"platform": "reddit", "label": "r/LocalLLaMA 640▲", "url": "https://reddit.com/r/LocalLLaMA/1"},
                    {"platform": "hackernews", "label": "HN 310pts", "url": "https://news.ycombinator.com/item?id=1"},
                ],
                "primary_url": "https://github.com/example/kv-quant",
                "wait_what": {
                    "premise_zh": "過去 70B 模型推論瓶頸常卡在長對話累積的記憶體，而不是模型權重本身。",
                    "why_it_matters_to_you_zh": "部署成本可少租一張 GPU，對你的高併發服務直接省錢。",
                    "glossary_notes": [
                        {"term": "Needle-in-a-Haystack", "explain_zh": "長文章大海撈針測試，用來檢查壓縮後有沒有忘記細節"}
                    ],
                },
                "community_voices": [
                    {
                        "stance": "pro",
                        "platform": "reddit",
                        "author": "u/gpu_tuner",
                        "quote_zh": "RTX 4090 實測 32K 上下文速度幾乎沒掉，終於不用租雙卡。",
                        "url": "https://reddit.com/r/LocalLLaMA/1",
                    },
                    {
                        "stance": "con",
                        "platform": "hackernews",
                        "author": "ml_skeptic",
                        "quote_zh": "寫程式碼超過 50K tokens 時變數名偶爾會記錯，生產環境建議先壓測。",
                        "url": "https://news.ycombinator.com/item?id=1",
                    },
                ],
            },
            {
                "id": "story-b",
                "short_tag": "Agent權限沙盒",
                "topic_id": "agents",
                "topic_name": "AI Agents & Coding Tools",
                "importance_score": 84,
                "popularity_score": 78,
                "rank_reason_zh": "直接改善 Coding Agent 本機安全邊界",
                "headline_zh": "主流 Coding Agent 新增宣告式檔案白名單，降低背景指令誤刪風險。",
                "community_consensus_zh": "多數開發者認可方向，但抱怨設定檔過於繁瑣。",
                "sentiment_tag": "mixed",
                "sources": [{"platform": "x", "label": "X 1.8k likes", "url": "https://x.com/example/status/1"}],
                "primary_url": "https://x.com/example/status/1",
                "wait_what": {
                    "premise_zh": "背景自主跑的 Agent 若開全權限容易誤改系統檔，開發者一直在找折衷方案。",
                    "why_it_matters_to_you_zh": "跟你的 harness guard 概念相同，但直接內建在 CLI 層。",
                    "glossary_notes": [],
                },
                "community_voices": [],
            },
        ],
        "meta": {
            "generated_at": "2026-11-23T07:30+08:00",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
            "failover_log": [],
            "source_status": {"reddit": "ok", "hackernews": "ok", "x": "ok"},
            "raw_candidate_count": 20,
        },
    }


def test_dual_llm_failover_gemini_429_to_claude():
    client = DualLLMClient(
        provider="auto",
        priority_order=["gemini", "claude"],
        gemini_api_key="gem-key",
        anthropic_api_key="ant-key",
    )

    def fake_post(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
        if "generativelanguage.googleapis.com" in url:
            raise urllib.error.HTTPError(
                url, 429, "Too Many Requests", {}, io.BytesIO(b"quota exceeded")
            )
        assert "api.anthropic.com" in url
        return {"content": [{"type": "text", "text": '```json\n{"ok": true}\n```'}]}

    with patch("daily_digest.llm_client._http_post_json", side_effect=fake_post):
        resp = client.complete("sys", "user", expect_json=True)

    assert resp.provider == "claude"
    assert resp.parsed_json == {"ok": True}
    assert resp.failover_log and "429" in resp.failover_log[0]


def test_dual_llm_uses_claude_when_only_claude_key_set():
    client = DualLLMClient(
        provider="auto",
        priority_order=["gemini", "claude"],
        gemini_api_key="",
        anthropic_api_key="ant-key",
    )
    assert client.available_providers() == ["claude"]

    with patch(
        "daily_digest.llm_client._http_post_json",
        return_value={"content": [{"type": "text", "text": '{"picked": "claude"}'}]},
    ):
        resp = client.complete("sys", "user", expect_json=True)

    assert resp.provider == "claude"
    assert resp.parsed_json == {"picked": "claude"}


def test_dual_llm_raises_when_no_keys():
    client = DualLLMClient(provider="auto", gemini_api_key="", anthropic_api_key="")
    with pytest.raises(LLMError):
        client.complete("sys", "user")


def test_extract_json_handles_fences_and_noise():
    raw = 'Here is the output:\n```json\n{"a": 1, "b": [2, 3]}\n```\nDone.'
    assert extract_json(raw) == {"a": 1, "b": [2, 3]}


def test_humanizer_strips_slop_and_invisible_chars():
    dirty = "值得一提的是，這項顛覆性的技術\u200b無縫整合了推論引擎——引爆社群熱議。"
    clean = humanize_text(dirty)
    assert "\u200b" not in clean
    assert "值得一提的是" not in clean
    assert "顛覆性的" not in clean
    assert "——" not in clean
    assert "直接整合" in clean


def test_dedup_and_resurface_on_3x_spike(tmp_path: Path):
    cfg = load_config()
    seen_file = tmp_path / "seen.json"
    save_seen_store(
        seen_file,
        {
            "old-flat": {"first_seen": "2026-11-22", "engagement": 100.0},
            "old-spike": {"first_seen": "2026-11-22", "engagement": 100.0},
        },
        window_days=14,
    )
    seen = load_seen_store(seen_file)
    items = [
        RawItem("old-flat", "Same news", "https://a.com/1", "reddit", "agents", "Agents", "2026-11-23", engagement_score=150.0),
        RawItem("old-spike", "Exploding news", "https://a.com/2", "hackernews", "agents", "Agents", "2026-11-23", engagement_score=450.0),
        RawItem("spam", "Sponsored crypto airdrop", "https://a.com/3", "x", "agents", "Agents", "2026-11-23", engagement_score=999.0),
        RawItem("fresh", "Brand new model", "https://a.com/4", "github", "models-edge", "Models", "2026-11-23", engagement_score=200.0),
    ]
    out = filter_and_deduplicate(items, cfg, seen)
    ids = [x.id for x in out]
    assert "old-flat" not in ids
    assert "spam" not in ids
    assert ids == ["old-spike", "fresh"]
    assert all(25.0 <= x.popularity_norm <= 100.0 for x in out)


def test_engagement_and_ranking():
    assert compute_engagement_value("hackernews", {"points": 200, "comments": 100}) == 450.0
    items = [
        RawItem("1", "a", "https://a", "hn", "agents", "A", "2026-11-23", engagement_score=50),
        RawItem("2", "b", "https://b", "hn", "agents", "A", "2026-11-23", engagement_score=500),
    ]
    normalize_popularity_scores(items)
    assert items[1].popularity_norm > items[0].popularity_norm

    cfg = load_config()
    d = _sample_digest()
    d["items"].reverse()
    ranked = compute_final_ranking(d, cfg)
    assert ranked["items"][0]["id"] == "story-a"
    assert ranked["items"][0]["rank"] == 1


def test_svg_and_html_render_and_archive(tmp_path: Path):
    cfg = load_config()
    d = humanize_digest_payload(compute_final_ranking(_sample_digest(), cfg))

    q_svg = render_quadrant_svg(d["items"])
    assert "<svg" in q_svg and 'data-story-index="0"' in q_svg
    f_svg = render_top1_flow_svg(d["top1_diagram"])
    assert "01 BEFORE" in f_svg and "04 DEBATE" in f_svg

    html = render_digest_html(d, cfg)
    assert "Wait, what?" in html
    assert "社群風向" in html
    assert "thumb-bar" in html  # Mobile thumb bar for Pixel 10 Pro
    assert "quadrant-svg" in html
    assert "javascript:" not in html

    idx, arch = write_digest_site(d, cfg, tmp_path / "docs")
    assert idx.exists() and arch.exists()
    arch_index = json.loads((tmp_path / "docs" / "archive" / "index.json").read_text(encoding="utf-8"))
    assert arch_index["latest"] == "2026-11-23"


def test_notify_dry_run_and_payloads():
    cfg = load_config()
    d = compute_final_ranking(_sample_digest(), cfg)
    title, body, click = build_ntfy_payload(d, cfg)
    assert "AI 晨間特刊" in title
    assert "風向：" in body
    assert click.startswith("https://")

    res_ntfy = send_ntfy(d, cfg, topic="test-topic", dry_run=True)
    assert res_ntfy["status"] == "dry-run"

    subj, plain, html = build_gmail_html(d, cfg)
    assert "KV量化實測" in subj
    assert "社群風向" in plain and "打開手機一頁式特刊" in html

    res_mail = send_gmail(d, cfg, app_password="dummy", dry_run=True)
    assert res_mail["status"] == "dry-run"


def test_gemini_key_sent_in_header_not_url():
    client = DualLLMClient(provider="gemini", gemini_api_key="secret-gem-123")
    captured: dict = {}

    def fake_post(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
        captured["url"] = url
        captured["headers"] = headers
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}

    with patch("daily_digest.llm_client._http_post_json", side_effect=fake_post):
        resp = client.complete("sys", "user", expect_json=True)

    assert resp.parsed_json == {"ok": 1}
    assert "secret-gem-123" not in captured["url"]
    assert captured["headers"].get("x-goog-api-key") == "secret-gem-123"


def test_diagram_svg_escaping_at_slice_boundary_produces_valid_xml():
    import xml.etree.ElementTree as ET

    diagram = {
        "story_id": "story-a",
        "title_zh": "R&D <test> & edge",
        "nodes": [
            {
                "step": "01",
                "label_zh": "A&B <C>",
                "detail_zh": "1234567890123&4567890<123>",
                "tone": "change",
            }
        ],
    }
    svg = render_top1_flow_svg(diagram)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")


def test_diagram_binds_to_story_id_after_reranking():
    cfg = load_config()
    d = _sample_digest()
    # Bind diagram explicitly to story-b (ranked #2), ensure it renders inside story-b, not story-a (#1)
    d["top1_diagram"]["story_id"] = "story-b"
    ranked = compute_final_ranking(d, cfg)
    assert ranked["items"][0]["id"] == "story-a"
    assert ranked["items"][1]["id"] == "story-b"

    html = render_digest_html(ranked, cfg)
    card0_pos = html.index('id="story-0"')
    card1_pos = html.index('id="story-1"')
    flow_pos = html.index("flow-svg")
    assert flow_pos > card1_pos > card0_pos


def test_send_ntfy_live_mode_uses_utf8_json_and_ascii_headers():
    cfg = load_config()
    d = compute_final_ranking(_sample_digest(), cfg)
    captured_req = {}

    class DummyResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=20, context=None):
        captured_req["url"] = req.full_url
        captured_req["headers"] = dict(req.header_items())
        captured_req["data"] = req.data
        return DummyResp()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        res = send_ntfy(d, cfg, topic="my-secret-topic", dry_run=False)

    assert res["status"] == "sent"
    # Every HTTP header key/value must be pure latin-1/ASCII
    for k, v in captured_req["headers"].items():
        k.encode("latin-1")
        v.encode("latin-1")
    body = json.loads(captured_req["data"].decode("utf-8"))
    assert body["topic"] == "my-secret-topic"
    assert "AI 晨間特刊" in body["title"]
    assert body["actions"][0]["label"] == "打開特刊"


def test_format_taipei_timestamp_and_source_links_in_html():
    from daily_digest.fetchers import format_taipei_timestamp

    assert format_taipei_timestamp("2026-11-23T06:20:00Z") == "2026-11-23 14:20"
    assert format_taipei_timestamp("Mon Nov 23 10:01:26 +0000 2026") == "2026-11-23 18:01"
    assert format_taipei_timestamp("2026-11-22") == "2026-11-22"

    cfg = load_config()
    d = _sample_digest()
    d["items"][0]["published_at_display"] = "2026-11-23 14:20"
    d["items"][0]["sources"][0]["published_at"] = "2026-11-23 14:20"
    html = render_digest_html(compute_final_ranking(d, cfg), cfg)
    assert "2026-11-23 14:20" in html
    assert "資料來源與原始連結" in html
    assert "https://github.com/example/kv-quant" in html
    assert "https://reddit.com/r/LocalLLaMA/1" in html


def test_per_source_sections_and_overview_assembly_and_tabs():
    from daily_digest.synthesizer import assemble_overview_from_sections

    cfg = load_config()
    base = _sample_digest()
    sec_x = {
        "source": "x",
        "label": "X",
        "takeaway_zh": "X 焦點",
        "top_diagram": {
            "story_id": "story-a",
            "title_zh": "X 頭條架構",
            "nodes": [{"step": "01", "label_zh": "A", "detail_zh": "detail", "tone": "change"}],
        },
        "items": [base["items"][0]],
    }
    sec_reddit = {
        "source": "reddit",
        "label": "Reddit",
        "takeaway_zh": "Reddit 焦點",
        "top_diagram": None,
        "items": [base["items"][1]],
    }
    overview_items, diag, takeaway = assemble_overview_from_sections([sec_x, sec_reddit], cfg, "2026-11-23")
    assert len(overview_items) == 2
    assert overview_items[0]["id"] == "story-a"
    assert diag is not None and diag["story_id"] == "story-a"
    assert "X" in takeaway and "Reddit" in takeaway

    full_digest = {
        "issue_date": "2026-11-23",
        "executive_takeaway_zh": takeaway,
        "top1_diagram": diag,
        "items": overview_items,
        "sections": [sec_x, sec_reddit],
        "meta": base["meta"],
    }
    html = render_digest_html(full_digest, cfg)
    assert 'data-tab="overview"' in html
    assert 'data-tab="x"' in html
    assert 'data-tab="reddit"' in html
    assert 'id="drawer-overview-0"' in html
    assert 'id="drawer-x-0"' in html
    assert 'id="drawer-reddit-0"' in html


def test_social_recency_filter_drops_old_ig_and_threads():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from daily_digest.fetchers import is_within_max_age

    now = datetime(2026, 11, 24, 12, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert is_within_max_age("2026-11-23 18:01", 7, now=now) is True
    assert is_within_max_age("2026-11-18 00:00", 7, now=now) is True
    assert is_within_max_age("2026-10-29 23:00", 7, now=now) is False
    assert is_within_max_age("2026-09-30 01:14", 7, now=now) is False

    cfg = load_config()
    items = [
        RawItem("ig-old", "Old IG", "https://instagram.com/reel/old/", "instagram", "industry", "AI", "2026-09-30 01:14", engagement_score=500),
        RawItem("th-old", "Old Threads", "https://threads.net/post/old", "threads", "agents", "AI", "2026-10-29 23:00", engagement_score=400),
        RawItem("ig-new", "Fresh IG", "https://instagram.com/reel/new/", "instagram", "industry", "AI", "2026-11-23 10:00", engagement_score=100),
        RawItem("hn-new", "Fresh HN", "https://news.ycombinator.com/item?id=99", "hackernews", "agents", "AI", "2026-11-23 11:00", engagement_score=120),
    ]
    out = filter_and_deduplicate(items, cfg, {})
    ids = [x.id for x in out]
    assert "ig-old" not in ids
    assert "th-old" not in ids
    assert "ig-new" in ids and "hn-new" in ids


def test_archive_pages_can_navigate_back_and_forth_between_dates(tmp_path: Path):
    cfg = load_config()
    docs_dir = tmp_path / "docs"

    d1 = compute_final_ranking(_sample_digest(), cfg)
    d1["issue_date"] = "2026-11-23"
    write_digest_site(d1, cfg, docs_dir)

    d2 = compute_final_ranking(_sample_digest(), cfg)
    d2["issue_date"] = "2026-11-25"
    write_digest_site(d2, cfg, docs_dir)

    old_html = (docs_dir / "archive" / "2026-11-23.html").read_text(encoding="utf-8")
    new_html = (docs_dir / "index.html").read_text(encoding="utf-8")

    # Previously archived 2026-11-23 page must include 2026-11-25 option and keep 2026-11-23 selected
    assert 'value="2026-11-25.html"' in old_html
    assert 'value="2026-11-23.html" selected' in old_html
    assert 'data-current-date="2026-11-23"' in old_html
    # Runtime fetch hydration to ./index.json for future dates
    assert "./index.json" in old_html
    assert 'value="archive/2026-11-23.html"' in new_html
    assert 'data-current-date="2026-11-25"' in new_html
