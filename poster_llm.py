#!/usr/bin/env python3
"""Poster ranking + caption via OpenAI, then Gemini, then Hugging Face."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import football_poster
from ai_client import chat_json

LOG = logging.getLogger("poster_llm")


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
            "บรรณาธิการข่าวฟุตบอลไทย ตอบ JSON เท่านั้น "
            'รูปแบบ {"items":[{"id":"","score":0,"is_worthy":true,"main_angle":"","reason":""}]} '
            "score 0-100 และ is_worthy=true เมื่อเหมาะโพสต์เพจข่าวฟุตบอลไทย"
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
    post_input = {
        "title": item.title[:300],
        "summary": str(getattr(item, "summary", "") or "")[:600],
        "source": getattr(item, "source", ""),
        "angle": score.get("main_angle", ""),
        "reason": score.get("reason", ""),
    }
    post = chat_json(
        (
            "เขียนโพสต์ข่าวฟุตบอลภาษาไทยล้วนและตอบเป็น JSON object เท่านั้น "
            "ห้ามใช้ Markdown หรือ code fence และห้ามใช้อีโมจิใน hook "
            "hook สั้นแรงไม่เกินประมาณ 40 ตัวอักษร "
            "body มี 3-5 บรรทัด เรียบเรียงไม่แปลตรงตัว "
            "cta ชวนคอมเมนต์ และ hashtags ภาษาไทย 3-5 รายการ"
        ),
        json.dumps(post_input, ensure_ascii=False),
    )
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


def patch() -> None:
    football_poster.rank_news = rank_news
    football_poster.write_post = write_post
