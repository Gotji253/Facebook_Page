#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from PIL import Image

import ai_client
import video_draft as vd
from ai_client import chat_json
from football_poster import (
    NewsItem,
    http_get,
    search_openverse,
    search_reddit,
    search_rss_image,
    search_unsplash,
    search_wikimedia,
)
from hf_image import generate_hf_image

ai_client._hf_image = generate_hf_image
LOG = logging.getLogger("video_post")
THAI_RE = re.compile(r"[\u0E00-\u0E7F]")


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
    hook = "เกิดประเด็นร้อนในวงการลูกหนัง"
    body = "รายละเอียดอยู่ในข่าวต้นทาง ติดตามให้ครบก่อนแชร์"
    return finalize_storyboard(item, {
        "scenes": [
            {"title": "เกิดอะไรขึ้น", "line": hook, "narration": hook, "image_prompt": "real photo football news"},
            {"title": "สรุปสั้น", "line": body, "narration": body, "image_prompt": "real photo football recap"},
        ],
        "caption": hook,
        "hook": hook,
        "body": body,
        "cta": "แฟนบอลมองเรื่องนี้ยังไงครับ?",
        "hashtags": ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": "comedy",
    })


def finalize_storyboard(item, data: dict) -> dict:
    hook = thai_or(str(data.get("hook") or ""), "เกิดประเด็นร้อนในวงการลูกหนัง")[:80]
    body = thai_or(str(data.get("body") or ""), "รายละเอียดอยู่ในข่าวต้นทาง ติดตามให้ครบก่อนแชร์")[:400]
    recap = first_sentence(body, 90) or hook
    if recap == hook:
        recap = thai_or(str(data.get("cta") or ""), "สรุปสั้นจากข่าวต้นทาง อย่าแชร์ก่อนเช็ก")[:90]
    scenes = [
        {
            "title": "เกิดอะไรขึ้น",
            "line": hook[:90],
            "narration": hook[:140],
            "image_prompt": "real football photo scene 1",
        },
        {
            "title": "สรุปสั้น",
            "line": recap[:90],
            "narration": body[:140],
            "image_prompt": "real football photo scene 2",
        },
    ]
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    style = str(data.get("music_style", "comedy")).strip().lower()
    if style not in vd.MUSIC_STYLES:
        style = "comedy"
    caption = thai_or(str(data.get("caption") or ""), hook)[:500]
    return {
        "scenes": scenes,
        "caption": caption,
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


def _add_photo(found: list, seen: set, url: str, source: str, credit: str) -> None:
    key = re.sub(r"[?#].*$", "", str(url or "")).lower().rstrip("/")
    if not key or key in seen:
        return
    seen.add(key)
    found.append({"url": url, "source": source, "credit": credit})


def _search_with_title(item, title: str):
    alt = NewsItem(
        id=item.id,
        source=item.source,
        title=title,
        summary=item.summary,
        url=item.url,
        image_url=item.image_url,
        published=item.published,
    )
    for searcher in (search_wikimedia, search_unsplash, search_openverse, search_reddit):
        try:
            url, source, credit = searcher(alt)
            if url:
                yield url, source, credit
        except Exception as exc:
            LOG.warning("Photo search failed (%s / %s): %s", searcher.__name__, title[:40], exc)


def collect_real_photos(item) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for searcher in (search_rss_image, search_wikimedia, search_unsplash, search_openverse, search_reddit):
        try:
            url, source, credit = searcher(item)
            _add_photo(found, seen, url, source, credit)
        except Exception as exc:
            LOG.warning("Photo search failed (%s): %s", searcher.__name__, exc)
    words = re.findall(r"[A-Za-z0-9\-']+", item.title)
    queries = [
        " ".join(words[:3]),
        " ".join(words[-3:]) if len(words) >= 3 else "",
        f"{item.title} portrait",
        "football stadium matchday",
        "soccer press conference coach",
        "premier league football action",
    ]
    for query in queries:
        if len(found) >= 4 or not query.strip():
            continue
        for url, source, credit in _search_with_title(item, query.strip()[:180]):
            _add_photo(found, seen, url, source, credit)
            if len(found) >= 4:
                break
    LOG.info("Collected %d real photo candidates", len(found))
    return found


def download_photo(url: str, path: Path) -> None:
    response = http_get(url)
    path.write_bytes(response.content)
    with Image.open(path) as check:
        check.verify()
    with Image.open(path) as check:
        if check.size[0] < 400 or check.size[1] < 400:
            raise RuntimeError(f"Photo too small: {check.size} {url}")


def generate_scene_images(item, storyboard, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    photos = collect_real_photos(item)
    if item.image_url:
        photos = [{"url": item.image_url, "source": item.image_source or "RSS", "credit": item.image_credit}] + photos
    unique = []
    seen = set()
    for photo in photos:
        key = re.sub(r"[?#].*$", "", str(photo.get("url") or "")).lower().rstrip("/")
        if key and key not in seen:
            seen.add(key)
            unique.append(photo)
    results = []
    used_bytes = set()
    for index in range(vd.SCENE_COUNT):
        image_path = output_dir / f"scene_{index + 1:02d}.png"
        saved = False
        last_error = None
        for photo in unique:
            try:
                download_photo(photo["url"], image_path)
                data = image_path.read_bytes()
                if data in used_bytes:
                    continue
                used_bytes.add(data)
                unique.remove(photo)
                results.append({
                    "scene": index + 1,
                    "path": str(image_path),
                    "prompt": photo["url"],
                    "source": photo["source"],
                    "credit": photo.get("credit", ""),
                })
                saved = True
                LOG.info("Scene %s photo from %s", index + 1, photo["source"])
                break
            except Exception as exc:
                last_error = exc
                LOG.warning("Skip photo %s: %s", photo.get("url", "")[:80], exc)
        if not saved:
            raise RuntimeError(f"Need two different real photos; failed on scene {index + 1}: {last_error}")
    return results


vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images


if __name__ == "__main__":
    raise SystemExit(vd.main())
