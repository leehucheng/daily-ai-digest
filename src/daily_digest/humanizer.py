"""Anti-slop text cleaner inspired by ig-human (strips AI tells & invisible chars)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Invisible Unicode watermark / formatting characters stripped by ig-human
_INVISIBLE_CHARS = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad\u034f\u061c]"
)

# Common AI slop clichés in Traditional Chinese & English tech summaries
_ZH_SLOP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"值得一提的是[，,]?"), ""),
    (re.compile(r"不容忽視的是[，,]?"), ""),
    (re.compile(r"總而言之[，,]?"), ""),
    (re.compile(r"毫無疑問[，,]?"), ""),
    (re.compile(r"這不僅僅是(.+?)，更是"), r"這不只是\1，也是"),
    (re.compile(r"顛覆性的|革命性的|劃時代的|史無前例的"), "重大"),
    (re.compile(r"無縫整合"), "直接整合"),
    (re.compile(r"賦能"), "讓"),
    (re.compile(r"深度剖析|深入探討"), "拆解"),
    (re.compile(r"引爆社群熱議"), "引發社群討論"),
    (re.compile(r"——+"), "："),
    (re.compile(r"\s*—\s*"), "，"),
]

_EN_SLOP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(game[- ]changer|revolutionary|groundbreaking)\b", re.I), "major"),
    (re.compile(r"\bseamlessly\b", re.I), "directly"),
    (re.compile(r"\bdelve into\b", re.I), "examine"),
    (re.compile(r"\bleverage\b", re.I), "use"),
]


def humanize_text(text: str) -> str:
    """Strip invisible characters, excessive em dashes, and AI slop clichés."""
    if not text:
        return ""
    out = _INVISIBLE_CHARS.sub("", text)
    # Remove remaining Cf (format) characters except standard whitespace
    out = "".join(
        ch for ch in out if unicodedata.category(ch) != "Cf" or ch in ("\n", "\t", " ")
    )
    for pattern, repl in _ZH_SLOP_PATTERNS:
        out = pattern.sub(repl, out)
    for pattern, repl in _EN_SLOP_PATTERNS:
        out = pattern.sub(repl, out)
    # Clean duplicated punctuation after substitutions
    out = re.sub(r"([，,。：:])\1+", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def _humanize_item_list(items: list[dict[str, Any]]) -> None:
    for item in items:
        for field in ("headline_zh", "community_consensus_zh", "rank_reason_zh"):
            if field in item and isinstance(item[field], str):
                item[field] = humanize_text(item[field])
        ww = item.get("wait_what")
        if isinstance(ww, dict):
            for field in ("premise_zh", "why_it_matters_to_you_zh"):
                if field in ww and isinstance(ww[field], str):
                    ww[field] = humanize_text(ww[field])
            if isinstance(ww.get("glossary_notes"), list):
                for note in ww["glossary_notes"]:
                    if isinstance(note, dict) and isinstance(note.get("explain_zh"), str):
                        note["explain_zh"] = humanize_text(note["explain_zh"])
        if isinstance(item.get("community_voices"), list):
            for voice in item["community_voices"]:
                if isinstance(voice, dict) and isinstance(voice.get("quote_zh"), str):
                    voice["quote_zh"] = humanize_text(voice["quote_zh"])


def humanize_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively humanize user-facing text fields in overview items & per-source sections."""
    if "executive_takeaway_zh" in payload:
        payload["executive_takeaway_zh"] = humanize_text(str(payload["executive_takeaway_zh"]))
    _humanize_item_list(payload.get("items", []))
    for sec in payload.get("sections", []):
        if isinstance(sec, dict):
            if isinstance(sec.get("takeaway_zh"), str):
                sec["takeaway_zh"] = humanize_text(sec["takeaway_zh"])
            _humanize_item_list(sec.get("items", []))
    return payload
