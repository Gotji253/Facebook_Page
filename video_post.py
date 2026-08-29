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


def has_thai(text: str) -> bool:
    return bool(THAI_RE.search(text or ""))


def first_sentence(text: str, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[។.!\n])\s+", text, maxsplit=1)
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
    recap = first_sentence(body, 90) or "สรุปสั้นจากข่าวต้นทาง อย่าแชร์ก่อนเช็ก"
    if recap == hook:
        recap = "สรุปสั้นจากข่าวต้นทาง อย่าแชร์ก่อนเช็ก"
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    style = str(data.get("music_style", "comedy")).strip().lower()
    if style not in vd.MUSIC_STYLES:
        style = "comedy"
    return {
        "scenes": [
            {"title": "เกิดอะไรขึ้น", "line": hook[:90], "narration": hook[:140], "image_prompt": "real football photo 1"},
            {"title": "สรุปสั้น", "line": recap[:90], "narration": body[:140], "image_prompt": "real football photo 2"},
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
    if not has_thai(board["hook"]) or not has_thai(board["scenes"][1]["line"]):
        return fallback_storyboard(item)
    return board


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
        thumb = ((page.get("thumbnail") or {}).get("source") or "").replace("/thumb/", "/")
        source = (page.get("thumbnail") or {}).get("source") or ""
        if source:
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
        "Bundesliga football stadium",
        "Premier League football match",
        "association football player",
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


if __name__ == "__main__":
    raise SystemExit(vd.main())
