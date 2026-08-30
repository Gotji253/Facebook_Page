#!/usr/bin/env python3
"""Grade football stories before a poster or video goes out."""
from __future__ import annotations

import logging
import re
from typing import Any

LOG = logging.getLogger("news_grade")
POSTER_MIN = 65
VIDEO_MIN = 80

GOSSIP_RE = re.compile(
    r"rumours?|gossip|paper talk\b|transfer news\s*:|around the grounds|"
    r"รวมข่าว|ข่าวลือรวม|ตามตลาดวัน|ซุบซิบ",
    re.I,
)
BIG_CLUBS = (
    "liverpool", "arsenal", "chelsea", "manchester city", "man city",
    "manchester united", "man utd", "tottenham", "newcastle", "aston villa",
    "barcelona", "real madrid", "bayern", "psg", "juventus", "inter",
    "ac milan", "napoli", "ลิเวอร์พูล", "อาร์เซนอล", "เชลซี", "แมนซิตี้",
    "แมนยู", "ท็อตแน่ม", "นิวคาสเซิล",
)
BIG_PLAYERS = (
    "salah", "saka", "haaland", "mbappe", "yamal", "rice", "palmer",
    "isak", "gyokeres", "gakpo", "martinez", "tonali", "นาย", "ธีราทร",
)
HARD_NEWS = (
    "sign", "signs", "signed", "deal", "transfer", "loan", "sack", "sacked",
    "appoint", "ban", "injury", "injured", "wins", "won", "beat", "beats",
    "ย้าย", "เซ็น", "พักงาน", "ไล่", "โดนแบน", "บาดเจ็บ", "ชนะ", "พ่าย",
)
TALK_NEWS = (
    "why", "how", "should", "must", "backlash", "furious", "crisis",
    "ทำไม", "ควร", "แฟนบอล", "ดราม่า", "เดือด",
)


def _text(item) -> str:
    return f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}".lower()


def is_gossip(item) -> bool:
    text = _text(item)
    if GOSSIP_RE.search(text):
        return True
    if text.count(",") >= 4 and any(word in text for word in ("transfer", "sign", "loan")):
        return True
    return False


def rule_score(item) -> dict[str, Any]:
    text = _text(item)
    who = 8
    if any(name in text for name in BIG_CLUBS):
        who += 12
    if any(name in text for name in BIG_PLAYERS):
        who += 5
    who = min(25, who)

    what = 8
    if any(word in text for word in HARD_NEWS):
        what += 12
    if any(word in text for word in ("hat-trick", "winner", "sacked", "permanent")):
        what += 5
    what = min(25, what)

    timing = 12
    published = str(getattr(item, "published", "") or "")
    if published:
        timing = 18
    timing = min(20, timing)

    tell = 6
    if re.search(r"\d", text):
        tell += 5
    if len(str(getattr(item, "summary", "") or "")) >= 80:
        tell += 4
    tell = min(15, tell)

    debate = 4
    if any(word in text for word in TALK_NEWS):
        debate += 8
    debate = min(15, debate)

    total = who + what + timing + tell + debate
    if is_gossip(item):
        total = min(total, 40)
    return {
        "who": who,
        "what": what,
        "timing": timing,
        "tell": tell,
        "debate": debate,
        "rule_score": max(0, min(100, total)),
        "gossip": is_gossip(item),
    }


def finalize(item, llm_row: dict[str, Any] | None = None, minimum: int = POSTER_MIN) -> dict[str, Any]:
    local = rule_score(item)
    llm_score = float((llm_row or {}).get("score", 0) or 0)
    if llm_row:
        score = round((0.6 * llm_score) + (0.4 * local["rule_score"]), 1)
        angle = str((llm_row or {}).get("main_angle", ""))[:500]
        reason = str((llm_row or {}).get("reason", ""))[:1000]
    else:
        score = float(local["rule_score"])
        angle = getattr(item, "title", "")[:500]
        reason = "ให้คะแนนจากกติกาเพจ"
    if local["gossip"]:
        score = min(score, 40)
    worthy = (not local["gossip"]) and score >= minimum
    row = {
        "id": getattr(item, "id", ""),
        "score": max(0, min(100, score)),
        "is_worthy": worthy,
        "main_angle": angle,
        "reason": reason,
        "minimum": minimum,
        **local,
    }
    LOG.info(
        "Grade score=%s min=%s worthy=%s gossip=%s | %s",
        row["score"],
        minimum,
        worthy,
        local["gossip"],
        getattr(item, "title", "")[:90],
    )
    return row


def pick(items: list, scores: dict[str, dict[str, Any]], minimum: int):
    best = None
    best_score = -1.0
    for item in items:
        row = scores.get(getattr(item, "id", "")) or finalize(item, None, minimum)
        if not row.get("is_worthy"):
            continue
        score = float(row.get("score", 0) or 0)
        if score >= minimum and score > best_score:
            best = item
            best_score = score
    return best
