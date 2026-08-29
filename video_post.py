#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations

import json
import logging
import re
from io import BytesIO
from pathlib import Path

from PIL import Image

import ai_client
import video_draft as vd
from ai_client import chat_json
from football_poster import http_get, search_rss_image
from hf_image import generate_hf_image

ai_client._hf_image = generate_hf_image
LOG = logging.getLogger("video_post")
THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp)(?:$|\?)", re.I)
MAX_PHOTO_BYTES = 4_000_000
SCENE_LABELS = {"สรุปสั้น", "สรุปข่าว", "สรุปข่าวสั้น"}
SKIP_NEWS = (
    "quiz", "quizzes", "puzzle", "crossword", "podcast", "newsletter",
    "fantasy football", "predictor", "prediction game", "daily quiz",
    "flex your football", "test your", "brain teaser", "trivia",
    "live text", "as it happened",
)
KEEP_NEWS = (
    "football", "soccer", "premier league", "la liga", "bundesliga", "serie a",
    "ligue 1", "champions league", "europa league", "world cup", "euros",
    "transfer", "midfielder", "striker", "goalkeeper", "winger", "manager",
    "sacked", "signed", "hat-trick", "match", "goal", "fixture",
    "ฟุตบอล", "บอล", "พรีเมียร์", "ชามเปียนส์", "ย้ายทีม",
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


def first_sentence(text: str, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[ฮ.!\n])\s+", text, maxsplit=1)
    return parts[0].strip()[:limit]


def thai_or(text: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if has_thai(text) else fallback


def fallback_storyboard(item):
    return finalize_storyboard(item, {
        "hook": "เกิดประเด็นร้อนในวงการลูกหนัง",
        "body": "รายละเอียดอยู่ในข่าวต้นทาง ติดตามให้ครบก่อนแชร์",
        "cta": "แฟนบอลมองเรื่องนี้ยังไงครับ?",
        "hashtags": ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": "comedy",
    })


def finalize_storyboard(item, data: dict) -> dict:
    hook = thai_or(str(data.get("hook") or ""), "เกิดประเด็นร้อนในวงการลูกหนัง")[:80]
    body = thai_or(str(data.get("body") or ""), "รายละเอียดอยู่ในข่าวต้นทาง ติดตามให้ครบก่อนแชร์")[:400]
    recap = first_sentence(body, 90) or "รายละเอียดอยู่ในข่าวต้นทาง อย่าแชร์ก่อนเช็ก"
    if recap == hook:
        recap = "รายละเอียดอยู่ในข่าวต้นทาง อย่าแชร์ก่อนเช็ก"
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    style = str(data.get("music_style", "comedy")).strip().lower()
    if style not in vd.MUSIC_STYLES:
        style = "comedy"
    return {
        "scenes": [
            {"title": "เกิดอะไรขึ้น", "line": hook[:90], "narration": hook[:140], "image_prompt": "real football photo 1"},
            {"title": recap[:90], "line": "", "narration": body[:140], "image_prompt": "real football photo 2"},
        ],
        "caption": thai_or(str(data.get("caption") or ""), hook)[:500],
        "hook": hook,
        "body": body,
        "cta": thai_or(str(data.get("cta") or ""), "แฟนบอลมองเรื่องนี้ยังไงครับ?")[:120],
        "hashtags": [str(tag).strip()[:40] for tag in tags if str(tag).strip()][:5] or ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": style,
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
            "hook และ body ต้องเป็นภาษาไทยล้วน ห้ามคัดลอกหัวข้ออังกฤษ. "
            "hook ไม่เกิน 18 คำ body 1-2 ประโยคเข้าใจง่าย. "
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
        return fallback_storyboard(item)
    if not isinstance(data, dict):
        return fallback_storyboard(item)
    board = finalize_storyboard(item, data)
    if not has_thai(board["hook"]) or not has_thai(board["scenes"][1]["title"]):
        return fallback_storyboard(item)
    return board


def _wrap_chars(draw, text: str, font, width: int):
    text = " ".join(str(text).split())
    if not text:
        return []
    lines, current = [], ""
    for char in text:
        candidate = current + char
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
            continue
        if current.strip():
            lines.append(current.strip())
            current = "" if char == " " else char
            continue
        lines.append(char)
        current = ""
    if current.strip():
        lines.append(current.strip())
    return lines or [text]


_orig_draw = vd.draw_scene
_orig_fetch = vd.fetch_feed
_orig_validate = vd.validate_news


def draw_scene(base, scene, scene_index, font_path):
    scene = dict(scene or {})
    title = str(scene.get("title") or "").strip()
    if title in SCENE_LABELS:
        scene["title"] = str(scene.get("line") or "")
        scene["line"] = ""
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


def _photo_key(url: str) -> str:
    return re.sub(r"[?#].*$", "", str(url or "")).lower().rstrip("/")


def _looks_like_photo(url: str) -> bool:
    lowered = url.lower()
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
    for page in pages.values():
        source = (page.get("thumbnail") or {}).get("source") or ""
        title = str(page.get("title") or "").lower()
        if source and any(word in title for word in ("football", "soccer", "f.c", "fc ", "club", "stadium")):
            yield source, "Wikipedia", page.get("title", query)


def collect_real_photos(item) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    if item.image_url:
        _add_photo(found, seen, item.image_url, item.image_source or "RSS", item.image_credit)
    try:
        url, source, credit = search_rss_image(item)
        _add_photo(found, seen, url, source, credit)
    except Exception as exc:
        LOG.warning("RSS photo search failed: %s", exc)
    words = re.findall(r"[A-Za-z0-9\-']+", f"{item.title} {item.summary}")
    queries = [
        " ".join(words[:4]) + " football",
        " ".join(words[:2]) + " football club",
        "Premier League football match",
        "Champions League football",
        "association football player portrait",
    ]
    for query in queries:
        if len(found) >= 8:
            break
        try:
            for url, source, credit in search_wikipedia_thumbs(query.strip()[:80]):
                _add_photo(found, seen, url, source, credit)
        except Exception as exc:
            LOG.warning("Wikipedia thumb search failed (%s): %s", query[:40], exc)
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
        if not saved:
            raise RuntimeError(f"Need two different real photos; failed on scene {index + 1}: {last_error}")
    return results


vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images
vd.draw_scene = draw_scene
vd.wrap = _wrap_chars
vd.fetch_feed = fetch_feed
vd.validate_news = validate_news


if __name__ == "__main__":
    raise SystemExit(vd.main())
