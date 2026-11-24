"""LLM synthesis: per-source parallel Top 5 + cross-platform Overview Top 8."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import DigestConfig
from .fetchers import RawItem
from .humanizer import humanize_digest_payload
from .llm_client import DualLLMClient

SOURCE_ORDER = [
    ("x", "X"),
    ("reddit", "Reddit"),
    ("threads", "Threads"),
    ("instagram", "IG"),
    ("hackernews", "HN"),
    ("github", "GitHub"),
    ("arxiv", "arXiv"),
    ("xiaohongshu", "小紅書"),
]
SOURCE_LABELS = dict(SOURCE_ORDER)

SYSTEM_PROMPT = """你是一位資深 AI 科技編輯，專門為有實務經驗的 AI/Edge 工程師製作「晨間一頁式情報特刊」。

你必須嚴格遵守以下寫作與編輯規則：
1. **一律使用繁體中文**；模型名、工具名、專有名詞、路徑、程式碼保留英文原文。
2. **ASD-STE100 白話簡化規則（源自 wait-what skill）**：
   - 短句、主動語態、一詞一義。
   - 嚴禁 AI 官腔與空泛形容詞（禁用：顛覆性、革命性、劃時代、無縫整合、賦能、值得一提的是、不容忽視、總而言之、引爆熱議、破折號濫用「——」）。
   - 嚴禁使用任何 Emoji。
3. **寧缺勿濫過濾雜訊**：
   - 剔除明顯無關 AI/科技、純課程行銷廣告、求追蹤引流文或重複轉發廢文；有效項目不足上限就少出，絕不硬湊。
4. **兩層資訊架構**：
   - **L1（一眼掃完）**：
     - `headline_zh`：一句話講清楚「誰做了什麼＋實際改變或影響」（25–48 字，不要新聞聳動標題）。
     - `community_consensus_zh`：一句話提煉社群真實風向（25–50 字；若該則無留言資料，誠實標註「目前以轉發分享為主，尚無顯著正反論戰」）。
   - **L2（點開 `? Wait, what?` 抽屜）**：
     - `wait_what.premise_zh`：退一步補齊前情提要（2–3 句），跳過 `GLOSSARY.md` 中讀者已熟的基礎術語。
     - `wait_what.why_it_matters_to_you_zh`：1–2 句直指對推論延遲/顯存/成本或日常 Agent 工作流的實質意義。
     - `wait_what.glossary_notes`：0–2 個本次出現的新術語白話對照（無則空陣列）。
     - `community_voices`：從原始留言/討論中提煉 0–3 條具代表性的真實聲音（`pro` / `con` / `nuance`），每條附原平台、作者與連結，**不得捏造未在原始資料出現的數據或連結**。
5. **完整溯源**：
   - 每一則必附真實可點擊的 `primary_url`、`sources[]`（每筆含 `platform`、`label`、`published_at`、`url`），以及 `published_at_display`（台灣時間 `YYYY-MM-DD HH:MM` 或 `YYYY-MM-DD`）。絕不捏造或改寫 URL 與時間。
