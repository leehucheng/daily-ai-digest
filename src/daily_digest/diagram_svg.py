"""Editorial SVG generators following diagram-design v2.6 + minimalist-ui aesthetics.

Zero drop shadows, warm paper background (#FAF8F5), charcoal ink (#1F2328),
crisp 1px hairlines, muted spot-pastel semantic accents, mobile-legible typography.
"""

from __future__ import annotations

from html import escape
from typing import Any


def render_quadrant_svg(items: list[dict[str, Any]]) -> str:
    """Render an interactive editorial Quadrant Scatter SVG (Popularity x Importance).

    Each story dot carries data-story-index so clicking jumps straight to that card.
    """
    w, h = 680, 420
    pad_l, pad_r, pad_t, pad_b = 64, 34, 40, 56
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    mid_x = pad_l + plot_w / 2
    mid_y = pad_t + plot_h / 2

    def sx(val: float) -> float:
        clamped = max(0.0, min(100.0, float(val)))
        return pad_l + (clamped / 100.0) * plot_w

    def sy(val: float) -> float:
        clamped = max(0.0, min(100.0, float(val)))
        return pad_t + (1.0 - clamped / 100.0) * plot_h

    # Topic pastel fills
    topic_fills = {
        "agents": ("#FDEBEC", "#9F2F2D"),
        "models-edge": ("#EDF3EC", "#346538"),
        "industry": ("#E1F3FE", "#1F6C9F"),
    }

    dots_svg: list[str] = []
    # Avoid label collisions with deterministic slight offset if coordinates are close
    placed: list[tuple[float, float]] = []
    for idx, item in enumerate(items, start=1):
        pop = float(item.get("popularity_score", 50))
        imp = float(item.get("importance_score", 50))
        cx, cy = sx(pop), sy(imp)
        for px, py in placed:
            if abs(cx - px) < 22 and abs(cy - py) < 22:
                cx = min(pad_l + plot_w - 16, cx + 18)
                cy = max(pad_t + 16, cy - 14)
        placed.append((cx, cy))

        tid = str(item.get("topic_id", "industry"))
        fill, stroke = topic_fills.get(tid, ("#FBF3DB", "#956400"))
        is_top = idx == 1
        r = 16 if is_top else 13
        dot_fill = "#111111" if is_top else fill
        dot_stroke = "#111111" if is_top else stroke
        num_color = "#FFFFFF" if is_top else stroke
        short_label = escape(str(item.get("short_tag", f"#{idx}"))[:14])
        # Place caption below dot if near top edge, else above
        label_y = cy + r + 14 if cy < pad_t + 44 else cy - r - 7

        dots_svg.append(
            f'<g class="q-dot" data-story-index="{idx - 1}" role="button" tabindex="0" '
            f'aria-label="前往第 {idx} 則：{short_label}">'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{dot_fill}" '
            f'stroke="{dot_stroke}" stroke-width="1.5"/>'
            f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" '
            f'font-family="JetBrains Mono, SF Mono, monospace" font-size="11" '
            f'font-weight="600" fill="{num_color}">{idx}</text>'
            f'<text x="{cx:.1f}" y="{label_y:.1f}" text-anchor="middle" '
            f'font-family="SF Pro Display, Noto Sans TC, sans-serif" font-size="10.5" '
            f'fill="#2F3437">{short_label}</text>'
            f"</g>"
        )

    return f"""<svg class="editorial-svg quadrant-svg" viewBox="0 0 {w} {h}" role="img" aria-label="今日 AI 消息重要度與熱門度四象限圖" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{w}" height="{h}" rx="8" fill="#FAF8F5" stroke="#EAEAEA" stroke-width="1"/>
  <!-- Highlight top-right quadrant (must-read) -->
  <rect x="{mid_x:.1f}" y="{pad_t}" width="{plot_w / 2:.1f}" height="{plot_h / 2:.1f}" fill="#FBF3DB" fill-opacity="0.38"/>
  <!-- Outer frame -->
  <rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" fill="none" stroke="#D8D6D0" stroke-width="1"/>
  <!-- Midpoint hairlines -->
  <line x1="{mid_x:.1f}" y1="{pad_t}" x2="{mid_x:.1f}" y2="{pad_t + plot_h}" stroke="#D8D6D0" stroke-width="1" stroke-dasharray="4 4"/>
  <line x1="{pad_l}" y1="{mid_y:.1f}" x2="{pad_l + plot_w}" y2="{mid_y:.1f}" stroke="#D8D6D0" stroke-width="1" stroke-dasharray="4 4"/>
  <!-- Quadrant titles -->
  <text x="{pad_l + plot_w - 10}" y="{pad_t + 20}" text-anchor="end" font-family="SF Pro Display, Noto Sans TC, sans-serif" font-size="11" font-weight="600" fill="#956400">必看突破（高重要 · 高熱門）</text>
  <text x="{pad_l + 10}" y="{pad_t + 20}" text-anchor="start" font-family="SF Pro Display, Noto Sans TC, sans-serif" font-size="11" fill="#787774">低調硬核（高重要 · 低討論）</text>
  <text x="{pad_l + plot_w - 10}" y="{pad_t + plot_h - 10}" text-anchor="end" font-family="SF Pro Display, Noto Sans TC, sans-serif" font-size="11" fill="#787774">社群熱議（討論兇 · 實質影響待觀察）</text>
  <text x="{pad_l + 10}" y="{pad_t + plot_h - 10}" text-anchor="start" font-family="SF Pro Display, Noto Sans TC, sans-serif" font-size="11" fill="#787774">背景動態</text>
  <!-- Axis labels -->
  <text x="{pad_l + plot_w / 2:.1f}" y="{h - 16}" text-anchor="middle" font-family="JetBrains Mono, SF Mono, monospace" font-size="11" fill="#55524C">熱門度 POPULARITY（跨社群真實互動） →</text>
  <text x="20" y="{pad_t + plot_h / 2:.1f}" text-anchor="middle" transform="rotate(-90 20 {pad_t + plot_h / 2:.1f})" font-family="JetBrains Mono, SF Mono, monospace" font-size="11" fill="#55524C">重要度 IMPORTANCE →</text>
  <!-- Tick marks -->
  <text x="{pad_l}" y="{pad_t + plot_h + 16}" font-family="JetBrains Mono, monospace" font-size="10" fill="#787774">0</text>
  <text x="{mid_x:.1f}" y="{pad_t + plot_h + 16}" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="10" fill="#787774">50</text>
  <text x="{pad_l + plot_w}" y="{pad_t + plot_h + 16}" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="10" fill="#787774">100</text>
  <text x="{pad_l - 8}" y="{pad_t + 8}" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="10" fill="#787774">100</text>
  <text x="{pad_l - 8}" y="{mid_y + 4:.1f}" text-anchor="end" font-family="JetBrains Mono, monospace" font-size="10" fill="#787774">50</text>
  <!-- Story nodes -->
  {"".join(dots_svg)}
</svg>"""


