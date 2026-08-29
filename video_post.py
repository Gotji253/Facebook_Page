#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations

import html
import json
import logging
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image, ImageOps

import ai_client
import video_draft as vd
from ai_client import chat_json
from football_poster import http_get, image_url_variants, search_rss_image
from hf_image import generate_hf_image

ai_client._hf_image = generate_hf_image
LOG = logging.getLogger("video_post")
THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
THAI_MARK = re.compile(r"[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]")
IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp)(?:$|\?)", re.I)
SCORE_RE = re.compile(r"\b\d{1,2}\s*[-\u2013]\s*\d{1,2}\b")
NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b")
STORY_SPLIT = re.compile(r"\s*(?:นอกจากนี้|ในขณะเดียวกัน|ส่วน(?=[ก-ฮ])|meanwhile|separately)\s+", re.I)
MAX_PHOTO_BYTES = 4_000_000
SCENE_LABELS = {"สรุปสั้น", "สรุปข่าว", "สรุปข่าวสั้น"}
STOP_TOKENS = {
    "เกิดอะไรขึ้น", "นอกจากนี้", "อย่าง", "ใกล้ชิด", "สนใจ", "สถานการณ์",
    "ของ", "และ", "ที่", "ใน", "จะ", "ได้", "ไม่", "กับ", "จาก", "เพื่อ", "ส่วน", "ยัง",
}
SKIP_NEWS = (
    "quiz", "quizzes", "puzzle", "crossword", "podcast", "newsletter",
    "fantasy football", "predictor", "prediction game", "daily quiz",
    "flex your football", "test your", "brain teaser", "trivia",
    "live text", "as it happened", "gossip", "rumour mill", "transfer rumours",
    "iplayer", "match of the day", "motd", "watch live", "live stream",
)
KEEP_NEWS = (
    "football", "soccer", "premier league", "la liga", "bundesliga", "serie a",
    "ligue 1", "champions league", "europa league", "world cup", "euros",
    "transfer", "midfielder", "striker", "goalkeeper", "winger", "manager",
    "sacked", "signed", "hat-trick", "match", "goal", "fixture",
    "ฟุตบอล", "บอล", "พรีเมียร์", "ชามเปียนส์", "ย้ายทีม",
)
GENERIC_FALLBACK = (
    "เกิดประเด็นร้อนในวงการลูกหนัง",
    "รายละเอียดอยู่ในข่าวต้นทาง",
)
NAME_SKIP = {
    "The", "And", "For", "With", "From", "This", "That", "After", "Before",
    "Against", "Premier", "League", "United", "City", "News", "Sport",
    "Football", "Saturday", "Sunday", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Live", "Watch", "How", "Why", "What", "When",
}
CLUB_MAP = {
    "tottenham": "Tottenham Hotspur",
    "spurs": "Tottenham Hotspur",
    "hotspur": "Tottenham Hotspur",
    "สเปอร์ส": "Tottenham Hotspur",
    "ไก่เดือยทอง": "Tottenham Hotspur",
    "newcastle": "Newcastle United",
    "นิวคาส": "Newcastle United",
    "สาลิกาดง": "Newcastle United",
    "liverpool": "Liverpool F.C.",
    "ลิเวอร์พูล": "Liverpool F.C.",
    "arsenal": "Arsenal F.C.",
    "อาร์เซนอล": "Arsenal F.C.",
    "chelsea": "Chelsea F.C.",
    "เชลซี": "Chelsea F.C.",
    "manchester city": "Manchester City",
    "man city": "Manchester City",
    "แมนซิตี้": "Manchester City",
    "manchester united": "Manchester United",
    "man united": "Manchester United",
    "แมนยู": "Manchester United",
    "barcelona": "FC Barcelona",
    "บาร์ซา": "FC Barcelona",
    "บาร์เซโลนา": "FC Barcelona",
    "real madrid": "Real Madrid",
    "เรอัลมาดริด": "Real Madrid",
    "bayern": "Bayern Munich",
    "บาเยิร์น": "Bayern Munich",
    "psg": "Paris Saint-Germain",
    "เปแอสเช": "Paris Saint-Germain",
    "milan": "AC Milan",
    "มิลาน": "AC Milan",
    "inter": "Inter Milan",
    "อินเตอร์": "Inter Milan",
    "juventus": "Juventus",
    "ยูเวนตุส": "Juventus",
    "dortmund": "Borussia Dortmund",
    "ดอร์ทมุนด์": "Borussia Dortmund",
    "atletico": "Atletico Madrid",
    "แอตเลติโก": "Atletico Madrid",
    "west ham": "West Ham United",
    "เวสต์แฮม": "West Ham United",
    "aston villa": "Aston Villa",
    "แอสตันวิลลา": "Aston Villa",
    "brighton": "Brighton & Hove Albion",
    "ไบรท์ตัน": "Brighton & Hove Albion",
    "everton": "Everton F.C.",
    "เอฟเวอร์ตัน": "Everton F.C.",
}
THAI_BREAKS = (
    "ขณะที่", "ทำให้", "พร้อม", "เพราะ", "หลังจาก", "ก่อนที่",
    "โดยที่", "แต่", "โดย", "และ", "กับ", "จาก", "ของ", "ที่",
    "ใน", "เพื่อ", "จน", "เลย", "ยัง", "จะ", "ได้", "ไม่", "กว่า",
)


