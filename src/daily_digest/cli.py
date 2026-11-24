"""CLI entry point for daily-ai-digest pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import PROJECT_ROOT, load_config
from .fetchers import RawItem, collect_all_sources, load_seen_store, save_seen_store
from .humanizer import humanize_digest_payload
from .llm_client import DualLLMClient
from .notify import send_gmail, send_ntfy
from .render_html import write_digest_site
from .synthesizer import compute_final_ranking, synthesize_digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Daily AI Editorial Digest pipeline")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "topics.yaml")
    parser.add_argument("--glossary", type=Path, default=PROJECT_ROOT / "config" / "GLOSSARY.md")
    parser.add_argument("--docs-dir", type=Path, default=PROJECT_ROOT / "docs")
    parser.add_argument("--seen-file", type=Path, default=PROJECT_ROOT / "data" / "seen.json")
    parser.add_argument(
        "--provider",
        choices=["auto", "gemini", "claude"],
        default=None,
        help="Override LLM_PROVIDER (auto tries Gemini then Claude on quota/error failover)",
    )
    parser.add_argument("--topic-override", type=str, default=None, help="Ad-hoc topic query override")
    parser.add_argument("--skip-last30days", action="store_true", help="Skip last30days subprocess")
    parser.add_argument("--fetch-only", type=Path, default=None, help="Fetch candidates and save raw JSON")
    parser.add_argument(
        "--from-raw",
        type=Path,
        default=None,
        help="Synthesize & render from a saved raw candidates JSON without re-fetching SNS",
    )
    parser.add_argument(
        "--render-only",
        type=Path,
        default=None,
        help="Render HTML + notify from a pre-synthesized digest JSON",
    )
    parser.add_argument(
        "--notify-only",
        type=Path,
        default=None,
        help="Send ntfy/Gmail notifications from a pre-synthesized digest JSON (after deploy)",
    )
    parser.add_argument("--skip-notify", action="store_true", help="Do not send ntfy/Gmail notifications")
    parser.add_argument("--dry-run-notify", action="store_true", help="Print notification payloads without sending")
    args = parser.parse_args(argv)

    cfg = load_config(args.config, args.glossary)
    if args.provider:
        cfg.llm_provider = args.provider
    if args.topic_override:
        cfg.topics = [cfg.topics[0]] if cfg.topics else []
        if cfg.topics:
            cfg.topics[0].name = args.topic_override
            cfg.topics[0].query = args.topic_override

    tz = ZoneInfo(cfg.timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    if args.notify_only:
        digest = json.loads(args.notify_only.read_text(encoding="utf-8"))
        print("[notify]", send_ntfy(digest, cfg, dry_run=args.dry_run_notify))
        print("[notify]", send_gmail(digest, cfg, dry_run=args.dry_run_notify))
        return 0

    if args.render_only:
        digest = json.loads(args.render_only.read_text(encoding="utf-8"))
        digest = compute_final_ranking(digest, cfg)
        digest = humanize_digest_payload(digest)
        idx_path, arch_path = write_digest_site(digest, cfg, args.docs_dir)
        print(f"[render-only] wrote {idx_path} and {arch_path}")
        if not args.skip_notify:
            print("[notify]", send_ntfy(digest, cfg, dry_run=args.dry_run_notify))
            print("[notify]", send_gmail(digest, cfg, dry_run=args.dry_run_notify))
        return 0

    if args.from_raw:
        raw_blob = json.loads(args.from_raw.read_text(encoding="utf-8"))
        source_status = raw_blob.get("source_status") or {}
        candidates = [RawItem(**c) for c in raw_blob.get("candidates", [])]
        print(f"[from-raw] loaded {len(candidates)} candidates from {args.from_raw}; status={source_status}")
    else:
        candidates, source_status = collect_all_sources(
            cfg,
            seen_path=args.seen_file,
            skip_last30days=args.skip_last30days,
        )
        print(f"[fetch] collected {len(candidates)} deduped candidates; status={source_status}")
        # Auto-cache raw fetch so LLM retries never double-spend Apify/ScrapeCreators credits
        cache_path = PROJECT_ROOT / "data" / "tmp" / "last_fetch.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "issue_date": today,
                    "source_status": source_status,
                    "candidates": [c.to_dict() for c in candidates],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if args.fetch_only:
        args.fetch_only.parent.mkdir(parents=True, exist_ok=True)
        args.fetch_only.write_text(
            json.dumps(
                {
                    "issue_date": today,
                    "source_status": source_status,
                    "candidates": [c.to_dict() for c in candidates],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[fetch-only] saved raw candidates to {args.fetch_only}")
        return 0

    if not candidates:
        print("[warn] no candidates collected; aborting without overwriting docs/index.html", file=sys.stderr)
        return 2

    client = DualLLMClient(
        provider=cfg.llm_provider,
        priority_order=cfg.llm_priority_order,
        gemini_model=cfg.gemini_model,
        claude_model=cfg.claude_fast_model,
        max_tokens=cfg.max_tokens,
    )
    if not client.available_providers():
        print(
            "[error] Neither GEMINI_API_KEY nor ANTHROPIC_API_KEY is set. "
            "Set at least one in .env / GitHub Secrets, or use --fetch-only / --render-only.",
            file=sys.stderr,
        )
        return 3

    digest = synthesize_digest(
        candidates,
        cfg,
        source_status,
        llm_client=client,
        issue_date=today,
    )
    idx_path, arch_path = write_digest_site(digest, cfg, args.docs_dir)
    sec_summary = ", ".join(
        f"{s.get('label')}={len(s.get('items', []))}" for s in digest.get("sections", [])
    )
    print(
        f"[synth] provider={digest['meta']['llm_provider']} ({digest['meta']['llm_model']}) "
        f"overview={len(digest.get('items', []))} sections=[{sec_summary}] -> {idx_path}"
    )

    # Update cross-day dedup store with all published items (overview + sections)
    seen = load_seen_store(args.seen_file)
    cand_map: dict[str, RawItem] = {c.id: c for c in candidates}
    all_published = list(digest.get("items", []))
    for sec in digest.get("sections", []):
        all_published.extend(sec.get("items", []))
    for it in all_published:
        cid = str(it.get("id", ""))
        eng = cand_map[cid].engagement_score if cid in cand_map else 100.0
        if cid:
            seen[cid] = {"first_seen": today, "engagement": eng, "headline": it.get("headline_zh", "")}
    save_seen_store(args.seen_file, seen, window_days=cfg.dedup_window_days)

    if not args.skip_notify:
        print("[notify]", send_ntfy(digest, cfg, dry_run=args.dry_run_notify))
        print("[notify]", send_gmail(digest, cfg, dry_run=args.dry_run_notify))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
