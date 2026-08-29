#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

LOG = logging.getLogger("slim_rank")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def rank_news(items: list) -> dict[str, dict[str, Any]]:
    payload = [
        {"id": item.id, "title": item.title[:140], "published": getattr(item, "published", "")[:40]}
        for item in items[:12]
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "บรรณาธิการข่าวฟุตบอลไทย ตอบ JSON สั้นเท่านั้น "
                "รูปแบบ {\"items\":[{\"id\":\"\",\"score\":0,\"is_worthy\":true}]} "
                "score 0-100 is_worthy=true เมื่อเหมาะโพสต์เพจไทย"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    request_args = {
        "model": env("OPENAI_MODEL", "gpt-5-mini"),
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 800,
    }
    client = OpenAI()
    try:
        response = client.chat.completions.create(**request_args, reasoning_effort="minimal")
    except Exception as exc:
        LOG.warning("slim rank minimal failed; retrying low: %s", exc)
        response = client.chat.completions.create(**request_args, reasoning_effort="low")
    raw = ((response.choices or [None])[0].message.content if response.choices else "") or ""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    parsed = json.loads(raw)
    results: dict[str, dict[str, Any]] = {}
    for item in parsed.get("items", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        ident = str(item["id"])
        results[ident] = {
            "id": ident,
            "score": max(0, min(100, float(item.get("score", 0)))),
            "is_worthy": bool(item.get("is_worthy", False)),
            "main_angle": "",
            "reason": "",
        }
    return results