6. **只輸出合法 JSON**，完全符合指定 Schema，不要額外輸出解釋文字。
"""


def build_user_prompt(
    items: list[RawItem],
    cfg: DigestConfig,
    issue_date: str,
    *,
    source_label: str | None = None,
    max_pick: int | None = None,
) -> str:
    limit = max_pick or cfg.max_items
    scope_desc = (
        f"本批次為單一來源【{source_label}】，請從以下 {len(items)} 筆原始資料中挑出最值得看的前 {limit} 則（過濾廣告與非 AI 雜訊，不足 {limit} 則不要硬湊）。"
        if source_label
        else f"請從以下 {len(items)} 筆原始社群候選資料中，合併重複事件並挑出最值得看的前 {limit} 則（不足 {limit} 則不要硬湊）。"
    )
    topics_summary = [{"id": t.id, "name": t.name, "weight": t.weight} for t in cfg.topics]
    compact_items = [
        {
            "id": it.id,
            "source": it.source,
            "topic_id": it.topic_id,
            "title": it.title,
            "url": it.url,
            "discussion_url": it.discussion_url,
            "published_at": it.published_at,
            "popularity_norm": it.popularity_norm,
            "engagement": it.engagement_raw,
            "summary": it.summary[:360],
            "comments": it.comments[:3],
        }
        for it in items
    ]

    schema_spec = {
        "issue_date": issue_date,
        "executive_takeaway_zh": "一句話總結本來源今日最核心的 1 個訊號（≤42字，無Emoji）",
        "top1_diagram": {
            "story_id": "對應你評分最高的第 1 名新聞 id",
            "title_zh": "該則頭條的架構或因果流程圖標題（≤18字）",
            "nodes": [
                {"step": "01 BEFORE", "label_zh": "舊痛點（≤12字）", "detail_zh": "具體瓶頸（≤24字）", "tone": "neutral"},
                {"step": "02 CHANGE", "label_zh": "關鍵改動（≤12字）", "detail_zh": "怎麼改的（≤24字）", "tone": "change"},
                {"step": "03 IMPACT", "label_zh": "實際結果（≤12字）", "detail_zh": "省了什麼或快了多少（≤24字）", "tone": "impact"},
                {"step": "04 DEBATE", "label_zh": "社群保留點（≤12字）", "detail_zh": "限制或反派觀點（≤24字）", "tone": "tension"},
            ],
        },
        "items": [
            {
                "id": "對應原始資料的 id",
                "short_tag": "四象限圖超短標籤（≤10字）",
                "topic_id": "對應 topics 列表中的 id",
                "topic_name": "主題顯示名",
                "published_at_display": "2026-11-23 14:20（台灣時間；無時分則寫 YYYY-MM-DD）",
                "importance_score": 85,
                "popularity_score": 78,
                "rank_reason_zh": "一句話說明為何值得看（≤28字）",
                "headline_zh": "去 AI 腔一句話消息（25-48字）",
                "community_consensus_zh": "一句話社群風向（25-50字）",
                "sentiment_tag": "mostly_positive | mixed | critical | neutral",
                "sources": [
                    {
                        "platform": "hackernews | reddit | x | instagram | threads | github | arxiv",
                        "label": "平台與互動數（如 r/LocalLLaMA 420▲ / 90則留言）",
                        "published_at": "2026-11-23 14:20",
                        "url": "https://...",
                    }
                ],
                "primary_url": "https://...",
                "wait_what": {
                    "premise_zh": "白話前情提要（2-3句，跳過讀者已熟術語）",
                    "why_it_matters_to_you_zh": "對推論/成本/Agent工作流的實際影響（1-2句）",
                    "glossary_notes": [{"term": "新術語", "explain_zh": "白話類比說明（≤28字）"}],
                },
                "community_voices": [
                    {
                        "stance": "pro | con | nuance",
                        "platform": "reddit",
                        "author": "u/example",
                        "quote_zh": "社群高讚觀點白話精煉（≤55字）",
                        "url": "https://...",
                    }
                ],
            }
        ],
    }

    return (
        f"今天日期：{issue_date}\n"
        f"{scope_desc}\n\n"
        f"## 追蹤主題與權重\n{json.dumps(topics_summary, ensure_ascii=False)}\n\n"
        f"## 重要度加分準則\n- " + "\n- ".join(cfg.importance_boost) + "\n"
        f"## 重要度扣分準則\n- " + "\n- ".join(cfg.importance_penalize) + "\n\n"
        f"## 讀者個人術語表 (GLOSSARY.md)\n{cfg.glossary_text}\n\n"
        f"## 輸出 JSON Schema（請嚴格遵守鍵名）\n{json.dumps(schema_spec, ensure_ascii=False, indent=2)}\n\n"
        f"## 原始候選資料\n{json.dumps(compact_items, ensure_ascii=False)}"
    )


def _rank_item_list(items: list[dict[str, Any]], cfg: DigestConfig, limit: int) -> list[dict[str, Any]]:
    topic_weights = {t.id: t.weight for t in cfg.topics}
    for it in items:
        pop = max(0.0, min(100.0, float(it.get("popularity_score", 50))))
        imp = max(0.0, min(100.0, float(it.get("importance_score", 50))))
        w = topic_weights.get(str(it.get("topic_id", "")), 1.0)
        raw_total = pop * cfg.popularity_weight + imp * cfg.importance_weight
        it["popularity_score"] = round(pop)
        it["importance_score"] = round(imp)
        it["composite_score"] = round(min(100.0, raw_total * (0.9 + 0.1 * w)), 1)

    items.sort(key=lambda x: (x.get("composite_score", 0), x.get("importance_score", 0)), reverse=True)
    ranked = items[:limit]
    for idx, it in enumerate(ranked, start=1):
        it["rank"] = idx
    return ranked


def compute_final_ranking(payload: dict[str, Any], cfg: DigestConfig) -> dict[str, Any]:
    """Deterministically rank overview items and every per-source section."""
    payload["items"] = _rank_item_list(list(payload.get("items", [])), cfg, cfg.max_items)
    for sec in payload.get("sections", []):
        if isinstance(sec, dict):
            sec["items"] = _rank_item_list(
                list(sec.get("items", [])), cfg, cfg.per_source_max_items
            )
    return payload


def assemble_overview_from_sections(
    sections: list[dict[str, Any]],
    cfg: DigestConfig,
    issue_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    """Pick cross-platform Overview Top N (with diversity cap) and match winning #1's diagram."""
    pool: list[tuple[str, dict[str, Any]]] = []
    diagram_by_story: dict[str, dict[str, Any]] = {}
    seen_keys: set[str] = set()

    for sec in sections:
        skey = str(sec.get("source", ""))
        diag = sec.get("top_diagram")
        if isinstance(diag, dict) and diag.get("story_id") and diag.get("nodes"):
            diagram_by_story[str(diag["story_id"])] = diag
        for it in sec.get("items", []):
            key = str(it.get("primary_url") or it.get("id") or "")
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            cloned = copy.deepcopy(it)
            cloned["source_section"] = skey
            pool.append((skey, cloned))

    # Score all items on the same scale
    _rank_item_list([it for _, it in pool], cfg, len(pool))
    pool.sort(key=lambda p: (p[1].get("composite_score", 0), p[1].get("importance_score", 0)), reverse=True)

    # Pass 1: cap at 2 per source for balanced cross-SNS Overview; Pass 2: fill remaining by score
    picked: list[dict[str, Any]] = []
    picked_ids: set[str] = set()
    per_src: dict[str, int] = {}
    for skey, it in pool:
        if len(picked) >= cfg.max_items:
            break
        if per_src.get(skey, 0) < 2:
            picked.append(it)
            picked_ids.add(str(it.get("id", "")))
            per_src[skey] = per_src.get(skey, 0) + 1
    for _, it in pool:
        if len(picked) >= cfg.max_items:
            break
        if str(it.get("id", "")) not in picked_ids:
            picked.append(it)
            picked_ids.add(str(it.get("id", "")))

    ranked = _rank_item_list(picked, cfg, cfg.max_items)

    # Pick diagram strictly matching the highest-ranked Overview item that has a diagram
    chosen_diagram: dict[str, Any] | None = None
    for it in ranked:
        sid = str(it.get("id", ""))
        if sid in diagram_by_story:
            chosen_diagram = diagram_by_story[sid]
            break

    top_tags = [str(it.get("short_tag") or "") for it in ranked[:3] if it.get("short_tag")]
    active_labels = [str(sec.get("label", "")) for sec in sections if sec.get("items")]
    takeaway = (
        f"跨 {', '.join(active_labels)} 共 {len(active_labels)} 個來源彙整；"
        f"今日焦點集中在 {'、'.join(top_tags)}。"
        if top_tags
        else f"{issue_date} AI 跨社群情報已更新。"
    )
    return ranked, chosen_diagram, takeaway


