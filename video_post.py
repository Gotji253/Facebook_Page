#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations

import json
import logging
import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

import ai_client
import video_draft as vd
from ai_client import chat_json
from football_poster import http_get, search_rss_image
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


def complete_phrase(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        cleaned = text
    else:
        cut = text[:limit].rstrip()
        cleaned = cut.rsplit(" ", 1)[0] if " " in cut else cut
    cleaned = cleaned.rstrip(" ,;:-/")
    while cleaned and THAI_MARK.match(cleaned[-1]):
        cleaned = cleaned[:-1].rstrip()
    return cleaned


def first_sentence(text: str, limit: int = 56) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[ฮ.!\n])\s+", text, maxsplit=1)
    return complete_phrase(parts[0].strip(), limit)


def thai_or(text: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if has_thai(text) else fallback


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
    if THAI_MARK.match(text[-1]):
        return True
    if text.endswith(("และ", "ที่", "ของ", "ใน", "จะ", "ได้", "ไม่", "กับ", "จาก", "เพื่อ", "ส่วน")):
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
    skip_names = {"The", "And", "For", "With", "From", "This", "That", "After", "Before", "Against", "Premier", "League", "United", "City", "News", "Sport", "Football", "Saturday"}
    names = [name for name in NAME_RE.findall(getattr(item, "title", "") or "") if name.split()[0] not in skip_names]
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


def finalize_storyboard(item, data: dict, limit: int = 56) -> dict:
    hook = complete_phrase(thai_or(str(data.get("hook") or ""), "เกิดประเด็นร้อนในวงการลูกหนัง"), limit)
    raw_body = thai_or(str(data.get("body") or ""), hook)
    body = one_story(raw_body) or hook
    recap = first_sentence(body, min(limit, 36)) or complete_phrase(hook, 36)
    if not same_story("เกิดอะไรขึ้น " + hook, recap):
        recap = complete_phrase(hook, 36)
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    style = str(data.get("music_style", "comedy")).strip().lower()
    if style not in vd.MUSIC_STYLES:
        style = "comedy"
    return {
        "scenes": [
            {"title": "เกิดอะไรขึ้น", "line": hook, "narration": hook[:140], "image_prompt": "real football photo 1"},
            {"title": recap, "line": "", "narration": body[:140], "image_prompt": "real football photo 2"},
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
            "hook ไม่เกิน 12 คำ body 1 ประโยคสั้นของเรื่องเดียวกัน. "
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
    board = finalize_storyboard(item, data, 42)
    if not has_thai(board["hook"]) or not has_thai(board["scenes"][1]["title"]):
        board = fallback_storyboard(item)
        data = board.get("_source_data") or data
    review = review_on_screen_text(item, board)
    if not review["ok"]:
        LOG.warning("Clip text failed first review, rewriting same-story recap: %s", review["errors"])
        data = dict(data or board.get("_source_data") or {})
        data["body"] = data.get("hook") or board.get("hook")
        board = finalize_storyboard(item, data, 42)
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


def _photo_key(url: str) -> str:
    return re.sub(r"[?#].*$", "", str(url or "")).lower().rstrip("/")


def _looks_like_photo(url: str) -> bool:
    lowered = url.lower()
    if any(host in lowered for host in ("pollinations.ai", "openverse.org", "wikimedia.org", "bbci.co.uk")):
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
    for page in pages.values():
        source = (page.get("thumbnail") or {}).get("source") or ""
        title = str(page.get("title") or "").lower()
        if source and any(word in title for word in ("football", "soccer", "f.c", "fc ", "club", "stadium")):
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
    if len(found) < 4:
        for query in ("football match", "soccer stadium", "premier league trophy"):
            try:
                for url, source, credit in search_openverse(query):
                    _add_photo(found, seen, url, source, credit)
            except Exception as exc:
                LOG.warning("Openverse search failed (%s): %s", query, exc)
    _add_photo(
        found, seen,
        "https://image.pollinations.ai/prompt/real%20photo%20football%20match%20stadium?width=1080&height=1920&nologo=true",
        "Pollinations", "football match",
    )
    _add_photo(
        found, seen,
        "https://image.pollinations.ai/prompt/real%20photo%20football%20player%20celebration?width=1080&height=1920&nologo=true",
        "Pollinations", "football player",
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