def render_top1_flow_svg(diagram: dict[str, Any] | None) -> str:
    """Render an editorial causal/architecture diagram SVG for the #1 story."""
    if not diagram or not diagram.get("nodes"):
        return ""
    title = escape(str(diagram.get("title_zh", "頭條關鍵脈絡")))
    nodes: list[dict[str, Any]] = list(diagram.get("nodes", []))[:4]
    n = len(nodes)
    if n == 0:
        return ""

    w = 640
    h = 190
    gap = 28
    margin_x = 24
    box_w = (w - 2 * margin_x - gap * (n - 1)) / n
    box_h = 92
    box_y = 60

    tone_styles = {
        "neutral": ("#FFFFFF", "#D8D6D0", "#2F3437", "#787774"),
        "change": ("#FBF3DB", "#D4B36A", "#111111", "#956400"),
        "impact": ("#EDF3EC", "#8EB492", "#111111", "#346538"),
        "tension": ("#FDEBEC", "#D99A98", "#111111", "#9F2F2D"),
    }

    parts: list[str] = []
    for i, node in enumerate(nodes):
        x = margin_x + i * (box_w + gap)
        tone = str(node.get("tone", "neutral"))
        fill, stroke, title_col, sub_col = tone_styles.get(tone, tone_styles["neutral"])
        step_tag = escape(str(node.get("step", f"0{i+1}")))
        label = escape(str(node.get("label_zh", ""))[:16])
        raw_detail = str(node.get("detail_zh", ""))[:28]
        # Slice BEFORE XML-escaping so entities like &amp; are never split across lines
        d1, d2 = escape(raw_detail[:14]), escape(raw_detail[14:28])

        parts.append(
            f'<g>'
            f'<rect x="{x:.1f}" y="{box_y}" width="{box_w:.1f}" height="{box_h}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
            f'<text x="{x + 12:.1f}" y="{box_y + 20}" font-family="JetBrains Mono, monospace" '
            f'font-size="9.5" letter-spacing="0.06em" fill="{sub_col}">{step_tag}</text>'
            f'<text x="{x + 12:.1f}" y="{box_y + 42}" font-family="SF Pro Display, Noto Sans TC, sans-serif" '
            f'font-size="13" font-weight="600" fill="{title_col}">{label}</text>'
            f'<text x="{x + 12:.1f}" y="{box_y + 62}" font-family="SF Pro Display, Noto Sans TC, sans-serif" '
            f'font-size="10.5" fill="#55524C">{d1}</text>'
            + (
                f'<text x="{x + 12:.1f}" y="{box_y + 77}" font-family="SF Pro Display, Noto Sans TC, sans-serif" '
                f'font-size="10.5" fill="#55524C">{d2}</text>'
                if d2
                else ""
            )
            + "</g>"
        )
        if i < n - 1:
            ax1 = x + box_w + 4
            ax2 = x + box_w + gap - 5
            ay = box_y + box_h / 2
            parts.append(
                f'<line x1="{ax1:.1f}" y1="{ay:.1f}" x2="{ax2:.1f}" y2="{ay:.1f}" '
                f'stroke="#787774" stroke-width="1.25"/>'
                f'<polyline points="{ax2 - 5:.1f},{ay - 4:.1f} {ax2:.1f},{ay:.1f} {ax2 - 5:.1f},{ay + 4:.1f}" '
                f'fill="none" stroke="#787774" stroke-width="1.25"/>'
            )

    return f"""<svg class="editorial-svg flow-svg" viewBox="0 0 {w} {h}" role="img" aria-label="{title}" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{w}" height="{h}" rx="8" fill="#FAF8F5" stroke="#EAEAEA" stroke-width="1"/>
  <text x="{margin_x}" y="30" font-family="JetBrains Mono, monospace" font-size="10" letter-spacing="0.08em" fill="#787774">TOP #1 ARCHITECTURE &amp; IMPACT</text>
  <text x="{margin_x}" y="47" font-family="SF Pro Display, Noto Sans TC, sans-serif" font-size="13" font-weight="600" fill="#111111">{title}</text>
  {"".join(parts)}
</svg>"""
