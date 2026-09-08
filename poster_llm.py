#!/usr/bin/env python3
"""Poster ranking + caption via OpenAI, then Gemini, then Hugging Face."""
from __future__ import annotations

import json
import logging
from typing import Any

import caption_th
import football_poster
import news_grade
import shared_stories
from ai_client import chat_json

LOG = logging.getLogger("poster_llm")

FAN_WRITE = (
    caption_th.prompt_block()
    + " ห้ามใช้ Markdown หรือ code fence และห้ามใช้อีโมจิใน hook"
)

RANK_PROMPT = (
    "บรรณาธิการเพจแฟนบอลไทย ตอบ JSON เท่านั้น "
    'รูปแบบ {"items":[{"id":"","score":0,"is_worthy":true,"main_angle":"","reason":""}]} '
    "ให้คะแนน 0-100 จาก ใคร 25 อะไร 25 ทัน 20 เล่าต่อ 15 คนเถียง 15 "
    "ทีมดัง พรีเมียร์ลีก ย้ายทีมปิดดีล ผลแข่ง ดราม่ากุนซือ ได้คะแนนสูง "
    "ข่าวซุบซิบรวมหลายเรื่อง ข่าวลือรวม paper talk ให้ score ไม่เกิน 40 และ is_worthy=false"
)


def _rule_rank(items: list) -> dict[str, dict[str, Any]]:
    results = {item.id: news_grade.finalize(item, None, news_grade.POSTER_MIN) for item in items}
    worthy = sum(1 for row in results.values() if row.get("is_worthy"))
    LOG.info("Rule-ranked %s items, %s passed poster grade %s", len(results), worthy, news_grade.POSTER_MIN)
    return results


def rank_news(items: list) -> dict[str, dict[str, Any]]:
    payload = [
        {
            "id": item.id,
            "source": getattr(item, "source", ""),
            "title": item.title[:180],
            "summary": str(getattr(item, "summary", "") or "")[:240],
            "published": str(getattr(item, "published", "") or "")[:40],
        }
        for item in items[:16]
    ]
    try:
        data = chat_json(RANK_PROMPT, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        LOG.warning("LLM rank failed; using rule grade: %s", exc)
        return _rule_rank(items)
    raw: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("id"):
            raw[str(item["id"])] = item
    results: dict[str, dict[str, Any]] = {}
    for item in items:
        results[item.id] = news_grade.finalize(item, raw.get(item.id), news_grade.POSTER_MIN)
    worthy = sum(1 for row in results.values() if row.get("is_worthy"))
    LOG.info("Ranked %s items, %s passed poster grade %s", len(results), worthy, news_grade.POSTER_MIN)
    if not results:
        return _rule_rank(items)
    return results


def _fallback_post(item, score: dict[str, Any]) -> dict[str, Any]:
    polished = caption_th.write_caption_th(item, None, score)
    return {
        "hook": polished["hook"],
        "body": polished["body"],
        "cta": polished["cta"],
        "hashtags": polished["hashtags"],
        "why": polished.get("why", ""),
        "grade": polished.get("grade", {}),
    }


def write_post(item, score: dict[str, Any]) -> dict[str, Any]:
    shared_stories.mark(item)
    post_input = {
        "title": item.title[:300],
        "summary": str(getattr(item, "summary", "") or "")[:600],
        "source": getattr(item, "source", ""),
        "angle": score.get("main_angle", ""),
        "reason": score.get("reason", ""),
        "grade": score.get("score", ""),
        "voice": caption_th.VOICE_RULES,
    }
    post = None
    try:
        post = chat_json(FAN_WRITE, json.dumps(post_input, ensure_ascii=False))
    except Exception as exc:
        LOG.warning("LLM write_post failed; using template caption: %s", exc)
        post = None
    if not isinstance(post, dict):
        polished = caption_th.polish_post(item, None, score)
    else:
        polished = caption_th.polish_post(item, post, score)
    LOG.info(
        "Caption type=%s grade=%s ok=%s | %s",
        polished.get("type"),
        (polished.get("grade") or {}).get("score"),
        (polished.get("grade") or {}).get("ok"),
        polished.get("hook"),
    )
    return {
        "hook": polished["hook"],
        "body": polished["body"],
        "cta": polished["cta"],
        "hashtags": polished["hashtags"],
        "why": polished.get("why", ""),
        "grade": polished.get("grade", {}),
    }


_orig_fetch = football_poster.fetch_feed
_orig_save = football_poster.save_state


def fetch_feed(source, url):
    kept = []
    for item in _orig_fetch(source, url):
        if shared_stories.is_used(item):
            LOG.info("Skip story already used by poster/video: %s", getattr(item, "title", "")[:80])
            continue
        kept.append(item)
    return kept


def save_state(path, state):
    return _orig_save(path, shared_stories.merge_into(state))


def patch() -> None:
    football_poster.rank_news = rank_news
    football_poster.write_post = write_post
    football_poster.fetch_feed = fetch_feed
    football_poster.save_state = save_state