def has_thai(text: str) -> bool:
    return bool(THAI_RE.search(text or ""))


def news_text(item) -> str:
    return f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')} {getattr(item, 'url', '')}".lower()


def is_football_news(item) -> bool:
    text = news_text(item)
    if any(word in text for word in SKIP_NEWS):
        return False
    return any(word in text for word in KEEP_NEWS)


def one_story(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return STORY_SPLIT.split(text, maxsplit=1)[0].strip(" ,.")


def story_tokens(text: str) -> set[str]:
    found = set(re.findall(r"[\u0E00-\u0E7F]{3,}|[A-Za-z]{4,}", text or ""))
    return {token.lower() for token in found if token not in STOP_TOKENS}


def thai_clusters(text: str) -> list[str]:
    clusters: list[str] = []
    for char in text:
        if clusters and THAI_MARK.fullmatch(char):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def complete_phrase(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text.rstrip(" ,;:-/")
    acc = ""
    for cluster in thai_clusters(text):
        if acc and len(acc) + len(cluster) > limit:
            break
        acc += cluster
    rest = text[len(acc):]
    if rest and not acc.endswith(" ") and not rest[0].isspace():
        extra = re.match(r"\S{1,20}", rest)
        if extra:
            acc += extra.group(0)
    elif " " in acc and not acc.endswith(" "):
        acc = acc.rsplit(" ", 1)[0]
    return acc.rstrip(" ,;:-/")


def finish_last_word(short: str, long: str, limit: int) -> str:
    short = re.sub(r"\s+", " ", str(short or "")).strip()
    long = re.sub(r"\s+", " ", str(long or "")).strip()
    if not short:
        return complete_phrase(long, limit)
    if long.startswith(short):
        rest = long[len(short):]
        if rest and not short.endswith(" ") and not rest[0].isspace():
            more = re.match(r"\S+", rest)
            if more:
                short = short + more.group(0)
        return complete_phrase(short, limit + 12)
    last = short.split()[-1]
    for token in long.split():
        if token.startswith(last) and len(token) > len(last):
            rebuilt = short[: short.rfind(last)] + token
            return complete_phrase(rebuilt, limit + 12)
    return complete_phrase(short, limit)


HANGING_TAILS = {
    "สุด", "อย่าง", "ด้วย", "เพื่อ", "ที่", "ของ", "และ", "จะ", "ได้", "ไม่",
    "กับ", "จาก", "ส่วน", "ยัง", "ให้", "มา", "ไป", "อยู่", "แล้ว", "กว่า",
    "แบบ", "ความ", "การ", "ใน", "ต่อ", "หลัง", "เพราะ", "แต่", "เลย", "จน",
}


def next_source_token(short: str, long: str) -> str:
    short = re.sub(r"\s+", " ", short or "").strip()
    long = re.sub(r"\s+", " ", long or "").strip()
    if long.startswith(short):
        rest = long[len(short):].lstrip()
        match = re.match(r"\S+", rest)
        return match.group(0) if match else ""
    tokens = long.split()
    parts = short.split()
    if not parts:
        return tokens[0] if tokens else ""
    for index, token in enumerate(tokens):
        if token == parts[-1] and index + 1 < len(tokens):
            if parts == tokens[index + 1 - len(parts): index + 1]:
                return tokens[index + 1]
    return ""


def finish_phrase(short: str, long: str, limit: int) -> str:
    text = finish_last_word(short, long, limit)
    while text:
        last = text.split()[-1]
        if last not in HANGING_TAILS:
            break
        nxt = next_source_token(text, long)
        if nxt:
            candidate = f"{text} {nxt}".strip()
            if len(candidate) <= limit + 24:
                text = candidate
                continue
        if " " in text:
            text = text.rsplit(" ", 1)[0]
        break
    return text.strip()


def source_scores(item) -> list[str]:
    source = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
    scores = []
    for score in SCORE_RE.findall(source):
        compact = re.sub(r"\s+", "", score).replace("\u2013", "-")
        if compact not in scores:
            scores.append(compact)
    return scores


def with_score(text: str, scores: list[str], limit: int) -> str:
    compact = re.sub(r"\s+", "", text or "")
    for score in scores:
        if score not in compact and score.replace("-", "") not in compact:
            text = complete_phrase(f"{text} {score}".strip(), limit + 8)
            compact = re.sub(r"\s+", "", text)
    return text


def first_sentence(text: str, limit: int = 56) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[ฮ.!\n])\s+", text, maxsplit=1)
    return complete_phrase(parts[0].strip(), limit)


def thai_or(text: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if has_thai(text) else fallback


def readable_thai(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    if compact and (len(text) - len(compact)) >= max(3, len(compact) // 10):
        return text
    spaced = text
    for word in THAI_BREAKS:
        spaced = re.sub(rf"(?<!\s)({re.escape(word)})(?!\s)", r" \1 ", spaced)
    return re.sub(r"\s+", " ", spaced).strip()


def news_names(item) -> list[str]:
    blob = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
    names = []
    for name in NAME_RE.findall(blob):
        first = name.split()[0]
        if first in NAME_SKIP or name in names:
            continue
        names.append(name)
    return names[:4]


def news_clubs(item) -> list[str]:
    blob = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}".lower()
    clubs = []
    for key, club in sorted(CLUB_MAP.items(), key=lambda row: len(row[0]), reverse=True):
        if key in blob and club not in clubs:
            clubs.append(club)
    return clubs[:3]


def photo_queries(item) -> list[str]:
    names = news_names(item)
    clubs = news_clubs(item)
    queries: list[str] = []
    for name in names:
        queries.append(f"{name} footballer")
        queries.append(f"{name} football")
        for club in clubs[:2]:
            queries.append(f"{name} {club}")
    for club in clubs:
        queries.append(f"{club} F.C.")
        queries.append(f"{club} football club")
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", f"{item.title} {item.summary}") if word not in NAME_SKIP]
    if words:
        queries.append(" ".join(words[:4]) + " football")
    seen: set[str] = set()
    unique = []
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique[:10]


def scene_visible(scene: dict) -> str:
    return " ".join(str(scene.get(key) or "").strip() for key in ("title", "line")).strip()


def same_story(scene1: str, scene2: str) -> bool:
    left = story_tokens(scene1)
    right = story_tokens(scene2)
    return bool(left and right and (left & right))


def tidy_wrap_lines(lines: list[str]) -> list[str]:
    cleaned = [line.strip() for line in lines if line and line.strip()]
    while len(cleaned) >= 2 and len(re.sub(r"\s+", "", cleaned[-1])) <= 6:
        prev, last = cleaned[-2], cleaned[-1]
        cleaned[-2:] = [prev + last]
    return [line for line in cleaned if line]


def looks_truncated(text: str) -> bool:
    text = (text or "").strip()
    if len(text) < 8:
        return True
    if text[-1] in ".!ฮ?…":
        return False
    last = text.split()[-1] if text.split() else text
    if re.fullmatch(r"\d{1,2}[-–]\d{1,2}", last):
        return False
    if last in HANGING_TAILS:
        return True
    if 1 <= len(re.sub(r"\s+", "", last)) <= 3 and text[-1] not in ".!ฮ?…":
        return True
    return False


def review_on_screen_text(item, storyboard: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    scenes = storyboard.get("scenes") or []
    hook = str(storyboard.get("hook") or "").strip()
    body = str(storyboard.get("body") or "").strip()
    if len(scenes) != 2:
        errors.append("ต้องมี 2 ฉาก")
        return {"ok": False, "errors": errors, "warnings": warnings, "checked": {}}
    s1 = scene_visible(scenes[0])
    s2 = scene_visible(scenes[1])
    if not has_thai(s1):
        errors.append("ฉาก 1 ไม่มีข้อความภาษาไทย")
    if not has_thai(s2):
        errors.append("ฉาก 2 ไม่มีข้อความภาษาไทย")
    if len(re.sub(r"\s+", "", s1)) < 12:
        errors.append("ข้อความฉาก 1 สั้นเกินไป")
    if len(re.sub(r"\s+", "", s2)) < 12:
        errors.append("ข้อความฉาก 2 สั้นเกินไป")
    if str(scenes[1].get("title") or "").strip() in SCENE_LABELS:
        errors.append("ฉาก 2 ยังมีหัวสรุปสั้น")
    if looks_truncated(str(scenes[0].get("line") or "")):
        errors.append("ข้อความฉาก 1 ถูกตัดกลาง")
    if looks_truncated(str(scenes[1].get("title") or "") or str(scenes[1].get("line") or "")):
        errors.append("ข้อความฉาก 2 ถูกตัดกลาง")
    if not same_story(s1, s2):
        errors.append("ฉาก 1 กับฉาก 2 ไม่ใช่ข่าวเดียวกัน")
    source = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
    clip = f"{hook} {body} {s1} {s2}"
    for score in SCORE_RE.findall(source):
        compact = re.sub(r"\s+", "", score)
        if compact not in re.sub(r"\s+", "", clip) and score not in clip:
            errors.append(f"สกอร์ในข่าวต้นทางคือ {score} แต่ไม่อยู่บนคลิป")
    names = news_names(item)
    if names and not any(name.split()[-1].lower() in clip.lower() for name in names[:3]):
        warnings.append("ชื่อในหัวข้อข่าวไม่ขึ้นบนข้อความคลิป")
    if any(phrase in clip for phrase in GENERIC_FALLBACK) and len(getattr(item, "title", "") or "") > 20:
        warnings.append("ข้อความยังเป็นประโยคทั่วไป")
    checked = {"scene1": s1, "scene2": s2, "hook": hook, "body": body[:160]}
    ok = not errors
    LOG.info("Clip review ok=%s errors=%s warnings=%s", ok, errors, warnings)
    return {"ok": ok, "errors": errors, "warnings": warnings, "checked": checked}


def fallback_storyboard(item):
    board = finalize_storyboard(item, {
        "hook": "เกิดประเด็นร้อนในวงการลูกหนัง",
        "body": "รายละเอียดอยู่ในข่าวต้นทาง ติดตามให้ครบก่อนแชร์",
        "cta": "แฟนบอลมองเรื่องนี้ยังไงครับ?",
        "hashtags": ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": "comedy",
    })
    board["clip_review"] = review_on_screen_text(item, board)
    return board


def finalize_storyboard(item, data: dict, limit: int = 110) -> dict:
    raw_hook = thai_or(str(data.get("hook") or ""), "เกิดประเด็นร้อนในวงการลูกหนัง")
    raw_body = thai_or(str(data.get("body") or ""), raw_hook)
    body = one_story(raw_body) or raw_hook
    scores = source_scores(item)
    hook = readable_thai(finish_phrase(complete_phrase(raw_hook, limit), body, limit))
    hook = with_score(hook, scores, limit)
    recap = readable_thai(first_sentence(body, 140) or complete_phrase(body, 140))
    recap = finish_phrase(recap, body, 140)
    hook_key = re.sub(r"\s+", "", hook)
    recap_key = re.sub(r"\s+", "", recap)
    if recap_key == hook_key or recap_key.startswith(hook_key) or len(recap_key) < 18:
        rest = body
        if hook and hook in body:
            rest = body.split(hook, 1)[-1]
        extra = readable_thai(complete_phrase(rest or body, 140))
        recap = extra if re.sub(r"\s+", "", extra) != hook_key else readable_thai(complete_phrase(body, 140))
        recap = finish_phrase(recap, body, 140)
    recap = readable_thai(with_score(recap, scores, 140))
    hook = readable_thai(hook)
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    style = str(data.get("music_style", "comedy")).strip().lower()
    if style not in vd.MUSIC_STYLES:
        style = "comedy"
    return {
        "scenes": [
            {"title": "เกิดอะไรขึ้น", "line": hook, "narration": hook[:140], "image_prompt": "real football photo 1"},
            {"title": recap, "line": "", "narration": body[:180], "image_prompt": "real football photo 2"},
        ],
        "caption": thai_or(str(data.get("caption") or ""), hook)[:500],
        "hook": hook,
        "body": body,
        "cta": thai_or(str(data.get("cta") or ""), "แฟนบอลมองเรื่องนี้ยังไงครับ?")[:120],
        "hashtags": [str(tag).strip()[:40] for tag in tags if str(tag).strip()][:5] or ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": style,
        "_source_data": {
            "hook": str(data.get("hook") or ""),
            "body": str(data.get("body") or hook),
            "cta": str(data.get("cta") or ""),
            "hashtags": tags,
            "music_style": style,
            "caption": str(data.get("caption") or ""),
        },
    }


def generate_storyboard(item):
    vd.validate_news(item)
    if not is_football_news(item):
        raise ValueError("ข่าวนี้ไม่ใช่ข่าวฟุตบอล")
    request = {
        "title": item.title[:180],
        "summary": item.summary[:280],
        "source": item.source,
        "instruction": (
            "ตอบ JSON สั้น มี hook, body, cta, hashtags, music_style. "
            "hook และ body ต้องเป็นภาษาไทยล้วน และต้องเล่าเรื่องเดียวกันเท่านั้น. "
            "ห้ามยัดข่าวซุบซิบหรือข่าวหลายเรื่องใน body. "
            "hook 1 ประโยค 16-22 คำ เว้นวรรคทุกคำ อ่านง่าย. "
            "body คนละประโยคกับ hook ต้องมีเหตุผลสั้นๆ และเว้นวรรคทุกคำ. "
            "music_style เป็น hype, triumph, tense, comedy หรือ calm"
        ),
    }
    try:
        data = chat_json(
            "บรรณาธิการข่าวฟุตบอลไทย ตอบ JSON ภาษาไทยล้วน",
            json.dumps(request, ensure_ascii=False),
        )
    except Exception as exc:
        LOG.warning("Storyboard AI failed; using fallback: %s", exc)
        data = {}
    if not isinstance(data, dict):
        data = {}
    board = finalize_storyboard(item, data, 110)
    if not has_thai(board["hook"]) or not has_thai(board["scenes"][1]["title"]):
        board = fallback_storyboard(item)
        data = board.get("_source_data") or data
    review = review_on_screen_text(item, board)
    if not review["ok"]:
        LOG.warning("Clip text failed first review, rewriting same-story recap: %s", review["errors"])
        data = dict(data or board.get("_source_data") or {})
        data["body"] = data.get("hook") or board.get("hook")
        board = finalize_storyboard(item, data, 110)
        review = review_on_screen_text(item, board)
    board["clip_review"] = review
    vd._clip_review = review
    if not review["ok"]:
        LOG.error("Clip text still failed review: %s", review["errors"])
    return board


_orig_draw = vd.draw_scene
_orig_fetch = vd.fetch_feed
_orig_validate = vd.validate_news
_orig_publish = vd.publish_video


def draw_scene(base, scene, scene_index, font_path):
    scene = dict(scene or {})
    title = str(scene.get("title") or "").strip()
    if title in SCENE_LABELS:
        scene["title"] = str(scene.get("line") or "")
        scene["line"] = ""
    scene["title"] = readable_thai(scene.get("title") or "")
    scene["line"] = readable_thai(scene.get("line") or "")
    return _orig_draw(base, scene, scene_index, font_path)


def fetch_feed(source, url):
    kept = []
    for item in _orig_fetch(source, url):
        if is_football_news(item):
            kept.append(item)
        else:
            LOG.info("Skip non-football item: %s", getattr(item, "title", "")[:80])
    return kept


def validate_news(item) -> None:
    _orig_validate(item)
    if not is_football_news(item):
        raise ValueError("ข้ามควิซ พอดคาสต์ หรือคอนเทนต์ทั่วไป ใช้เฉพาะข่าวฟุตบอล")


def publish_video(video, caption, page_id, token):
    review = getattr(vd, "_clip_review", None) or {}
    if not review.get("ok"):
        raise RuntimeError("ยังไม่โพสต์ ข้อความบนคลิปไม่ผ่าน: " + "; ".join(review.get("errors") or ["ไม่พบผลตรวจสอบคลิป"]))
    return _orig_publish(video, caption, page_id, token)


OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::url)?|twitter:image(?::src)?)["\'][^>]+content=["\']([^"\']+)',
    re.I,
)
OG_IMAGE_RE_FLIP = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image(?::url)?|twitter:image(?::src)?)["\']',
    re.I,
)
IMG_TAG_RE = re.compile(
    r'<img[^>]+(?:src|data-src|data-original|data-lazy-src|data-image)=["\']([^"\']+)',
    re.I,
)
SRCSET_RE = re.compile(r'(?:srcset|data-srcset)=["\']([^"\']+)', re.I)
HTTP_IMG_RE = re.compile(r'(https?:)?//[^\s,"\']+\.(?:jpe?g|png|webp)', re.I)
SKIP_IMG_HINTS = (
    "logo", "sprite", "icon", "avatar", "pixel", "1x1", "advert", "adservice",
    "placeholder", "blank.gif", "share-icon", "social", "amp-logo", "badge",
    "favicon", "emoji", "tracking", "spacer",
)


def _abs_image_url(base: str, raw: str) -> str:
    raw = html.unescape(str(raw or "").strip())
    if not raw or raw.startswith("data:"):
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    return urljoin(base, raw)


def scrape_article_images(item) -> list[tuple[str, str, str]]:
    page_url = str(getattr(item, "url", "") or "").strip()
    if not page_url.startswith("http"):
        return []
    try:
        page = http_get(page_url).text[:450_000]
    except Exception as exc:
        LOG.warning("Article image scrape failed: %s", exc)
        return []
    candidates: list[str] = []
    for regex in (OG_IMAGE_RE, OG_IMAGE_RE_FLIP):
        candidates.extend(regex.findall(page))
    candidates.extend(IMG_TAG_RE.findall(page))
    for srcset in SRCSET_RE.findall(page):
        parts = HTTP_IMG_RE.findall(srcset)
        if parts:
            last = parts[-1]
            candidates.append(last if last.startswith("http") or last.startswith("//") else parts[-1])
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for raw in candidates:
        url = _abs_image_url(page_url, raw)
        if not url or not _looks_like_photo(url):
            continue
        lowered = url.lower()
        if any(hint in lowered for hint in SKIP_IMG_HINTS):
            continue
        key = _photo_key(url)
        if key in seen:
            continue
        seen.add(key)
        source = f"{getattr(item, 'source', '') or 'article'} article"
        credit = f"ภาพจากข่าว: {page_url}"
        found.append((url, source, credit))
        for variant in image_url_variants(url):
            vkey = _photo_key(variant)
            if vkey in seen:
                continue
            seen.add(vkey)
            found.append((variant, source, credit))
        if len(found) >= 8:
            break
    LOG.info("Article scrape found %d image candidates from %s", len(found), page_url[:80])
    return found


def _photo_key(url: str) -> str:
    return re.sub(r"[?#].*$", "", str(url or "")).lower().rstrip("/")


def _looks_like_photo(url: str) -> bool:
    lowered = url.lower()
    news_hosts = (
        "pollinations.ai", "openverse.org", "wikimedia.org", "bbci.co.uk",
        "guim.co.uk", "theguardian.com", "espncdn.com", "fourfourtwo.com",
        "goal.com", "skysports.com", "cloudfront.net",
    )
    if any(host in lowered for host in news_hosts):
        if any(bad in lowered for bad in (".pdf", ".svg", ".tif", ".tiff", ".gif", "tiny_town")):
            return False
        return True
    if any(bad in lowered for bad in (".pdf", ".svg", ".tif", ".tiff", ".gif", "tiny_town")):
        return False
    return bool(IMAGE_EXT_RE.search(url))


def _add_photo(found: list, seen: set, url: str, source: str, credit: str) -> None:
    if not url or not _looks_like_photo(url):
        return
    key = _photo_key(url)
    if not key or key in seen:
        return
    seen.add(key)
    found.append({"url": url, "source": source, "credit": credit})


def search_wikipedia_thumbs(query: str):
    response = http_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": 8,
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": 1280,
            "format": "json",
        },
    )
    pages = (response.json().get("query") or {}).get("pages") or {}
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]{3,}", query)]
    sport_words = ("football", "soccer", "f.c", "fc ", "club", "stadium", "manager", "player")
    for page in pages.values():
        source = (page.get("thumbnail") or {}).get("source") or ""
        title = str(page.get("title") or "").lower()
        if not source:
            continue
        titled = any(word in title for word in sport_words) or any(token in title for token in tokens)
        if titled:
            yield source, "Wikipedia", page.get("title", query)


