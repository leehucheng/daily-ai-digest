"""Notifications: ntfy push (for Pixel 10 Pro) + Gmail digest email."""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

import certifi

from .config import DigestConfig


def build_ntfy_payload(digest: dict[str, Any], cfg: DigestConfig) -> tuple[str, str, str]:
    """Return (title, body, click_url) for ntfy push notification."""
    issue_date = str(digest.get("issue_date", ""))
    items = list(digest.get("items", []))
    title = f"{cfg.site_title} · {issue_date}（{len(items)} 則）"
    lines: list[str] = []
    for it in items[:3]:
        rank = it.get("rank", "")
        hl = str(it.get("headline_zh", ""))
        cons = str(it.get("community_consensus_zh", ""))
        lines.append(f"#{rank} {hl}")
        if cons:
            lines.append(f"   風向：{cons}")
    if len(items) > 3:
        lines.append(f"點擊看完整 {len(items)} 則互動滑卡與四象限圖")
    body = "\n".join(lines) or str(digest.get("executive_takeaway_zh", "今日特刊已更新"))
    click_url = cfg.site_url.rstrip("/") + "/" if cfg.site_url else ""
    return title, body, click_url


def send_ntfy(
    digest: dict[str, Any],
    cfg: DigestConfig,
    *,
    topic: str | None = None,
    server: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    ntfy_topic = (topic if topic is not None else os.environ.get("NTFY_TOPIC", "")).strip()
    ntfy_server = (server or os.environ.get("NTFY_SERVER", "https://ntfy.sh")).strip().rstrip("/")
    title, body, click_url = build_ntfy_payload(digest, cfg)

    if not ntfy_topic:
        return {"channel": "ntfy", "status": "skipped", "reason": "NTFY_TOPIC not set"}

    # Use ntfy JSON POST endpoint so UTF-8 Traditional Chinese titles/actions never hit latin-1 header errors
    json_payload: dict[str, Any] = {
        "topic": ntfy_topic,
        "title": title,
        "message": body,
        "priority": 3,
        "tags": ["newspaper", "robot"],
    }
    if click_url:
        json_payload["click"] = click_url
        json_payload["actions"] = [
            {"action": "view", "label": "打開特刊", "url": click_url}
        ]

    if dry_run:
        return {
            "channel": "ntfy",
            "status": "dry-run",
            "url": ntfy_server,
            "payload": json_payload,
        }

    req = urllib.request.Request(
        ntfy_server,
        data=json.dumps(json_payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        return {"channel": "ntfy", "status": "sent", "http_status": resp.status}


def build_gmail_html(digest: dict[str, Any], cfg: DigestConfig) -> tuple[str, str, str]:
    """Return (subject, plain_text, html_body) for editorial morning email."""
    issue_date = str(digest.get("issue_date", ""))
    items = list(digest.get("items", []))
    takeaway = str(digest.get("executive_takeaway_zh", ""))
    site_url = cfg.site_url.rstrip("/") + "/" if cfg.site_url else "#"
    subject = f"[{cfg.site_title}] {issue_date} — {items[0].get('short_tag', 'AI 精選') if items else '今日更新'}"

    plain_lines = [f"{cfg.site_title} ({issue_date})", takeaway, ""]
    html_rows: list[str] = []
    for it in items[:5]:
        rank = int(it.get("rank", 0))
        hl = str(it.get("headline_zh", ""))
        cons = str(it.get("community_consensus_zh", ""))
        imp = int(it.get("importance_score", 0))
        pop = int(it.get("popularity_score", 0))
        plain_lines.append(f"#{rank:02d} [重要 {imp} / 熱門 {pop}] {hl}")
        plain_lines.append(f"    社群風向：{cons}")
        plain_lines.append("")
        html_rows.append(
            f'<tr><td style="padding:14px 0;border-bottom:1px solid #EAEAEA;">'
            f'<div style="font-family:monospace;font-size:11px;color:#787774;margin-bottom:4px;">'
            f'#{rank:02d} · 重要 {imp} · 熱門 {pop} · {escape(str(it.get("topic_name", "")))}</div>'
            f'<div style="font-size:16px;font-weight:600;color:#111111;line-height:1.4;margin-bottom:6px;">'
            f'{escape(hl)}</div>'
            f'<div style="font-size:13.5px;color:#2F3437;background:#FAF8F5;padding:8px 10px;'
            f'border:1px solid #EAEAEA;border-radius:6px;">社群風向：{escape(cons)}</div>'
            f"</td></tr>"
        )
    plain_lines.append(f"打開手機一頁式互動特刊：{site_url}")

    html_body = f"""<!DOCTYPE html>
<html lang="zh-Hant"><body style="margin:0;padding:24px 12px;background:#F7F6F3;font-family:-apple-system,BlinkMacSystemFont,'Noto Sans TC',sans-serif;color:#2F3437;">
  <table role="presentation" width="100%" style="max-width:600px;margin:0 auto;background:#FFFFFF;border:1px solid #EAEAEA;border-radius:10px;padding:24px;">
    <tr><td style="padding-bottom:14px;border-bottom:1px solid #EAEAEA;">
      <div style="font-family:monospace;font-size:11px;letter-spacing:0.08em;color:#787774;">{escape(cfg.site_title)} · {escape(issue_date)}</div>
      <h1 style="margin:6px 0 8px;font-size:24px;color:#111111;">{escape(cfg.site_title)}</h1>
      <p style="margin:0;font-size:14.5px;line-height:1.6;color:#2F3437;">{escape(takeaway)}</p>
    </td></tr>
    {"".join(html_rows)}
    <tr><td style="padding-top:20px;text-align:center;">
      <a href="{escape(site_url, quote=True)}" style="display:inline-block;background:#111111;color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:600;padding:11px 22px;border-radius:6px;">打開手機一頁式特刊（含四象限圖與 Wait, what?）</a>
    </td></tr>
  </table>
</body></html>"""
    return subject, "\n".join(plain_lines), html_body


def send_gmail(
    digest: dict[str, Any],
    cfg: DigestConfig,
    *,
    to_addr: str | None = None,
    sender: str | None = None,
    app_password: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    recipient = (to_addr or os.environ.get("GMAIL_TO", "lee52025202@gmail.com")).strip()
    from_addr = (sender or os.environ.get("GMAIL_SENDER", recipient)).strip()
    pw = (app_password if app_password is not None else os.environ.get("GMAIL_APP_PASSWORD", "")).strip()
    subject, plain_text, html_body = build_gmail_html(digest, cfg)

    if not pw:
        return {"channel": "gmail", "status": "skipped", "reason": "GMAIL_APP_PASSWORD not set"}
    if dry_run:
        return {
            "channel": "gmail",
            "status": "dry-run",
            "to": recipient,
            "subject": subject,
        }

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ctx = ssl.create_default_context(cafile=certifi.where())
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=25) as server:
        server.login(from_addr, pw)
        server.sendmail(from_addr, [recipient], msg.as_string())
    return {"channel": "gmail", "status": "sent", "to": recipient}
