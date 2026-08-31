#!/usr/bin/env python3
"""Poster ranking + caption via OpenAI, then Gemini, then Hugging Face."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import football_poster
import news_grade
import shared_stories
from ai_client import chat_json

LOG = logging.getLogger("poster_llm")

FAN_WRITE = (
    "เขียนโพสต์ข่าวฟุตบอลเป็นภาษาแฟนบอลไทย ล้วน และตอบเป็น JSON object เท่านั้น "
    "ห้ามใช้ Markdown หรือ code fence และห้ามใช้อีโมจิใน hook "
    "ใช้ชื่อที่แฟนไทยเรียกถ้ามี เช่น สาลิกาดง ไก่เดือยทอง หงส์แดง ปืนใหญ่ เรือใบสีฟ้า ผีแดง กัคโป มาร์ติเนซ "
    "ห้ามแปลอังกฤษคำต่อคำ ห้ามโทนข่าวราชการ "
    "hook สั้นแรงไม่เกินประมาณ 40 ตัวอักษร "
    "body มี 3-5 บรรทัด เว้นวรรคอ่านง่ายบนมือถือ มีตัวเลขหรือเหตุผลถ้าข่าวมี "
    "cta ชวนเลือกข้างหรือคอมเมนต์ และ hashtags ภาษาไทย 3-5 รายการ"
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
    title = re.sub(r"\s+", " ", str(getattr(item, "title", "") or "")).strip()
    summary = re.sub(r"\s+", " ", str(getattr(item, "summary", "") or "")).strip()
    hook = (score.get("main_angle") or title)[:40].strip() or title[:40]
    body_bits = [title]
    if summary and summary.lower() not in title.lower():
        body_bits.append(summary[:280])
    reason = str(score.get("reason") or "").strip()
    if reason and reason not in body_bits[-1]:
        body_bits.append(reason[:180])
    body = "\n".join(body_bits[:4])
    return {
        "hook": hook,
        "body": body[:3000],
        "cta": "แฟนบอลมองเรื่องนี้ยังไงครับ?",
        "hashtags": ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#พรีเมียร์ลีก"],
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
    }
    try:
        post = chat_json(FAN_WRITE, json.dumps(post_input, ensure_ascii=False))
    except Exception as exc:
        LOG.warning("LLM write_post failed; using template caption: %s", exc)
        return _fallback_post(item, score)
    if not isinstance(post, dict):
        LOG.warning("write_post ได้ข้อมูลไม่ใช่ JSON object; using template caption")
        return _fallback_post(item, score)
    missing = [field for field in ("hook", "body", "cta", "hashtags") if field not in post]
    if missing:
        LOG.warning("write_post JSON ขาดฟิลด์: %s; using template caption", ", ".join(missing))
        return _fallback_post(item, score)
    hook = re.sub(r"[\U00010000-\U0010ffff]", "", str(post.get("hook") or "")).strip()[:100]
    body = str(post.get("body") or "").strip()[:3000]
    cta = str(post.get("cta") or "").strip()[:500]
    tags = [str(tag).strip()[:80] for tag in (post.get("hashtags") or []) if str(tag).strip()][:5]
    if not hook or not body or not cta or len(tags) < 3:
        LOG.warning("write_post ฟิลด์ไม่ครบหลังทำความสะอาด; using template caption")
        return _fallback_post(item, score)
    return {"hook": hook, "body": body, "cta": cta, "hashtags": tags}


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
