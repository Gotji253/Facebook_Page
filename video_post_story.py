from __future__ import annotations
import json
import logging
import re
import video_draft as vd
from ai_client import chat_json
from video_post_text import *

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
    if copied_scene(s1, s2):
        errors.append("ฉาก 2 คัดลอกข้อความฉาก 1")
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
    if copied_scene(hook, recap) or len(text_key(recap)) < 18:
        rest = body
        if hook and hook in body:
            rest = body.split(hook, 1)[-1].strip()
        if rest and not copied_scene(hook, rest) and has_thai(rest):
            recap = readable_thai(finish_phrase(complete_phrase(rest, 140), rest, 140))
        else:
            recap = source_detail(item)
    if copied_scene(hook, recap):
        recap = source_detail(item)
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
            "body ต้องเป็นรายละเอียดต่อจาก hook คนละประโยค ห้ามคัดลอก hook. "
            "body ใส่ชื่อนักเตะ การย้ายทีม สาเหตุ หรือสกอร์ที่ยังไม่มีใน hook. "
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
        LOG.warning("Clip text failed first review, rewriting extra-detail recap: %s", review["errors"])
        data = dict(data or board.get("_source_data") or {})
        data["body"] = source_detail(item)
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