def search_openverse(query: str):
    response = http_get(
        "https://api.openverse.org/v1/images/",
        params={"q": f"{query} football", "page_size": 8},
    )
    for row in response.json().get("results") or []:
        url = row.get("url") or ""
        if url:
            yield url, "Openverse", row.get("title") or query


def collect_real_photos(item) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    if item.image_url:
        for variant in image_url_variants(item.image_url) or [item.image_url]:
            _add_photo(found, seen, variant, item.image_source or "RSS", item.image_credit or item.title)
    try:
        for url, source, credit in scrape_article_images(item):
            _add_photo(found, seen, url, source, credit)
    except Exception as exc:
        LOG.warning("Article photo scrape failed: %s", exc)
    try:
        url, source, credit = search_rss_image(item)
        _add_photo(found, seen, url, source, credit)
    except Exception as exc:
        LOG.warning("RSS photo search failed: %s", exc)
    article_count = len(found)
    LOG.info("News-page photos collected: %s", article_count)
    queries = photo_queries(item)
    LOG.info("Photo queries: %s", queries[:6])
    if len(found) < 2:
        for query in queries:
            if len(found) >= 4:
                break
            try:
                for url, source, credit in search_wikipedia_thumbs(query.strip()[:80]):
                    _add_photo(found, seen, url, source, credit)
            except Exception as exc:
                LOG.warning("Wikipedia thumb search failed (%s): %s", query[:40], exc)
    if len(found) < 2:
        for query in queries[:4] or ("football match",):
            try:
                for url, source, credit in search_openverse(query):
                    _add_photo(found, seen, url, source, credit)
            except Exception as exc:
                LOG.warning("Openverse search failed (%s): %s", query, exc)
    topic = " ".join(queries[:2]) or "football player club"
    encoded = re.sub(r"[^A-Za-z0-9]+", "%20", topic)[:120]
    _add_photo(
        found, seen,
        f"https://image.pollinations.ai/prompt/real%20photo%20{encoded}%20football?width=1080&height=1920&nologo=true",
        "Pollinations", topic,
    )
    _add_photo(
        found, seen,
        f"https://image.pollinations.ai/prompt/real%20photo%20{encoded}%20football%20club?width=1080&height=1920&nologo=true",
        "Pollinations", topic,
    )
    LOG.info("Collected %d real photo candidates", len(found))
    return found


