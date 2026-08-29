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
    return {
        "scenes": [
            {"title": "ข่าวมาแล้ว", "line": item.title[:110], "narration": "มาดูข่าวฟุตบอลนี้แบบการ์ตูนล้อเลียนกันครับ", "image_prompt": "editorial football caricature, breaking news desk, no text, no logos, no watermark"},
            {"title": "ประเด็นสำคัญ", "line": "เรื่องนี้ทำให้แฟนบอลต้องหยิบเครื่องคิดเลขขึ้นมา", "narration": "รายละเอียดจริงอยู่ในข่าวต้นทาง ส่วนมุกนี้ทำเพื่อความบันเทิง", "image_prompt": "editorial football caricature, dramatic tactic board, no text, no logos, no watermark"},
            {"title": "มุมแฟนบอล", "line": "แฟนบอลบอกว่า ขอเวลาตั้งสติก่อนหนึ่งตลาดนักเตะ", "narration": "นี่คือการล้อเลียนแบบสุภาพ ไม่ใช่การกล่าวหาใคร", "image_prompt": "editorial football caricature, comic fans reacting, no text, no logos, no watermark"},
            {"title": "บทสรุป", "line": "ตรวจสอบข่าวต้นทางก่อนแชร์ แล้วพบกันคลิปหน้า", "narration": "ติดตามข่าวจริงและใช้วิจารณญาณก่อนแชร์ครับ", "image_prompt": "editorial football caricature, calm closing scene, no text, no logos, no watermark"},
        ],
        "caption": f"การ์ตูนล้อเลียนข่าวฟุตบอลเพื่อความบันเทิง: {item.title}",
    }


def generate_storyboard(item):
    vd.validate_news(item)
    request = {
        "title": item.title[:300],
        "summary": item.summary[:1400],
        "source": item.source,
        "source_url": item.url,
        "instruction": "ตอบเป็น JSON มี scenes 4 ฉากและ caption แต่ละฉากมี title line narration image_prompt",
    }
    try:
        data = chat_json(
            "คุณเป็นบรรณาธิการข่าวฟุตบอล ตอบ JSON เท่านั้น",
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
                    "title": str(scene.get("title", "ฉากข่าวฟุตบอล"))[:70],
                    "line": str(scene.get("line", "ติดตามข่าวฟุตบอลแบบเข้าใจง่าย"))[:110],
                    "narration": str(scene.get("narration", "นี่คือการ์ตูนล้อเลียนเพื่อความบันเทิง"))[:320],
                    "image_prompt": str(scene.get("image_prompt", "editorial football caricature, no text, no logos, no watermark"))[:1200],
                })
    if len(clean) != vd.SCENE_COUNT:
        return fallback_storyboard(item)
    return {"scenes": clean, "caption": str(data.get("caption", item.title))[:1800]}


def generate_scene_images(item, storyboard, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
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
        image_path.write_bytes(generate_image_bytes(prompt, vd.env("OPENAI_IMAGE_SIZE", "1024x1536")))
        with Image.open(image_path) as check:
            check.verify()
        results.append({"scene": index + 1, "path": str(image_path), "prompt": prompt, "source": "AI-generated"})
    return results


def make_image_prompt(item, scene, scene_index: int) -> str:
    extra = scene.get("image_prompt", "editorial football caricature, no text, no logos, no watermark") if isinstance(scene, dict) else "editorial football caricature, no text, no logos, no watermark"
    return (
        "Create a vertical 9:16 editorial cartoon illustration for a Thai football news parody video. "
        "Keep the main character in the upper two-thirds of the frame with clear headroom at the bottom for captions. "
        "Use fictionalized non-photorealistic footballer caricatures. No logos, readable text, or watermarks. "
        f"Article context: {item.title[:240]}. Scene {scene_index + 1}: {extra}"
    )


vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images
vd.make_image_prompt = make_image_prompt


if __name__ == "__main__":
    raise SystemExit(vd.main())
