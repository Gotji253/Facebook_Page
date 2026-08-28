#!/usr/bin/env python3
"""Create a review-only 45-second Thai football motion-comic draft."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from openai import OpenAI

from football_poster import DEFAULT_FEEDS, NewsItem, fetch_feed, find_related_image, http_get, load_state, required_env, save_state

LOG = logging.getLogger("video_draft")
W, H = 1080, 1920
FPS, DURATION = 30, 45


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


def wrap(draw, text: str, font, width: int) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    return lines + ([current] if current else []) or [text]


def cartoonize(image: Image.Image) -> Image.Image:
    image = ImageOps.fit(image.convert("RGB"), (W, H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.42))
    image = ImageEnhance.Color(image).enhance(1.35)
    image = ImageEnhance.Contrast(image).enhance(1.18)
    image = image.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.EDGE_ENHANCE)
    return ImageOps.posterize(image, 5)


def generate_joke(item: NewsItem) -> dict[str, object]:
    client = OpenAI()
    prompt = {
        "title": item.title[:300],
        "summary": item.summary[:1200],
        "instruction": "เขียนมุกล้อเลียนฟุตบอลแบบสุภาพ ไม่ใส่ข่าวปลอม ไม่กล่าวหาบุคคล และไม่ใช้คำหยาบ สำหรับ motion comic ภาษาไทย 45 วินาที แบ่งเป็น 3 ฉาก ฉากละประมาณ 15 วินาที โดยต้องตอบกลับเป็น valid JSON เท่านั้น มีคีย์ scenes และ caption",
    }
    response = client.chat.completions.create(
        model=env("OPENAI_MODEL", "gpt-5-mini"),
        messages=[
            {"role": "system", "content": "คุณเป็นนักเขียนมุกข่าวฟุตบอลภาษาไทย ใช้ข้อเท็จจริงจากข่าวเป็นฐาน และระบุชัดเจนว่าเป็นการล้อเลียน"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "{}")
    scenes = data.get("scenes")
    if isinstance(scenes, dict):
        scenes = list(scenes.values())
    clean_scenes = []
    if isinstance(scenes, list):
        for scene in scenes[:3]:
            if isinstance(scene, dict):
                clean_scenes.append({
                    "title": str(scene.get("title", "ฉากข่าวฟุตบอล"))[:80],
                    "line": str(scene.get("line", "มุกฟุตบอลประจำวันนี้"))[:180],
                    "narration": str(scene.get("narration", "ติดตามข่าวฟุตบอลแบบขำ ๆ กันครับ"))[:300],
                })
    if len(clean_scenes) != 3:
        LOG.warning("AI returned invalid scenes; using safe deterministic joke fallback")
        clean_scenes = [
            {"title": "ข่าวมาแล้ว", "line": f"{item.title[:80]}", "narration": "ข่าวนี้จริงจัง แต่ขอเล่าแบบขำ ๆ นะครับ"},
            {"title": "มุมแฟนบอล", "line": "ตลาดนักเตะทำเอาแฟนบอลต้องเปิดเครื่องคิดเลข", "narration": "ค่าตัวขยับที กระเป๋าสตางค์แฟนบอลก็สั่นตาม"},
            {"title": "บทสรุป", "line": "ติดตามตอนต่อไป ก่อนข่าวใหม่จะมาแซงคิว", "narration": "นี่คือการ์ตูนล้อเลียนจากข่าว โปรดตรวจสอบข่าวต้นทางก่อนแชร์"},
        ]
    caption = str(data.get("caption", f"มุกฟุตบอลประจำวัน: {item.title}"))[:1800]
    return {"scenes": clean_scenes, "caption": caption}


def draw_scene(base: Image.Image, scene: dict[str, str], scene_index: int, font_path: str) -> Image.Image:
    frame = base.copy().convert("RGBA")
    # Add gentle zoom/pan variation to make still artwork feel animated.
    zoom = 1.0 + scene_index * 0.035
    crop_w, crop_h = int(W / zoom), int(H / zoom)
    left = max(0, min(W - crop_w, int(scene_index * 26)))
    top = max(0, min(H - crop_h, int(scene_index * 18)))
    frame = frame.crop((left, top, left + crop_w, top + crop_h)).resize((W, H), Image.Resampling.LANCZOS)
    frame = Image.blend(frame, Image.new("RGBA", (W, H), (10, 25, 60, 80)), 0.28)
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 0, W, 165), fill=(7, 18, 45, 225))
    draw.rectangle((0, H - 620, W, H), fill=(7, 18, 45, 215))
    label_font = load_font(font_path, 42)
    title_font = load_font(font_path, 66)
    line_font = load_font(font_path, 52)
    draw.text((58, 42), f"รอบรู้ : INSIGHT  |  ฉาก {scene_index + 1}/3", font=label_font, fill=(255, 214, 88))
    title_lines = wrap(draw, scene["title"], title_font, 930)
    y = 225
    for line in title_lines[:2]:
        box = draw.textbbox((0, 0), line, font=title_font); x = (W - (box[2] - box[0])) // 2
        draw.text((x + 3, y + 4), line, font=title_font, fill=(0, 0, 0, 210), stroke_width=2, stroke_fill=(0, 0, 0, 210))
        draw.text((x, y), line, font=title_font, fill="white", stroke_width=1, stroke_fill=(0, 0, 0, 230))
        y += 82
    line_lines = wrap(draw, scene["line"], line_font, 900)
    y = H - 520
    for line in line_lines[:4]:
        box = draw.textbbox((0, 0), line, font=line_font); x = (W - (box[2] - box[0])) // 2
        draw.text((x + 3, y + 4), line, font=line_font, fill=(0, 0, 0, 220), stroke_width=2, stroke_fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=line_font, fill=(255, 240, 160), stroke_width=1, stroke_fill=(0, 0, 0, 230))
        y += 68
    draw.text((58, H - 85), "การ์ตูนล้อเลียนข่าวฟุตบอล | ตรวจสอบข่าวต้นทางก่อนแชร์", font=load_font(font_path, 28), fill=(220, 230, 245))
    return frame.convert("RGB")


def render_video(image_url: str, joke: dict[str, object], output: Path, font_path: str) -> None:
    source = Image.open(http_get(image_url, stream=True).raw).convert("RGB")
    base = cartoonize(source)
    scenes = joke["scenes"]
    with tempfile.TemporaryDirectory(prefix="football-video-") as tmp:
        frames = Path(tmp)
        for frame_no in range(FPS * DURATION):
            scene_index = min(2, frame_no // (FPS * 15))
            scene = scenes[scene_index]
            rendered = draw_scene(base, scene, scene_index, font_path)
            rendered.save(frames / f"frame_{frame_no:05d}.jpg", quality=90)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frames / "frame_%05d.jpg"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    logging.basicConfig(level=getattr(logging, env("LOG_LEVEL", "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    output_dir = Path(env("VIDEO_DRAFT_DIR", "video_drafts"))
    state_path = Path(env("VIDEO_STATE_FILE", "video_draft_state.json"))
    state = load_state(state_path)
    drafted = set(state.get("drafted_ids", []))
    feed_env = {"BBC Sport": "RSS_BBC_URL", "ESPN": "RSS_ESPN_URL", "The Guardian": "RSS_GUARDIAN_URL", "FourFourTwo": "RSS_FOURFOURTWO_URL"}
    feeds = {name: env(feed_env[name], url) for name, url in DEFAULT_FEEDS.items()}
    items = [item for source, url in feeds.items() for item in fetch_feed(source, url) if item.id not in drafted and item.image_url]
    if not items:
        LOG.info("No new news with RSS images available for video draft")
        return 0
    item = items[0]
    item.image_url, item.image_source, item.image_credit = find_related_image(item)
    if not item.image_url:
        raise RuntimeError("No real news image found; video draft was skipped")
    joke = generate_joke(item)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    video_path = output_dir / f"football_motion_comic_{stamp}.mp4"
    render_video(item.image_url, joke, video_path, required_env("FONT_PATH"))
    draft = {
        "created_at": stamp,
        "duration_seconds": DURATION,
        "format": "vertical 1080x1920 MP4",
        "status": "draft_pending_review",
        "item": asdict(item),
        "joke": joke,
        "video": str(video_path),
        "review_notes": "ตรวจชื่อบุคคล ตัวเลข ความเหมาะสมของมุก และสิทธิ์การใช้ภาพก่อนเผยแพร่",
    }
    metadata_path = video_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    state.setdefault("drafted_ids", []).append(item.id)
    save_state(state_path, state)
    LOG.info("Created video draft: %s", video_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
