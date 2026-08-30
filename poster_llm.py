#!/usr/bin/env python3
"""Poster ranking + caption via OpenAI, then Gemini, then Hugging Face."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import football_poster
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
    data = chat_json(
        (
            "บรรณาธิการเพจแฟนบอลไทย ตอบ JSON เท่านั้น "
            'รูปแบบ {"items":[{"id":"","score":0,"is_worthy":true,"main_angle":"","reason":""}]} '
            "ให้คะแนนสูงกับข่าวที่คนไทยแชร์ได้ ย้ายทีมดัง ผลแข่ง พรีเมียร์ลีก ดราม่าผู้จัดการ "
            "ตัดข่าวซุบซิบรวมหลายเรื่องและข่าวทั่วไป"
        ),
        json.dumps(payload, ensure_ascii=False),
    )
    results: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        ident = str(item["id"])
        results[ident] = {
            "id": ident,
            "score": max(0, min(100, float(item.get("score", 0) or 0))),
            "is_worthy": bool(item.get("is_worthy", False)),
            "main_angle": str(item.get("main_angle", ""))[:500],
            "reason": str(item.get("reason", ""))[:1000],
        }
    if not results:
        raise RuntimeError("rank_news ไม่ได้รายการข่าวกลับมา")
    LOG.info("Ranked %s items via fallback LLM chain", len(results))
    return results


def write_post(item, score: dict[str, Any]) -> dict[str, Any]:
    shared_stories.mark(item)
    post_input = {
        "title": item.title[:300],
        "summary": str(getattr(item, "summary", "") or "")[:600],
        "source": getattr(item, "source", ""),
        "angle": score.get("main_angle", ""),
        "reason": score.get("reason", ""),
    }
    post = chat_json(FAN_WRITE, json.dumps(post_input, ensure_ascii=False))
    if not isinstance(post, dict):
        raise ValueError("write_post ได้ข้อมูลไม่ใช่ JSON object")
    missing = [field for field in ("hook", "body", "cta", "hashtags") if field not in post]
    if missing:
        raise ValueError("write_post JSON ขาดฟิลด์: " + ", ".join(missing))
    hook = re.sub(r"[\U00010000-\U0010ffff]", "", str(post.get("hook") or "")).strip()[:100]
    body = str(post.get("body") or "").strip()[:3000]
    cta = str(post.get("cta") or "").strip()[:500]
    tags = [str(tag).strip()[:80] for tag in (post.get("hashtags") or []) if str(tag).strip()][:5]
    if not hook or not body or not cta or len(tags) < 3:
        raise ValueError("write_post ฟิลด์ไม่ครบหลังทำความสะอาด")
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