def save_photo(url: str, path: Path) -> bytes:
    response = http_get(url)
    raw = response.content
    if len(raw) > MAX_PHOTO_BYTES:
        raise RuntimeError(f"Photo too large: {len(raw)} bytes")
    image = Image.open(BytesIO(raw)).convert("RGB")
    if image.size[0] < 400 or image.size[1] < 300:
        raise RuntimeError(f"Photo too small: {image.size}")
    image.save(path, format="JPEG", quality=88, optimize=True)
    return path.read_bytes()


def generate_scene_images(item, storyboard, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    photos = collect_real_photos(item)
    results = []
    used_bytes = set()
    last_error = None
    for index in range(vd.SCENE_COUNT):
        image_path = output_dir / f"scene_{index + 1:02d}.jpg"
        saved = False
        for photo in photos:
            url = str(photo.get("url") or "")
            try:
                data = save_photo(url, image_path)
                if data in used_bytes:
                    continue
                used_bytes.add(data)
                results.append({
                    "scene": index + 1,
                    "path": str(image_path),
                    "prompt": url,
                    "source": photo.get("source", "photo"),
                    "credit": photo.get("credit", ""),
                })
                saved = True
                LOG.info("Scene %s photo from %s", index + 1, photo.get("source"))
                photos = [item for item in photos if item.get("url") != url]
                break
            except Exception as exc:
                last_error = exc
                LOG.warning("Skip photo %s: %s", url[:90], exc)
        if not saved and results:
            alt = ImageOps.mirror(Image.open(results[0]["path"]).convert("RGB"))
            alt.save(image_path, format="JPEG", quality=82, optimize=True)
            results.append({
                "scene": index + 1,
                "path": str(image_path),
                "prompt": results[0]["prompt"],
                "source": "rss-alt",
                "credit": results[0].get("credit", ""),
            })
            saved = True
            LOG.warning("Scene %s used mirrored fallback photo", index + 1)
        if not saved:
            raise RuntimeError(f"Need two different real photos; failed on scene {index + 1}: {last_error}")
    return results


vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images
vd.draw_scene = draw_scene
vd.fetch_feed = fetch_feed
vd.validate_news = validate_news
vd.publish_video = publish_video


if __name__ == "__main__":
    raise SystemExit(vd.main())
