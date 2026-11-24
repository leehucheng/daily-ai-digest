"""Mobile-first editorial HTML renderer (minimalist-ui + diagram-design aesthetics).

Supports Overview Top 8 + per-source Top <=5 tabs (X, Reddit, Threads, IG, HN, GitHub, arXiv).
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import DigestConfig
from .diagram_svg import render_quadrant_svg, render_top1_flow_svg

_SENTIMENT_MAP = {
    "mostly_positive": ("多數叫好", "pill-green"),
    "mixed": ("正反分歧", "pill-yellow"),
    "critical": ("多數質疑", "pill-red"),
    "neutral": ("資訊觀察", "pill-blue"),
}

_STANCE_MAP = {
    "pro": ("支持", "pill-green"),
    "con": ("質疑", "pill-red"),
    "nuance": ("實測", "pill-blue"),
}

_PLATFORM_LABELS = {
    "hackernews": "HN",
    "reddit": "Reddit",
    "x": "X",
    "instagram": "IG",
    "threads": "Threads",
    "github": "GitHub",
    "arxiv": "arXiv",
    "techmeme": "Techmeme",
    "xiaohongshu": "小紅書",
}


def _safe_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith(("https://", "http://")):
        return escape(u, quote=True)
    return "#"


def _render_card_html(
    item: dict[str, Any],
    index: int,
    total: int,
    *,
    tab_key: str,
    top1_flow_svg: str = "",
) -> str:
    rank = int(item.get("rank", index + 1))
    tid = escape(str(item.get("topic_id", "general")))
    tname = escape(str(item.get("topic_name", "AI")))
    pub_display = escape(str(item.get("published_at_display") or item.get("published_at") or ""))
    imp = int(item.get("importance_score", 50))
    pop = int(item.get("popularity_score", 50))
    comp = float(item.get("composite_score", 50.0))
    headline = escape(str(item.get("headline_zh", "")))
    consensus = escape(str(item.get("community_consensus_zh", "")))
    rank_reason = escape(str(item.get("rank_reason_zh", "")))
    sentiment_key = str(item.get("sentiment_tag", "neutral"))
    sent_label, sent_cls = _SENTIMENT_MAP.get(sentiment_key, ("資訊觀察", "pill-blue"))
    primary_url = _safe_url(str(item.get("primary_url", "")))

    ext_icon = (
        '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">'
        '<path d="M3.5 8.5L8.5 3.5M8.5 3.5H4.5M8.5 3.5V7.5" stroke="currentColor" '
        'stroke-width="1.4" stroke-linecap="square"/></svg>'
    )
    source_rows: list[str] = []
    seen_urls: set[str] = set()
    for src in item.get("sources", [])[:6]:
        plat_raw = str(src.get("platform", "")).lower()
        plat = escape(_PLATFORM_LABELS.get(plat_raw, plat_raw.upper() or "WEB"))
        lbl = escape(str(src.get("label") or plat))
        src_time = escape(str(src.get("published_at") or ""))
        surl = _safe_url(str(src.get("url") or primary_url))
        if surl != "#":
            seen_urls.add(surl)
        time_html = f'<span class="source-time">{src_time}</span>' if src_time else ""
        source_rows.append(
            f'<a class="source-link-item" href="{surl}" target="_blank" rel="noopener noreferrer">'
            f'<span class="source-plat">{plat}</span>'
            f'<span class="source-label">{lbl}</span>'
            f"{time_html}"
            f'<span class="source-arrow">{ext_icon}</span>'
            f"</a>"
        )
    if primary_url != "#" and primary_url not in seen_urls:
        host = escape(
            urlparse(str(item.get("primary_url", ""))).netloc.removeprefix("www.") or "原文連結"
        )
        source_rows.insert(
            0,
            f'<a class="source-link-item is-primary-src" href="{primary_url}" target="_blank" rel="noopener noreferrer">'
            f'<span class="source-plat">原文</span>'
            f'<span class="source-label">{host}</span>'
            f'{f"<span class=\"source-time\">{pub_display}</span>" if pub_display else ""}'
            f'<span class="source-arrow">{ext_icon}</span>'
            f"</a>",
        )
    if not source_rows and primary_url != "#":
        source_rows.append(
            f'<a class="source-link-item" href="{primary_url}" target="_blank" rel="noopener noreferrer">'
            f'<span class="source-plat">來源</span><span class="source-label">開啟原文</span>'
            f'<span class="source-arrow">{ext_icon}</span></a>'
        )

    ww = item.get("wait_what") or {}
    premise = escape(str(ww.get("premise_zh", "")))
    matters = escape(str(ww.get("why_it_matters_to_you_zh", "")))
    glossary_items = []
    for gn in ww.get("glossary_notes") or []:
        term = escape(str(gn.get("term", "")))
        exp = escape(str(gn.get("explain_zh", "")))
        if term and exp:
            glossary_items.append(
                f'<li><span class="gloss-term">{term}</span><span class="gloss-sep">：</span>{exp}</li>'
            )
    glossary_html = (
        f'<div class="drawer-block"><div class="drawer-kicker">新詞快查（對照 GLOSSARY）</div>'
        f'<ul class="gloss-list">{"".join(glossary_items)}</ul></div>'
        if glossary_items
        else ""
    )

    voices_html_parts: list[str] = []
    for v in item.get("community_voices") or []:
        st_lbl, st_cls = _STANCE_MAP.get(str(v.get("stance", "nuance")), ("觀點", "pill-blue"))
        plat = escape(
            _PLATFORM_LABELS.get(str(v.get("platform", "")).lower(), str(v.get("platform", "")))
        )
        author = escape(str(v.get("author", "")))
        quote = escape(str(v.get("quote_zh", "")))
        vurl = _safe_url(str(v.get("url") or primary_url))
        byline = f"{plat} · {author}" if author else plat
        voices_html_parts.append(
            f'<li class="voice-item">'
            f'<div class="voice-head"><span class="pill {st_cls}">{st_lbl}</span>'
            f'<a class="voice-meta" href="{vurl}" target="_blank" rel="noopener noreferrer">{byline}</a></div>'
            f'<p class="voice-quote">{quote}</p>'
            f"</li>"
        )
    voices_block = (
        f'<div class="drawer-block"><div class="drawer-kicker">社群代表聲音</div>'
        f'<ul class="voice-list">{"".join(voices_html_parts)}</ul></div>'
        if voices_html_parts
        else ""
    )

    diagram_block = f'<div class="top1-diagram-wrap">{top1_flow_svg}</div>' if top1_flow_svg else ""
    active_cls = " is-active" if index == 0 else ""
    safe_tab = escape(tab_key)
    card_dom_id = f"story-{index}" if tab_key == "overview" else f"story-{safe_tab}-{index}"
    drawer_dom_id = f"drawer-{safe_tab}-{index}"

    return f"""<article class="story-card{active_cls}" id="{card_dom_id}" data-index="{index}" data-topic="{tid}">
  <header class="card-meta-row">
    <div class="card-rank-group">
      <span class="rank-num">#{rank:02d}</span>
      <span class="topic-label">{tname}</span>
      {f'<span class="pub-time-badge">{pub_display}</span>' if pub_display else ''}
    </div>
    <div class="score-strip" title="綜合排序分 {comp:.1f}">
      <span>重要 <strong>{imp}</strong></span>
      <span class="dot-sep">·</span>
      <span>熱門 <strong>{pop}</strong></span>
    </div>
  </header>

  <h2 class="card-headline">{headline}</h2>
  {f'<p class="rank-reason">{rank_reason}</p>' if rank_reason else ''}

  <section class="consensus-box" aria-label="社群討論風向">
    <div class="consensus-head">
      <span class="consensus-kicker">社群風向</span>
      <span class="pill {sent_cls}">{sent_label}</span>
    </div>
    <p class="consensus-text">{consensus}</p>
  </section>

  {diagram_block}

  <section class="sources-panel" aria-label="資料來源與原始連結">
    <div class="sources-kicker">資料來源與原始連結</div>
    <div class="sources-list">{"".join(source_rows)}</div>
  </section>

  <div class="waitwhat-wrap">
    <button class="waitwhat-toggle" type="button" aria-expanded="false" aria-controls="{drawer_dom_id}">
      <span class="waitwhat-label">Wait, what? 補前情提要與社群正反留言</span>
      <span class="toggle-sign" aria-hidden="true">+</span>
    </button>
    <div class="waitwhat-drawer" id="{drawer_dom_id}" hidden>
      {f'<div class="drawer-block"><div class="drawer-kicker">前情提要（白話脈絡）</div><p class="drawer-text">{premise}</p></div>' if premise else ''}
      {f'<div class="drawer-block"><div class="drawer-kicker">對你的意義</div><p class="drawer-text">{matters}</p></div>' if matters else ''}
      {glossary_html}
      {voices_block}
    </div>
  </div>

  <div class="card-footer-counter">{index + 1} / {total}</div>
