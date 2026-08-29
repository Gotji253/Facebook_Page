#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image

import ai_client
import video_draft as vd
from ai_client import chat_json, generate_image_bytes
from hf_image import generate_hf_image

ai_client._hf_image = generate_hf_image
LOG = logging.getLogger("video_post")


def fallback_storyboard(item):
    title = item.title[:90]
    point = (item.summary or title).strip()[:110]
    return {
        "scenes": [
            {
                "title": "เกิดอะไรขึ้น",
                "line": title,
                "narration": title,
                "image_prompt": "editorial football caricature, breaking news moment, no text, no logos, no watermark",
            },
            {
                "title": "สรุปสั้น",
                "line": point,
                "narration": point,
                "image_prompt": "editorial football caricature, simple recap scene, no text, no logos, no watermark",
            },
        ],
        "caption": title,
        "hook": title[:40],
        "body": point,
        "cta": "แฟนบอลมองเรื่องนี้ยังไงครับ?",
        "hashtags": ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": "comedy",
    }


def generate_storyboard(item):
    vd.validate_news(item)
    request = {
        "title": item.title[:180],
        "summary": item.summary[:280],
        "source": item.source,
        "instruction": (
            "ตอบ JSON สั้นมาก มี scenes 2 ฉาก, caption, hook, body, cta, hashtags, music_style. "
            "ฉาก 1 สรุปว่าเกิดอะไรขึ้น ฉาก 2 สรุปประเด็นที่ต้องรู้ ภาษาไทยเข้าใจง่าย ไม่เกิน 18 คำต่อบรรทัด. "
            "music_style ต้องเป็น hype, triumph, tense, comedy หรือ calm. "
            "image_prompt ภาษาอังกฤษสั้น ไม่มีตัวอักษรในภาพ"
        ),
    }
    try:
        data = chat_json(
            "บรรณาธิการข่าวฟุตบอล ตอบ JSON สั้นเท่านั้น",
            json.dumps(request, ensure_ascii=False),
        )
    except Exception as exc:
        LOG.warning("Storyboard AI failed; using fallback: %s", exc)
        return fallback_storyboard(item)
    scenes = data.get("scenes")
    if isinstance(scenes, dict):
        scenes = list(scenes.values())
    clean = []
    if isinstance(scenes, list):
        for scene in scenes[: vd.SCENE_COUNT]:
            if isinstance(scene, dict):
                clean.append({
                    "title": str(scene.get("title", "สรุปข่าว"))[:40],
                    "line": str(scene.get("line", item.title))[:90],
                    "narration": str(scene.get("narration", scene.get("line", item.title)))[:140],
                    "image_prompt": str(scene.get("image_prompt", "editorial football caricature, no text, no logos"))[:280],
                })
    if len(clean) != vd.SCENE_COUNT:
        return fallback_storyboard(item)
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    style = str(data.get("music_style", "comedy")).strip().lower()
    if style not in vd.MUSIC_STYLES:
        style = "comedy"
    return {
        "scenes": clean,
        "caption": str(data.get("caption") or data.get("hook") or item.title)[:500],
        "hook": str(data.get("hook") or clean[0]["line"])[:80],
        "body": str(data.get("body") or clean[1]["line"])[:400],
        "cta": str(data.get("cta") or "แฟนบอลมองเรื่องนี้ยังไงครับ?")[:120],
        "hashtags": [str(tag).strip()[:40] for tag in tags if str(tag).strip()][:5] or ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": style,
    }


def generate_scene_images(item, storyboard, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    last_bytes = None
    probe = Path("hf_test.png")
    if probe.exists():
        last_bytes = probe.read_bytes()
    for index, scene in enumerate(storyboard["scenes"]):
        if not isinstance(scene, dict):
            scene = {}
        prompt = vd.make_image_prompt(item, {
            "title": str(scene.get("title", "")),
            "line": str(scene.get("line", "")),
            "image_prompt": str(scene.get("image_prompt", "editorial football caricature, no text, no logos, no watermark")),
        }, index)
        LOG.info("Generating AI cartoon image %d/%d", index + 1, vd.SCENE_COUNT)
        image_path = output_dir / f"scene_{index + 1:02d}.png"
        try:
            data = generate_image_bytes(prompt, vd.env("OPENAI_IMAGE_SIZE", "768x1344"))
            last_bytes = data
            source = "AI-generated"
        except Exception as exc:
            if not last_bytes:
                raise
            LOG.warning("Image generation failed on scene %s; reusing last frame: %s", index + 1, exc)
            data = last_bytes
            source = "reused-last-frame"
        image_path.write_bytes(data)
        with Image.open(image_path) as check:
            check.verify()
        results.append({"scene": index + 1, "path": str(image_path), "prompt": prompt, "source": source})
    return results


def make_image_prompt(item, scene, scene_index: int) -> str:
    extra = scene.get("image_prompt", "editorial football caricature, no text") if isinstance(scene, dict) else "editorial football caricature, no text"
    return (
        "Vertical 9:16 editorial football caricature, no text, no logos, no watermark. "
        f"News: {item.title[:120]}. Scene {scene_index + 1}: {extra[:220]}"
    )


vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images
vd.make_image_prompt = make_image_prompt


if __name__ == "__main__":
    raise SystemExit(vd.main())