def synthesize_digest(
    items: list[RawItem],
    cfg: DigestConfig,
    source_status: dict[str, str],
    *,
    llm_client: DualLLMClient | None = None,
    issue_date: str | None = None,
) -> dict[str, Any]:
    """Synthesize per-source Top <=5 sections in parallel, then assemble Overview Top 8."""
    tz = ZoneInfo(cfg.timezone)
    today = issue_date or datetime.now(tz).strftime("%Y-%m-%d")

    client = llm_client or DualLLMClient(
        provider=cfg.llm_provider,
        priority_order=cfg.llm_priority_order,
        gemini_model=cfg.gemini_model,
        claude_model=cfg.claude_fast_model,
        max_tokens=cfg.max_tokens,
    )

    # Group candidates by source platform
    grouped: dict[str, list[RawItem]] = {}
    for it in items:
        grouped.setdefault(it.source, []).append(it)

    ordered_keys = [k for k, _ in SOURCE_ORDER if grouped.get(k)]
    for k in grouped:
        if k not in ordered_keys:
            ordered_keys.append(k)

    def _run_one(skey: str) -> tuple[str, dict[str, Any], str, str, int]:
        label = SOURCE_LABELS.get(skey, skey.upper())
        batch = grouped[skey][:10]
        prompt = build_user_prompt(
            batch,
            cfg,
            today,
            source_label=label,
            max_pick=cfg.per_source_max_items,
        )
        resp = client.complete(SYSTEM_PROMPT, prompt, expect_json=True)
        if not isinstance(resp.parsed_json, dict):
            raise ValueError(f"Non-object JSON for source {skey}")
        raw_sec = resp.parsed_json
        sec_items = _rank_item_list(list(raw_sec.get("items", [])), cfg, cfg.per_source_max_items)
        for it in sec_items:
            it["source_section"] = skey
        sec_obj = {
            "source": skey,
            "label": label,
            "takeaway_zh": str(raw_sec.get("executive_takeaway_zh", "")),
            "top_diagram": raw_sec.get("top1_diagram"),
            "items": sec_items,
        }
        return skey, sec_obj, resp.provider, resp.model, len(resp.failover_log or [])

    results_by_source: dict[str, dict[str, Any]] = {}
    used_provider = ""
    used_model = ""
    failover_total = 0
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(ordered_keys)))) as pool:
        futures = {pool.submit(_run_one, skey): skey for skey in ordered_keys}
        for fut in as_completed(futures):
            skey = futures[fut]
            try:
                k, sec_obj, prov, mod, fo = fut.result()
                results_by_source[k] = sec_obj
                used_provider = prov
                used_model = mod
                failover_total += fo
            except Exception as exc:
                errors.append(f"{skey}:{type(exc).__name__}")

    sections = [results_by_source[k] for k in ordered_keys if k in results_by_source]
    if not sections:
        raise RuntimeError(f"All per-source synthesis calls failed: {', '.join(errors)}")

    overview_items, overview_diagram, overview_takeaway = assemble_overview_from_sections(
        sections, cfg, today
    )

    payload: dict[str, Any] = {
        "issue_date": today,
        "executive_takeaway_zh": overview_takeaway,
        "top1_diagram": overview_diagram,
        "items": overview_items,
        "sections": sections,
        "meta": {
            "generated_at": datetime.now(tz).isoformat(timespec="minutes"),
            "llm_provider": used_provider,
            "llm_model": used_model,
            "failover_count": failover_total,
            "source_status": source_status,
            "section_errors": errors,
            "raw_candidate_count": len(items),
            "total_curated_count": sum(len(s.get("items", [])) for s in sections),
        },
    }
    return humanize_digest_payload(payload)