</article>"""


def _render_tab_panel(
    tab_key: str,
    items: list[dict[str, Any]],
    diagram_spec: dict[str, Any] | None,
    *,
    section_takeaway: str = "",
    is_default: bool = False,
) -> str:
    quadrant_svg = render_quadrant_svg(items) if items else ""
    top_svg = render_top1_flow_svg(diagram_spec) if diagram_spec else ""
    diagram_target_id = str((diagram_spec or {}).get("story_id") or "").strip()

    diagram_idx = 0
    if diagram_target_id:
        matched = [i for i, it in enumerate(items) if str(it.get("id", "")) == diagram_target_id]
        diagram_idx = matched[0] if matched else -1

    cards_html = "\n".join(
        _render_card_html(
            it,
            i,
            len(items),
            tab_key=tab_key,
            top1_flow_svg=top_svg if i == diagram_idx else "",
        )
        for i, it in enumerate(items)
    )

    takeaway_html = (
        f'<p class="section-takeaway">{escape(section_takeaway)}</p>'
        if (section_takeaway and tab_key != "overview")
        else ""
    )
    quad_html = (
        f'<section class="quadrant-panel" aria-label="重要度與熱門度四象限">'
        f'<div class="panel-head"><span class="panel-kicker">SIGNAL MAP</span>'
        f'<span class="panel-hint">點圓點直達該則卡片</span></div>'
        f"{quadrant_svg}</section>"
        if quadrant_svg
        else ""
    )
    active_cls = " is-active" if is_default else ""
    hidden_attr = "" if is_default else " hidden"
    empty_html = '<div class="empty-state">本來源今日無符合條件的新消息。</div>'
    return (
        f'<div class="tab-panel{active_cls}" data-tab-panel="{escape(tab_key)}"{hidden_attr}>'
        f"{takeaway_html}"
        f"{quad_html}"
        f'<section class="cards-stage" aria-live="polite">'
        f"{cards_html if cards_html else empty_html}"
        f"</section></div>"
    )


def render_digest_html(
    digest: dict[str, Any],
    cfg: DigestConfig,
    *,
    archive_dates: list[str] | None = None,
    is_archive_page: bool = False,
) -> str:
    """Render a standalone mobile-first + desktop-responsive editorial HTML page."""
    issue_date = escape(str(digest.get("issue_date", "")))
    takeaway = escape(str(digest.get("executive_takeaway_zh", "")))
    items: list[dict[str, Any]] = list(digest.get("items", []))
    sections: list[dict[str, Any]] = [
        s for s in (digest.get("sections") or []) if isinstance(s, dict) and s.get("items")
    ]
    meta = digest.get("meta") or {}
    generated_at = escape(str(meta.get("generated_at", "")))
    llm_provider = escape(str(meta.get("llm_provider", "unknown")))
    llm_model = escape(str(meta.get("llm_model", "")))
    source_status = meta.get("source_status") or {}

    # Platform tabs (Overview + each active source Top <=5)
    tab_buttons = [
        f'<button class="source-tab is-active" type="button" data-tab="overview" role="tab" aria-selected="true">'
        f'總覽 <span class="tab-count">{len(items)}</span></button>'
    ]
    tab_panels = [
        _render_tab_panel(
            "overview",
            items,
            digest.get("top1_diagram"),
            is_default=True,
        )
    ]
    for sec in sections:
        skey = str(sec.get("source", ""))
        slabel = escape(str(sec.get("label") or _PLATFORM_LABELS.get(skey, skey.upper())))
        sitems = list(sec.get("items", []))
        tab_buttons.append(
            f'<button class="source-tab" type="button" data-tab="{escape(skey)}" role="tab" aria-selected="false">'
            f'{slabel} <span class="tab-count">{len(sitems)}</span></button>'
        )
        tab_panels.append(
            _render_tab_panel(
                skey,
                sitems,
                sec.get("top_diagram"),
                section_takeaway=str(sec.get("takeaway_zh", "")),
                is_default=False,
            )
        )

    # Topic filter pills (shown inside Overview tab)
    seen_topics: list[tuple[str, str]] = []
    for it in items:
        pair = (str(it.get("topic_id", "general")), str(it.get("topic_name", "AI")))
        if pair not in seen_topics:
            seen_topics.append(pair)
    topic_pills = ['<button class="filter-pill is-active" type="button" data-filter="all">全部主題</button>']
    for tid, tname in seen_topics:
        topic_pills.append(
            f'<button class="filter-pill" type="button" data-filter="{escape(tid)}">{escape(tname)}</button>'
        )

    # Archive selector
    dates = archive_dates or ([str(digest.get("issue_date", ""))] if digest.get("issue_date") else [])
    archive_opts = []
    for d in dates[:30]:
        href = f"{d}.html" if is_archive_page else f"archive/{d}.html"
        sel = " selected" if d == str(digest.get("issue_date", "")) else ""
        archive_opts.append(f'<option value="{escape(href)}"{sel}>{escape(d)}</option>')
    archive_select = (
        f'<select class="archive-select" id="archiveSelect" aria-label="切換歷史期數">'
        f'{"".join(archive_opts)}</select>'
        if archive_opts
        else ""
    )

    status_badges = []
    for src, st in sorted(source_status.items()):
        lbl = _PLATFORM_LABELS.get(src, src)
        cls = "st-ok" if st == "ok" else "st-muted"
        status_badges.append(f'<span class="status-tag {cls}">{escape(lbl)}: {escape(str(st))}</span>')

    title_text = escape(f"{cfg.site_title} · {issue_date}")
    home_href = "../index.html" if is_archive_page else "./index.html"
    source_tabs_html = (
        f'<nav class="source-tabs" id="sourceTabs" role="tablist" aria-label="切換資料來源分頁">'
        f'{"".join(tab_buttons)}</nav>'
        if len(tab_buttons) > 1
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#F7F6F3" />
  <title>{title_text}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Newsreader:opsz,wght@6..72,500;6..72,600&family=Noto+Sans+TC:wght@400;500;600;700&family=Noto+Serif+TC:wght@600;700&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --canvas: #F7F6F3;
      --surface: #FFFFFF;
      --surface-soft: #FAF8F5;
      --border: #EAEAEA;
      --border-strong: #D6D3CD;
      --ink: #111111;
      --body: #2F3437;
      --muted: #787774;
      --red-bg: #FDEBEC; --red-fg: #9F2F2D;
      --blue-bg: #E1F3FE; --blue-fg: #1F6C9F;
      --green-bg: #EDF3EC; --green-fg: #346538;
      --yellow-bg: #FBF3DB; --yellow-fg: #956400;
      --font-serif: 'Newsreader', 'Noto Serif TC', Georgia, serif;
      --font-sans: 'SF Pro Display', 'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      padding: 0;
      background: var(--canvas);
      color: var(--body);
      font-family: var(--font-sans);
      -webkit-font-smoothing: antialiased;
      line-height: 1.65;
    }}
    .progress-track {{
      position: fixed;
      top: 0; left: 0; right: 0;
      height: 2px;
      background: var(--border);
      z-index: 40;
    }}
    .progress-fill {{
      height: 100%;
      width: 0%;
      background: var(--ink);
      transition: width 220ms cubic-bezier(0.16, 1, 0.3, 1);
    }}
    .page-shell {{
      max-width: 880px;
      margin: 0 auto;
      padding: 20px 16px 104px;
    }}
    @media (min-width: 768px) {{
      .page-shell {{ padding: 48px 28px 80px; }}
    }}
    .masthead {{
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
      margin-bottom: 14px;
    }}
    .masthead-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }}
    .brand-kicker {{
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      text-decoration: none;
    }}
    .masthead-controls {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .archive-select {{
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--body);
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 5px 8px;
    }}
    .mode-switch {{
      display: inline-flex;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 2px;
    }}
    .mode-btn {{
      border: 0;
      background: transparent;
      color: var(--muted);
      font-family: var(--font-sans);
      font-size: 12px;
      font-weight: 500;
      padding: 4px 10px;
      border-radius: 4px;
      cursor: pointer;
    }}
    .mode-btn.is-active {{
      background: var(--ink);
      color: #FFFFFF;
    }}
    .masthead-title {{
      margin: 0 0 8px;
      font-family: var(--font-serif);
      font-size: clamp(26px, 5vw, 38px);
      line-height: 1.15;
      letter-spacing: -0.02em;
      color: var(--ink);
    }}
    .takeaway {{
      margin: 0;
      font-size: 15px;
      color: var(--body);
      max-width: 68ch;
    }}

    /* Platform source tabs (Overview + per-source Top 5) */
    .source-tabs {{
      display: flex;
      align-items: center;
      gap: 6px;
      overflow-x: auto;
      padding: 4px 0 12px;
      margin-bottom: 8px;
      border-bottom: 1px solid var(--border);
      scrollbar-width: none;
    }}
    .source-tabs::-webkit-scrollbar {{ display: none; }}
    .source-tab {{
      flex: 0 0 auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--body);
      border-radius: 6px;
      font-family: var(--font-sans);
      font-size: 13px;
      font-weight: 600;
      padding: 7px 12px;
      cursor: pointer;
    }}
    .source-tab.is-active {{
      background: var(--ink);
      border-color: var(--ink);
      color: #FFFFFF;
    }}
    .tab-count {{
      font-family: var(--font-mono);
      font-size: 11px;
      opacity: 0.72;
    }}
    .section-takeaway {{
      margin: 0 0 14px;
      font-size: 14px;
      color: var(--muted);
    }}

    .filter-bar {{
      display: flex;
      align-items: center;
      gap: 8px;
      overflow-x: auto;
      padding: 4px 0 14px;
      scrollbar-width: none;
    }}
    .filter-bar::-webkit-scrollbar {{ display: none; }}
    .filter-bar[hidden] {{ display: none; }}
    .filter-pill {{
      flex: 0 0 auto;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--muted);
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 500;
      padding: 5px 12px;
      cursor: pointer;
    }}
    .filter-pill.is-active {{
      background: var(--ink);
      border-color: var(--ink);
      color: #FFFFFF;
    }}
    .quadrant-panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 20px;
    }}
    .panel-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .panel-kicker {{
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .panel-hint {{
      font-size: 12px;
      color: var(--muted);
    }}
    .editorial-svg {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .q-dot {{ cursor: pointer; }}
    .q-dot:hover circle {{ stroke-width: 2.5; }}

    .cards-stage {{
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .story-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px 18px;
      transition: box-shadow 200ms ease;
    }}
    @media (min-width: 768px) {{
      .story-card {{ padding: 26px 28px; }}
      .story-card:hover {{ box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04); }}
    }}
    .card-meta-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .card-rank-group {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .rank-num {{
      font-family: var(--font-mono);
      font-size: 13px;
      font-weight: 600;
      color: var(--ink);
    }}
    .topic-label {{
      font-size: 12px;
      color: var(--muted);
    }}
    .pub-time-badge {{
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
      background: var(--canvas);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1px 6px;
    }}
    .score-strip {{
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--muted);
    }}
    .score-strip strong {{ color: var(--ink); font-weight: 600; }}
    .dot-sep {{ margin: 0 4px; }}
    .card-headline {{
      margin: 0 0 8px;
      font-family: var(--font-serif);
      font-size: clamp(20px, 4.5vw, 24px);
      line-height: 1.32;
      letter-spacing: -0.015em;
      color: var(--ink);
      font-weight: 600;
    }}
    .rank-reason {{
      margin: 0 0 14px;
      font-size: 13px;
      color: var(--muted);
    }}
    .consensus-box {{
      background: var(--surface-soft);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 14px;
    }}
    .consensus-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }}
    .consensus-kicker {{
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.06em;
      color: var(--muted);
    }}
    .consensus-text {{
      margin: 0;
      font-size: 14.5px;
      color: var(--body);
    }}
    .pill {{
      display: inline-block;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.04em;
      padding: 2px 9px;
      line-height: 1.5;
    }}
    .pill-green {{ background: var(--green-bg); color: var(--green-fg); }}
    .pill-yellow {{ background: var(--yellow-bg); color: var(--yellow-fg); }}
    .pill-red {{ background: var(--red-bg); color: var(--red-fg); }}
    .pill-blue {{ background: var(--blue-bg); color: var(--blue-fg); }}

    .top1-diagram-wrap {{ margin: 14px 0; }}
    .sources-panel {{ margin: 12px 0; }}
    .sources-kicker {{
      font-family: var(--font-mono);
      font-size: 10.5px;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .sources-list {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .source-link-item {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 7px 10px;
      background: var(--canvas);
      border: 1px solid var(--border);
      border-radius: 6px;
      text-decoration: none;
      color: var(--body);
      font-size: 12.5px;
      transition: border-color 150ms ease, background 150ms ease;
    }}
    .source-link-item:hover {{
      border-color: var(--border-strong);
      background: var(--surface-soft);
    }}
    .source-link-item.is-primary-src {{
      background: var(--surface);
      border-color: var(--border-strong);
    }}
    .source-plat {{
      flex: 0 0 auto;
      font-family: var(--font-mono);
      font-size: 10.5px;
      font-weight: 600;
      color: var(--ink);
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1px 6px;
    }}
    .source-label {{
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 500;
    }}
    .source-time {{
      flex: 0 0 auto;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
    }}
    .source-arrow {{
      flex: 0 0 auto;
      display: inline-flex;
      color: var(--muted);
    }}

    .waitwhat-wrap {{
      border-top: 1px solid var(--border);
      margin-top: 10px;
      padding-top: 8px;
    }}
    .waitwhat-toggle {{
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: transparent;
      border: 0;
      padding: 8px 0;
      cursor: pointer;
      color: var(--ink);
      font-family: var(--font-sans);
      font-size: 13.5px;
      font-weight: 600;
      text-align: left;
    }}
    .toggle-sign {{
      font-family: var(--font-mono);
      font-size: 18px;
      font-weight: 400;
      color: var(--muted);
      line-height: 1;
    }}
    .waitwhat-drawer {{ padding: 8px 0 4px; }}
    .drawer-block {{ padding: 10px 0; border-top: 1px dashed var(--border); }}
    .drawer-block:first-child {{ border-top: 0; padding-top: 2px; }}
    .drawer-kicker {{
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin-bottom: 5px;
    }}
    .drawer-text {{ margin: 0; font-size: 14px; color: var(--body); }}
    .gloss-list, .voice-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .gloss-list li {{ font-size: 13.5px; }}
    .gloss-term {{ font-family: var(--font-mono); font-weight: 600; color: var(--ink); }}
    .voice-item {{
      padding: 8px 10px;
      background: var(--surface-soft);
      border: 1px solid var(--border);
      border-radius: 6px;
    }}
    .voice-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 4px;
    }}
    .voice-meta {{
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
      text-decoration: none;
    }}
    .voice-meta:hover {{ text-decoration: underline; }}
    .voice-quote {{ margin: 0; font-size: 13.5px; color: var(--body); }}
    .card-footer-counter {{
      display: none;
      margin-top: 12px;
      text-align: right;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
    }}

    /* Focus single-card mode (default on mobile) */
    body.mode-focus .quadrant-panel {{ display: none; }}
    body.mode-focus .quadrant-panel.is-open {{ display: block; }}
    body.mode-focus .story-card {{ display: none; }}
    body.mode-focus .story-card.is-active {{ display: block; }}
    body.mode-focus .card-footer-counter {{ display: block; }}

    .thumb-bar {{
      position: fixed;
      left: 0; right: 0; bottom: 0;
      z-index: 30;
      background: rgba(247, 246, 243, 0.94);
      backdrop-filter: blur(10px);
      border-top: 1px solid var(--border);
      padding: 10px 14px calc(10px + env(safe-area-inset-bottom));
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }}
    body.mode-overview .thumb-nav-only {{ opacity: 0.45; }}
    .thumb-btn {{
      flex: 1;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--ink);
      border-radius: 6px;
      padding: 10px 8px;
      font-family: var(--font-sans);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      text-align: center;
    }}
    .thumb-btn:active {{ transform: scale(0.98); }}
    .thumb-btn.is-primary {{
      background: var(--ink);
      border-color: var(--ink);
      color: #FFFFFF;
    }}
    @media (min-width: 768px) {{
      .thumb-bar {{ display: none; }}
    }}

    .kbd-hint {{
      display: none;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--muted);
      margin-top: 10px;
    }}
    @media (min-width: 768px) {{
      .kbd-hint {{ display: block; }}
    }}
    kbd {{
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--surface);
      padding: 1px 5px;
      font-family: var(--font-mono);
      font-size: 11px;
    }}

    .digest-footer {{
      margin-top: 40px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
      font-size: 12px;
      color: var(--muted);
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .status-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .status-tag {{
      font-family: var(--font-mono);
      font-size: 11px;
      padding: 2px 7px;
      border-radius: 4px;
      border: 1px solid var(--border);
      background: var(--surface);
    }}
    .status-tag.st-ok {{ color: var(--green-fg); background: var(--green-bg); border-color: transparent; }}
    .status-tag.st-muted {{ color: var(--muted); }}
    .empty-state {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 36px 24px;
      text-align: center;
      color: var(--muted);
    }}
  </style>
</head>
<body class="mode-overview">
  <div class="progress-track" aria-hidden="true"><div class="progress-fill" id="progressFill"></div></div>

  <main class="page-shell">
    <header class="masthead">
      <div class="masthead-top">
        <a class="brand-kicker" href="{home_href}">{escape(cfg.site_title)} · {issue_date}</a>
        <div class="masthead-controls">
          {archive_select}
          <div class="mode-switch" role="group" aria-label="切換閱讀視角">
            <button class="mode-btn" type="button" id="btnModeFocus">滑卡</button>
            <button class="mode-btn is-active" type="button" id="btnModeOverview">全景</button>
          </div>
        </div>
      </div>
      <h1 class="masthead-title">{escape(cfg.site_title)}</h1>
      <p class="takeaway">{takeaway}</p>
      <div class="kbd-hint">快捷鍵：<kbd>←</kbd> <kbd>→</kbd> 切換滑卡 · <kbd>W</kbd> 展開 Wait, what? · <kbd>G</kbd> 切換全景／滑卡</div>
    </header>

    {source_tabs_html}

    <nav class="filter-bar" id="topicFilterBar" aria-label="依主題篩選">
      {"".join(topic_pills)}
    </nav>

    <div id="panelsRoot">
      {"".join(tab_panels)}
    </div>

    <footer class="digest-footer">
      <div>生成時間：{generated_at} · 整理引擎：{llm_provider} ({llm_model}) · 規則：ASD-STE100 繁中 + ig-human 去 AI 腔</div>
      <div class="status-row">{"".join(status_badges)}</div>
    </footer>
  </main>

  <nav class="thumb-bar" aria-label="單手快速操作">
    <button class="thumb-btn thumb-nav-only" type="button" id="btnPrev">上則</button>
    <button class="thumb-btn" type="button" id="btnToggleQuad">四象限</button>
    <button class="thumb-btn" type="button" id="btnToggleModeMobile">全景</button>
    <button class="thumb-btn is-primary thumb-nav-only" type="button" id="btnNext">下則</button>
  </nav>

  <script>
    (() => {{
      const body = document.body;
      const progressFill = document.getElementById('progressFill');
      const topicFilterBar = document.getElementById('topicFilterBar');
      const panelsRoot = document.getElementById('panelsRoot');
      const panels = Array.from(document.querySelectorAll('.tab-panel'));
      const sourceTabs = Array.from(document.querySelectorAll('.source-tab'));
      const btnModeFocus = document.getElementById('btnModeFocus');
      const btnModeOverview = document.getElementById('btnModeOverview');
      const btnToggleModeMobile = document.getElementById('btnToggleModeMobile');
      const btnToggleQuad = document.getElementById('btnToggleQuad');
      const btnPrev = document.getElementById('btnPrev');
      const btnNext = document.getElementById('btnNext');
      const archiveSelect = document.getElementById('archiveSelect');

      let activeTab = 'overview';
      let activeFilter = 'all';
      let currentIdx = 0;

      function currentPanel() {{
        return panels.find(p => p.dataset.tabPanel === activeTab) || panels[0];
      }}

      function panelCards() {{
        const p = currentPanel();
        return p ? Array.from(p.querySelectorAll('.story-card')) : [];
      }}

      function visibleCards() {{
        const cards = panelCards();
        if (activeTab !== 'overview' || activeFilter === 'all') return cards;
        return cards.filter(c => c.dataset.topic === activeFilter);
      }}

      function updateProgress() {{
        const vis = visibleCards();
        if (!vis.length) {{ progressFill.style.width = '0%'; return; }}
        const pct = body.classList.contains('mode-focus')
          ? ((currentIdx + 1) / vis.length) * 100
          : 100;
        progressFill.style.width = pct.toFixed(1) + '%';
      }}

      function renderFocusState() {{
        const cards = panelCards();
        const vis = visibleCards();
        if (currentIdx >= vis.length) currentIdx = Math.max(0, vis.length - 1);
        cards.forEach(c => {{
          const inFilter = activeTab !== 'overview' || activeFilter === 'all' || c.dataset.topic === activeFilter;
          c.style.display = body.classList.contains('mode-overview')
            ? (inFilter ? 'block' : 'none')
            : '';
          c.classList.remove('is-active');
        }});
        if (body.classList.contains('mode-focus') && vis[currentIdx]) {{
          vis[currentIdx].classList.add('is-active');
          const counter = vis[currentIdx].querySelector('.card-footer-counter');
          if (counter) counter.textContent = (currentIdx + 1) + ' / ' + vis.length;
        }}
        updateProgress();
      }}

      function setMode(mode) {{
        const isFocus = mode === 'focus';
        body.classList.toggle('mode-focus', isFocus);
        body.classList.toggle('mode-overview', !isFocus);
        btnModeFocus.classList.toggle('is-active', isFocus);
        btnModeOverview.classList.toggle('is-active', !isFocus);
        btnToggleModeMobile.textContent = isFocus ? '全景' : '滑卡';
        if (!isFocus) {{
          panels.forEach(p => p.querySelector('.quadrant-panel')?.classList.remove('is-open'));
        }}
        renderFocusState();
      }}

      function setTab(tabKey) {{
        activeTab = tabKey;
        activeFilter = 'all';
        currentIdx = 0;
        sourceTabs.forEach(t => {{
          const on = t.dataset.tab === tabKey;
          t.classList.toggle('is-active', on);
          t.setAttribute('aria-selected', String(on));
        }});
        panels.forEach(p => {{
          const on = p.dataset.tabPanel === tabKey;
          p.classList.toggle('is-active', on);
          p.hidden = !on;
          p.querySelector('.quadrant-panel')?.classList.remove('is-open');
        }});
        if (topicFilterBar) {{
          topicFilterBar.hidden = tabKey !== 'overview';
          topicFilterBar.querySelectorAll('.filter-pill').forEach(p =>
            p.classList.toggle('is-active', p.dataset.filter === 'all')
          );
        }}
        renderFocusState();
      }}

      function goDelta(delta) {{
        const vis = visibleCards();
        if (!vis.length) return;
        if (!body.classList.contains('mode-focus')) setMode('focus');
        currentIdx = (currentIdx + delta + vis.length) % vis.length;
        currentPanel()?.querySelector('.quadrant-panel')?.classList.remove('is-open');
        renderFocusState();
        window.scrollTo({{ top: 0, behavior: 'smooth' }});
      }}

      function jumpToStory(origIndex) {{
        activeFilter = 'all';
        topicFilterBar?.querySelectorAll('.filter-pill').forEach(p =>
          p.classList.toggle('is-active', p.dataset.filter === 'all')
        );
        const cards = panelCards();
        currentIdx = Math.max(0, Math.min(cards.length - 1, origIndex));
        currentPanel()?.querySelector('.quadrant-panel')?.classList.remove('is-open');
        renderFocusState();
        if (body.classList.contains('mode-overview')) {{
          cards[currentIdx]?.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }} else {{
          window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}
      }}

      sourceTabs.forEach(tab => tab.addEventListener('click', () => setTab(tab.dataset.tab || 'overview')));

      document.querySelectorAll('.waitwhat-toggle').forEach(btn => {{
        btn.addEventListener('click', () => {{
          const drawer = document.getElementById(btn.getAttribute('aria-controls'));
          const open = btn.getAttribute('aria-expanded') === 'true';
          btn.setAttribute('aria-expanded', String(!open));
          btn.querySelector('.toggle-sign').textContent = open ? '+' : '-';
          if (drawer) drawer.hidden = open;
        }});
      }});

      document.querySelectorAll('.filter-pill').forEach(pill => {{
        pill.addEventListener('click', () => {{
          activeFilter = pill.dataset.filter || 'all';
          currentIdx = 0;
          document.querySelectorAll('.filter-pill').forEach(p => p.classList.toggle('is-active', p === pill));
          renderFocusState();
        }});
      }});

      document.querySelectorAll('.q-dot').forEach(dot => {{
        const go = () => jumpToStory(Number(dot.dataset.storyIndex || 0));
        dot.addEventListener('click', go);
        dot.addEventListener('keydown', e => {{ if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); go(); }} }});
      }});

      btnModeFocus.addEventListener('click', () => setMode('focus'));
      btnModeOverview.addEventListener('click', () => setMode('overview'));
      btnToggleModeMobile.addEventListener('click', () => setMode(body.classList.contains('mode-focus') ? 'overview' : 'focus'));
      btnToggleQuad.addEventListener('click', () => {{
        const qp = currentPanel()?.querySelector('.quadrant-panel');
        if (!qp) return;
        if (!body.classList.contains('mode-focus')) {{
          qp.scrollIntoView({{ behavior: 'smooth' }});
        }} else {{
          qp.classList.toggle('is-open');
          if (qp.classList.contains('is-open')) window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}
      }});
      btnPrev.addEventListener('click', () => goDelta(-1));
      btnNext.addEventListener('click', () => goDelta(1));

      if (archiveSelect) {{
        archiveSelect.addEventListener('change', () => {{
          if (archiveSelect.value) window.location.href = archiveSelect.value;
        }});
      }}

      let touchStartX = 0, touchStartY = 0;
      panelsRoot.addEventListener('touchstart', e => {{
        touchStartX = e.changedTouches[0].clientX;
        touchStartY = e.changedTouches[0].clientY;
      }}, {{ passive: true }});
      panelsRoot.addEventListener('touchend', e => {{
        if (!body.classList.contains('mode-focus')) return;
        const dx = e.changedTouches[0].clientX - touchStartX;
        const dy = e.changedTouches[0].clientY - touchStartY;
        if (Math.abs(dx) > 56 && Math.abs(dx) > Math.abs(dy) * 1.4) {{
          goDelta(dx < 0 ? 1 : -1);
        }}
      }}, {{ passive: true }});

      document.addEventListener('keydown', e => {{
        if (e.target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
        if (e.key === 'ArrowRight') goDelta(1);
        else if (e.key === 'ArrowLeft') goDelta(-1);
        else if (e.key.toLowerCase() === 'g') setMode(body.classList.contains('mode-focus') ? 'overview' : 'focus');
        else if (e.key.toLowerCase() === 'w') {{
          const vis = visibleCards();
          const btn = vis[currentIdx]?.querySelector('.waitwhat-toggle');
          if (btn) btn.click();
        }}
      }});

      setMode(window.innerWidth < 768 ? 'focus' : 'overview');
    }})();
  </script>
</body>
</html>
"""


def write_digest_site(
    digest: dict[str, Any],
    cfg: DigestConfig,
    docs_dir: Path,
) -> tuple[Path, Path]:
    """Write docs/index.html, docs/archive/YYYY-MM-DD.html, and docs/archive/index.json."""
    issue_date = str(digest.get("issue_date", "latest"))
    archive_dir = docs_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    existing_dates = sorted(
        {p.stem for p in archive_dir.glob("????-??-??.html")} | {issue_date},
        reverse=True,
    )

    index_html = render_digest_html(digest, cfg, archive_dates=existing_dates, is_archive_page=False)
    archive_html = render_digest_html(digest, cfg, archive_dates=existing_dates, is_archive_page=True)

    index_path = docs_dir / "index.html"
    archive_path = archive_dir / f"{issue_date}.html"
    index_path.write_text(index_html, encoding="utf-8")
    archive_path.write_text(archive_html, encoding="utf-8")
    (archive_dir / "index.json").write_text(
        json.dumps({"dates": existing_dates, "latest": issue_date}, indent=2) + "\n",
        encoding="utf-8",
    )
    (archive_dir / f"{issue_date}.json").write_text(
        json.dumps(digest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index_path, archive_path
